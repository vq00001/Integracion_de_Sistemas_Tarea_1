"""
Autenticación por API Key (header `X-API-Key`).

Por qué API Key y no OAuth2/JWT ni Basic Auth para esta API:

- Los consumidores de esta API REST no son usuarios finales anónimos de
  internet, sino *sistemas cliente* de confianza dentro de AprendeMás
  (front-end de matrícula usado por secretaría académica, integraciones
  internas futuras). El modelo de confianza es máquina-a-máquina, igual
  que la relación entre esta misma API y el gRPC de Cupos: no hay un
  flujo de login interactivo de un estudiante que necesite pedir permiso
  (scopes), refresh tokens, ni sesiones de usuario que justifiquen la
  complejidad de OAuth2/JWT.
- Es stateless y barata de verificar en cada request (no requiere
  validar firmas, ni consultar un Authorization Server), lo cual importa
  porque el endpoint de matrícula ya depende de una llamada gRPC externa
  con su propio costo/latencia.
- Permite emitir/revocar credenciales por sistema cliente de forma
  simple (una key por integración), suficiente para trazabilidad y para
  cortar acceso a un consumidor problemático sin afectar a los demás.
- Basic Auth se descartó porque obliga a manejar un par usuario/contraseña
  que sugiere una identidad humana que esta API no gestiona.

Semántica de los códigos (RFC 9110):
- 401 Unauthorized: la credencial falta o NO autentica (clave inexistente o
  inválida). Es la respuesta correcta para una API key incorrecta: el
  servidor no sabe quién es el cliente.
- 403 Forbidden se reserva para un cliente ya autenticado que no tiene
  permiso para la operación. Hoy no existen roles, por lo que esta API no
  responde 403.

Trade-off reconocido: no captura identidad de usuario final ni scopes
granulares. Si más adelante se necesita distinguir "qué usuario humano
matriculó", la evolución natural es JWT (posiblemente emitido por un
Authorization Server central de AprendeMás) *encima* de este mecanismo,
sin romper compatibilidad, dado que el mismo header Authorization puede
convivir con X-API-Key para clientes de servicio.
"""
from __future__ import annotations

import hmac

from fastapi import Header
from starlette import status
from starlette.exceptions import HTTPException

from .config import settings


def _clave_valida(candidata: str) -> bool:
    """Compara contra todas las claves en tiempo constante (evita filtrar
    información por diferencias de tiempo entre una clave casi correcta y otra
    totalmente errónea)."""
    resultado = False
    for valida in settings.valid_api_keys:
        resultado |= hmac.compare_digest(candidata.encode(), valida.encode())
    return resultado


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta la cabecera X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not _clave_valida(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return x_api_key
