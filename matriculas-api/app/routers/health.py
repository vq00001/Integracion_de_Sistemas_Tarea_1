from __future__ import annotations

from fastapi import APIRouter

from .. import grpc_client

router = APIRouter(tags=["Salud"])


@router.get(
    "/v1/health",
    summary="Estado del servicio y de su dependencia con Cupos",
    description=(
        "No requiere autenticación (usado por orquestadores/health checks). "
        "Informa el estado del circuit breaker hacia Cupos para observabilidad: "
        "'CLOSED' operando normal, 'OPEN' fallando rápido porque Cupos no responde, "
        "'HALF_OPEN' probando si Cupos ya se recuperó."
    ),
)
def health():
    return {
        "status": "ok",
        "dependencies": {
            "cupos_grpc": {
                "circuit_state": grpc_client._breaker.state.value,
            }
        },
    }
