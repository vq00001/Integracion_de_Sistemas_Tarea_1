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
- Es fácil de exponer también sobre HTTPS a un tercero externo si en el
  futuro "pública" pasa a significar "expuesta a internet", sin cambiar
  el contrato de la API (sigue siendo un header).

Trade-off reconocido: no captura identidad de usuario final ni scopes
granulares. Si más adelante se necesita distinguir "qué usuario humano
matriculó", la evolución natural es JWT (posiblemente emitido por un
Authorization Server central de AprendeMás) *encima* de este mecanismo,
sin romper compatibilidad, dado que el mismo header Authorization puede
convivir con X-API-Key para clientes de servicio.
"""
from __future__ import annotations

from fastapi import Header
from starlette import status
from starlette.exceptions import HTTPException

from .config import settings


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta la cabecera X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if x_api_key not in settings.valid_api_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key inválida",
        )
    return x_api_key
