"""
Autenticación y autorización por API Key (header `X-API-Key`).

Por qué API Key y no OAuth2/JWT ni Basic Auth para esta API:

- Los consumidores de esta API REST son *sistemas cliente* de confianza dentro de
  AprendeMás (front-end de secretaría, un futuro portal), no usuarios anónimos de
  internet. El modelo es máquina-a-máquina, igual que la relación entre esta API y
  el gRPC de Cupos: no hace falta un flujo de login interactivo, refresh tokens ni
  sesiones que justifiquen la complejidad de OAuth2/JWT.
- Es stateless y barata de verificar en cada request.
- Basic Auth se descartó porque sugiere una identidad humana (usuario/contraseña)
  que esta API no gestiona.

Dos tipos de credencial (ambas en `X-API-Key`):

1. ADMIN (`API_KEYS`, variable de entorno): acceso total. Es la clave de la
   secretaría / integraciones internas y la única que puede crear estudiantes,
   listarlos y emitir o revocar keys de estudiante. NO existe un valor por defecto: si
   `API_KEYS` falta, es una clave pública conocida o tiene menos de 24 caracteres, la API
   se niega a arrancar (`config.validar_claves_admin`).

2. ESTUDIANTE (tabla `api_keys`): se emite a demanda, una activa por estudiante, y
   solo permite operar sobre los recursos de ese estudiante (su ficha y sus
   matrículas). Se guarda solo el hash SHA-256; el valor en claro se entrega una
   única vez al emitirla. Caduca a los `STUDENT_KEY_TTL_DAYS` días (90 por defecto).

Defensa contra abuso (ver ratelimit.py): los intentos de autenticación fallidos se limitan por
IP (429 + Retry-After) y las escrituras de un estudiante con clave propia, por minuto.

Semántica de los códigos (RFC 9110):
- 401 Unauthorized: la credencial falta o NO autentica (inexistente, inválida o
  revocada). El servidor no sabe quién es el cliente.
- 403 Forbidden: el cliente está autenticado pero no tiene permiso para esa
  operación o ese recurso (p. ej. un estudiante intentando ver a otro).
- Para matrículas ajenas identificadas por id se responde 404 y no 403, para no
  confirmar a un estudiante qué ids de matrícula existen.

Trade-off reconocido: sigue sin existir identidad de usuario final con login ni
scopes por operación: el permiso es "admin" o "dueño de este estudiante". Si más
adelante se necesita distinguir al usuario humano, la evolución natural es JWT
encima de este mecanismo (ver docs/adr/D5).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette import status
from starlette.exceptions import HTTPException

from . import models
from .config import settings
from .database import get_db
from .errors import TooManyRequestsError
from .ratelimit import LimitadorVentana
from .time import utc_now

log = logging.getLogger("matriculas.security")

# Prefijo legible: permite reconocer una clave de estudiante (y detectar fugas con
# escáneres de secretos). Formato: mat_<id público de 8 hex>.<secreto>. El id público es
# ALEATORIO e independiente del secreto: mostrarlo en listados/logs no revela ni un carácter
# de él (antes se mostraban los primeros 4 caracteres del propio secreto).
STUDENT_KEY_PREFIX = "mat_"

_limitador_fallos = LimitadorVentana(settings.auth_fail_limit, settings.auth_fail_window_seconds)
_limitador_escrituras = LimitadorVentana(settings.write_rate_limit_per_minute, 60.0)


@dataclass(frozen=True)
class Principal:
    """Quién hace la solicitud, ya autenticado."""

    es_admin: bool
    student_id: str | None = None  # solo para claves de estudiante


# ---------------------------------------------------------------------------
# Claves de estudiante
# ---------------------------------------------------------------------------

def generar_api_key() -> tuple[str, str]:
    """Devuelve (clave_completa, prefijo_publico). Secreto aleatorio de 256 bits."""
    prefijo = f"{STUDENT_KEY_PREFIX}{secrets.token_hex(4)}"
    return f"{prefijo}.{secrets.token_urlsafe(32)}", prefijo


def hash_api_key(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------

def _es_admin(candidata: str) -> str | None:
    """Devuelve la clave admin que coincide (o None). Compara contra todas en
    tiempo constante para no filtrar información por diferencias de tiempo."""
    coincidencia = None
    for valida in settings.admin_api_keys:
        if hmac.compare_digest(candidata.encode(), valida.encode()):
            coincidencia = valida
    return coincidencia


def _no_autenticado(detalle: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detalle,
        headers={"WWW-Authenticate": "ApiKey"},
    )


def _ip(request: Request) -> str:
    return request.client.host if request.client else "desconocido"


def _fallo_de_autenticacion(request: Request, detalle: str) -> HTTPException:
    _limitador_fallos.registrar(_ip(request))
    return _no_autenticado(detalle)


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Principal:
    """Autentica la solicitud y devuelve quién es. 401 si no autentica; 429 si la IP agotó
    sus intentos fallidos (se rechaza ANTES de comparar la clave, para no regalar un oráculo)."""
    espera = _limitador_fallos.excedido(_ip(request))
    if espera is not None:
        raise TooManyRequestsError(
            "Demasiados intentos de autenticación fallidos. Espera antes de reintentar.",
            retry_after=espera,
        )
    if not x_api_key:
        raise _fallo_de_autenticacion(request, "Falta la cabecera X-API-Key")

    if _es_admin(x_api_key) is not None:
        return Principal(es_admin=True)

    fila = db.execute(
        select(models.ApiKey.student_id, models.ApiKey.expires_at).where(
            models.ApiKey.key_hash == hash_api_key(x_api_key),
            models.ApiKey.revoked_at.is_(None),
        )
    ).first()
    if fila is None:
        raise _fallo_de_autenticacion(request, "API key inválida")
    student_id, expires_at = fila
    if expires_at is not None and expires_at <= utc_now():
        raise _fallo_de_autenticacion(request, "API key vencida: solicita una nueva al administrador")
    return Principal(es_admin=False, student_id=student_id)


def limitar_escrituras(principal: Principal) -> None:
    """429 si un estudiante con clave propia supera sus escrituras por minuto."""
    if principal.es_admin:
        return
    espera = _limitador_escrituras.consumir(principal.student_id or "")
    if espera is not None:
        raise TooManyRequestsError(
            "Demasiadas operaciones en poco tiempo. Espera antes de reintentar.", retry_after=espera
        )


# ---------------------------------------------------------------------------
# Autorización
# ---------------------------------------------------------------------------

def _prohibido(detalle: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detalle)


def require_admin(principal: Principal = Depends(require_api_key)) -> Principal:
    if not principal.es_admin:
        raise _prohibido("Esta operación requiere una API key de administrador")
    return principal


def exigir_acceso_a_estudiante(principal: Principal, student_id: str) -> None:
    """Un admin accede a cualquiera; un estudiante solo a sí mismo (403 si no)."""
    if not principal.es_admin and principal.student_id != student_id:
        raise _prohibido("Tu API key solo permite acceder a tus propios datos")
