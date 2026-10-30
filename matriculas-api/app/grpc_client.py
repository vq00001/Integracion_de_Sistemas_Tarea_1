"""
Cliente hacia el servicio gRPC de Cupos.

Estrategias de resiliencia aplicadas (requisito T7: "la API debe comportarse
de forma razonable cuando el sistema gRPC esté caído o lento"):

1. Timeout (deadline) por llamada (`cupos_grpc_timeout_seconds`): una
   matrícula nunca espera indefinidamente a Cupos.
2. Circuit breaker: tras varias fallas consecutivas se deja de intentar por un
   tiempo y se falla rápido en vez de acumular llamadas colgadas.
3. Fail-closed: si no se puede confirmar con Cupos que hay cupos, la matrícula
   NO queda CONFIRMADA.
4. Idempotencia: ocuparCupo/liberarCupo llevan una clave; reintentar con la
   misma clave no ocupa ni libera dos veces.

Traducción de errores gRPC → HTTP (justificada en docs/adr/D4):

  DEADLINE_EXCEEDED            → 504 CUPOS_TIMEOUT        (resultado INCIERTO)
  UNAVAILABLE, no se pudo conectar → 503 CUPOS_NO_DISPONIBLE  (nunca se envió: NO es incierto)
  UNAVAILABLE, conexión rota a mitad → 503 CUPOS_NO_DISPONIBLE (pudo llegar: SÍ es incierto)
  circuito abierto             → 503 CUPOS_NO_DISPONIBLE  (nunca se envió)
  UNKNOWN / INTERNAL           → 502 CUPOS_RESPUESTA_INVALIDA (se trata como incierto: ver D4)
  NOT_FOUND                    → 404 CURSO_NO_ENCONTRADO  (error de dominio)
  FAILED_PRECONDITION          → 409                      (error de dominio)

Qué cuenta como falla para el circuit breaker: solo los problemas de
*disponibilidad* (timeout, UNAVAILABLE, RESOURCE_EXHAUSTED). Los errores de
dominio y los errores internos del servidor (UNKNOWN/INTERNAL) significan que
Cupos SÍ respondió, así que no abren el circuito: antes, un choque de claves
de idempotencia dentro de Cupos llegaba como UNKNOWN y sumaba fallas aunque el
servicio estuviera sano.
"""
from __future__ import annotations

import logging

import grpc

from .circuit_breaker import CircuitBreaker
from .config import settings
from .cursos_cache import cache_cursos
from .errors import (
    BadGatewayError,
    ConflictError,
    CursoNoEncontradoError,
    DependencyTimeoutError,
    DependencyUnavailableError,
    SinCuposError,
)
from .grpc_stubs import cupos_pb2, cupos_pb2_grpc

log = logging.getLogger("matriculas.grpc_client")

_breaker = CircuitBreaker(
    failure_threshold=settings.cupos_cb_failure_threshold,
    recovery_seconds=settings.cupos_cb_recovery_seconds,
)

# Fallas de disponibilidad: las únicas que suman al circuit breaker.
_FALLAS_DE_DISPONIBILIDAD = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
}


# Textos con los que gRPC informa que NUNCA logró conectarse (la solicitud no salió del cliente).
# Cualquier otro UNAVAILABLE (socket cerrado, GOAWAY, reset) pudo ocurrir con la solicitud ya
# enviada, así que se trata como resultado incierto: ante la duda, la opción conservadora.
_SEÑALES_NO_ENVIADO = (
    "failed to connect to all addresses",
    "connection refused",
    "dns resolution failed",
    "name resolution",
    "no route to host",
    "unable to resolve",
)


def _no_se_envio(exc: grpc.RpcError) -> bool:
    detalle = (exc.details() or "").lower() if hasattr(exc, "details") else ""
    return any(señal in detalle for señal in _SEÑALES_NO_ENVIADO)


class CuposClient:
    def __init__(self) -> None:
        # Canal reutilizado entre requests (gRPC recomienda no crear uno
        # por llamada); gRPC maneja la reconexión de forma transparente.
        self._channel = grpc.insecure_channel(settings.cupos_grpc_target)
        self._stub = cupos_pb2_grpc.CuposStub(self._channel)

    def close(self) -> None:
        self._channel.close()

    def _call(self, fn_name: str, request, *, curso_id: int):
        if not _breaker.allow_request():
            log.warning("circuito abierto hacia Cupos: se rechaza sin intentar la llamada")
            raise DependencyUnavailableError(
                "El sistema de Cupos no está disponible en este momento "
                "(circuito abierto tras fallas repetidas). Intenta de nuevo en unos segundos.",
                enviado=False,  # ni siquiera se intentó: Cupos no pudo retener nada
            )
        method = getattr(self._stub, fn_name)
        try:
            response = method(request, timeout=settings.cupos_grpc_timeout_seconds)
        except grpc.RpcError as exc:
            code = exc.code() if hasattr(exc, "code") else None

            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                _breaker.record_failure()
                log.error("timeout llamando a Cupos.%s", fn_name)
                raise DependencyTimeoutError(
                    "Cupos no respondió dentro del plazo. El resultado es incierto: "
                    "el cupo pudo haberse ocupado. Reintenta con la misma Idempotency-Key "
                    "o consulta la matrícula; si no se confirma, se anula automáticamente.",
                    details={"grpc_status": str(code)},
                ) from exc

            if code in _FALLAS_DE_DISPONIBILIDAD or code is None:
                _breaker.record_failure()
                log.error("Cupos no disponible en %s: %s", fn_name, code)
                # Conexión rechazada / sin resolver: la solicitud no llegó a Cupos, no hay nada
                # incierto. Un corte a mitad de la llamada sí podría haber ocupado el cupo.
                nunca_enviado = code is not None and _no_se_envio(exc)
                raise DependencyUnavailableError(
                    "No se pudo contactar al sistema de Cupos. La matrícula no fue confirmada; "
                    "por favor reintenta.",
                    details={"grpc_status": str(code)},
                    enviado=not nunca_enviado,
                ) from exc

            if code == grpc.StatusCode.NOT_FOUND:
                _breaker.record_success()  # el servicio SÍ respondió, solo no existe el curso
                raise CursoNoEncontradoError(f"El curso {curso_id} no existe en Cupos") from exc

            if code == grpc.StatusCode.FAILED_PRECONDITION:
                # el servicio está sano, solo rechazó la operación puntual
                # (ej. liberar con una clave que no ocupó ese curso)
                _breaker.record_success()
                raise ConflictError(
                    f"Cupos rechazó la operación para el curso {curso_id}: {exc.details()}"
                ) from exc

            # UNKNOWN / INTERNAL / otros: Cupos respondió con un error propio.
            # Está alcanzable, así que no cuenta como falla del circuito (pero
            # se registra record_success para liberar un sondeo HALF_OPEN).
            _breaker.record_success()
            log.error("error interno de Cupos en %s: %s %s", fn_name, code, exc.details())
            raise BadGatewayError(
                "Cupos devolvió un error inesperado.",
                details={"grpc_status": str(code)},
            ) from exc
        else:
            _breaker.record_success()
            return response

    def ocupar_cupo(self, curso_id: int, idempotency_key: str) -> str:
        """
        Reserva atómicamente un cupo en Cupos (verificación + reserva en una
        sola llamada, evitando la carrera de "consultar" + "reservar").
        Devuelve el nombre del estado (`CUPO_OCUPADO`).
        Lanza SinCuposError / CursoNoEncontradoError ante rechazos de dominio.
        """
        request = cupos_pb2.OcuparRequest(curso_id=curso_id, idempotency_key=idempotency_key)
        response = self._call("ocuparCupo", request, curso_id=curso_id)
        cache_cursos.invalidar()  # la disponibilidad cambió (o pudo cambiar)
        estado = cupos_pb2.Estado.Name(response.estado)
        if estado == "SIN_CUPOS":
            raise SinCuposError(f"El curso {curso_id} no tiene cupos disponibles")
        if estado == "CURSO_NO_ENCONTRADO":
            raise CursoNoEncontradoError(f"El curso {curso_id} no existe en Cupos")
        return estado

    def consultar_operacion(self, idempotency_key: str) -> tuple[str, str]:
        """Solo lectura: (estado_ocupar, estado_liberar) de una clave. `ESTADO_SIN_DEFINIR` =
        esa operación nunca ocurrió. No modifica ningún cupo (lo usa el conciliador)."""
        request = cupos_pb2.ConsultarOperacionRequest(idempotency_key=idempotency_key)
        response = self._call("consultarOperacion", request, curso_id=0)
        return (cupos_pb2.Estado.Name(response.estado_ocupar),
                cupos_pb2.Estado.Name(response.estado_liberar))

    def obtener_cupo(self, curso_id: int) -> int:
        request = cupos_pb2.ObtenerRequest(curso_id=curso_id)
        return self._call("obtenerCupo", request, curso_id=curso_id).cupos_libres

    def listar_cursos(self, *, solo_disponibles: bool = False) -> list[dict]:
        request = cupos_pb2.ListarRequest()
        fn = "listarCursosDisponibles" if solo_disponibles else "listarCursos"
        response = self._call(fn, request, curso_id=0)
        return [
            {"curso_id": c.curso_id, "nombre": c.nombre,
             "cupos_totales": c.cupos_totales, "cupos_libres": c.cupos_libres}
            for c in response.cursos
        ]

    def liberar_cupo(self, curso_id: int, idempotency_key: str) -> str:
        request = cupos_pb2.LiberarRequest(curso_id=curso_id, idempotency_key=idempotency_key)
        response = self._call("liberarCupo", request, curso_id=curso_id)
        cache_cursos.invalidar()
        estado = cupos_pb2.Estado.Name(response.estado)
        if estado == "CURSO_NO_ENCONTRADO":
            raise CursoNoEncontradoError(f"El curso {curso_id} no existe en Cupos")
        return estado


cupos_client = CuposClient()
