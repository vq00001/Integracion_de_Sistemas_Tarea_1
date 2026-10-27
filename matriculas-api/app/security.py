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
   listarlos y emitir o revocar keys de estudiante. Si el valor configurado es el
   predeterminado público (`DEFAULT_ADMIN_API_KEY`) se deja un WARNING en el log al
   arrancar y en CADA request que la use.

2. ESTUDIANTE (tabla `api_keys`): se emite a demanda, una activa por estudiante, y
   solo permite operar sobre los recursos de ese estudiante (su ficha y sus
   matrículas). Se guarda solo el hash SHA-256; el valor en claro se entrega una
   única vez al emitirla.

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
from .config import DEFAULT_ADMIN_API_KEY, settings
from .database import get_db

log = logging.getLogger("matriculas.security")

# Prefijo legible: permite reconocer una clave de estudiante (y detectar fugas con
# escáneres de secretos) sin que el prefijo revele nada del secreto.
STUDENT_KEY_PREFIX = "mat_"


@dataclass(frozen=True)
class Principal:
    """Quién hace la solicitud, ya autenticado."""

    es_admin: bool
    student_id: str | None = None  # solo para claves de estudiante


# ---------------------------------------------------------------------------
# Claves de estudiante
# ---------------------------------------------------------------------------

def generar_api_key() -> str:
    """Token aleatorio de 256 bits (mat_<urlsafe>)."""
    return STUDENT_KEY_PREFIX + secrets.token_urlsafe(32)


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


def advertir_clave_por_defecto() -> None:
    """Aviso al arrancar si la clave admin configurada es la predeterminada."""
    if DEFAULT_ADMIN_API_KEY in settings.admin_api_keys:
        log.warning(
            "SEGURIDAD: la API key admin es el valor PREDETERMINADO público del repositorio. "
            "Cualquiera con acceso al código puede usarla. Define API_KEYS con un valor "
            "propio (python -c \"import secrets; print(secrets.token_urlsafe(32))\")."
        )


def _log_uso_clave_por_defecto(request: Request) -> None:
    cliente = request.client.host if request.client else "desconocido"
    log.warning(
        "SEGURIDAD: se autenticó con la API key admin PREDETERMINADA (%s %s, cliente=%s). "
        "Reemplázala por una clave propia en API_KEYS.",
        request.method, request.url.path, cliente,
    )


def _no_autenticado(detalle: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detalle,
        headers={"WWW-Authenticate": "ApiKey"},
    )


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Principal:
    """Autentica la solicitud y devuelve quién es. Lanza 401 si no autentica."""
    if not x_api_key:
        raise _no_autenticado("Falta la cabecera X-API-Key")

    admin = _es_admin(x_api_key)
    if admin is not None:
        if admin == DEFAULT_ADMIN_API_KEY:
            _log_uso_clave_por_defecto(request)
        return Principal(es_admin=True)

    fila = db.execute(
        select(models.ApiKey.student_id).where(
            models.ApiKey.key_hash == hash_api_key(x_api_key),
            models.ApiKey.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if fila is None:
        raise _no_autenticado("API key inválida")
    return Principal(es_admin=False, student_id=fila)


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
