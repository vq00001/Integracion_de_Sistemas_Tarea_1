"""
Cliente hacia el servicio gRPC de Cupos.

Estrategias de resiliencia aplicadas (requisito: "la API debe comportarse
de forma razonable cuando el sistema gRPC esté caído"):

1. Timeout corto por llamada (`cupos_grpc_timeout_seconds`): una matrícula
   nunca debe quedar esperando indefinidamente a Cupos.
2. Circuit breaker: si Cupos encadena varias fallas, se deja de intentar
   por un tiempo y se falla rápido (503) en vez de acumular llamadas
   colgadas contra un servicio caído.
3. Fail-closed, no fail-open: si no se puede confirmar contra Cupos que
   hay cupos disponibles, la matrícula NO se registra como CONFIRMADA.
   La regla de negocio ("solo se matricula si hay cupos según Cupos") no
   se puede cumplir sin respuesta de Cupos, así que ante duda se rechaza
   la operación (503 DependencyUnavailableError) en vez de arriesgarse a
   sobre-matricular un curso. Los endpoints de lectura de Matrículas
   (consultar/listar estudiantes y matrículas ya registradas) siguen
   funcionando igual: no dependen de Cupos.
4. Uso del `idempotency_key` que expone el contrato de Cupos: si Matrículas
   reintenta una operación (por timeout del lado del cliente, por
   ejemplo) usando la misma clave, Cupos no ocupa/libera el cupo dos
   veces.
"""
from __future__ import annotations

import logging

import grpc

from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .config import settings
from .errors import ConflictError, CursoNoEncontradoError, DependencyUnavailableError, SinCuposError
from .grpc_stubs import cupos_pb2, cupos_pb2_grpc

log = logging.getLogger("matriculas.grpc_client")

_breaker = CircuitBreaker(
    failure_threshold=settings.cupos_cb_failure_threshold,
    recovery_seconds=settings.cupos_cb_recovery_seconds,
)

# Errores de gRPC que consideramos "el servicio no está disponible ahora",
# en contraposición a errores de aplicación (NOT_FOUND, FAILED_PRECONDITION)
# que sí sabemos interpretar como reglas de negocio.
_TRANSIENT_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.INTERNAL,
    grpc.StatusCode.UNKNOWN,
}


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
                "(circuito abierto tras fallas repetidas). Intenta de nuevo en unos segundos."
            )
        method = getattr(self._stub, fn_name)
        try:
            response = method(request, timeout=settings.cupos_grpc_timeout_seconds)
        except grpc.RpcError as exc:
            code = exc.code() if hasattr(exc, "code") else None
            if code in _TRANSIENT_CODES or code is None:
                _breaker.record_failure()
                log.error("fallo transitorio llamando a Cupos.%s: %s", fn_name, code)
                raise DependencyUnavailableError(
                    "El sistema de Cupos no respondió a tiempo. La matrícula no fue "
                    "confirmada; por favor reintenta.",
                    details={"grpc_status": str(code)},
                ) from exc
            if code == grpc.StatusCode.NOT_FOUND:
                _breaker.record_success()  # el servicio SÍ respondió, solo no existe el curso
                raise CursoNoEncontradoError(
                    f"El curso {curso_id} no existe en Cupos"
                ) from exc
            if code == grpc.StatusCode.FAILED_PRECONDITION:
                # el servicio está sano, solo rechazó la operación puntual
                # (ej. liberar un cupo que ya estaba completamente libre)
                _breaker.record_success()
                raise ConflictError(
                    f"Cupos rechazó la operación para el curso {curso_id}: {exc.details()}"
                ) from exc
            # cualquier otro código no contemplado: por seguridad, se trata
            # igual que una falla del dependiente en vez de asumir éxito.
            _breaker.record_failure()
            raise DependencyUnavailableError(
                f"Cupos devolvió un error inesperado: {exc.details()}",
                details={"grpc_status": str(code)},
            ) from exc
        else:
            _breaker.record_success()
            return response

    def ocupar_cupo(self, curso_id: int, idempotency_key: str) -> str:
        """
        Reserva atómicamente un cupo en Cupos (verificación + reserva es
        una sola llamada del lado de Cupos, evitando la condición de
        carrera de "consultar disponibilidad" + "reservar" por separado).
        Devuelve el nombre del estado (`CUPO_OCUPADO`, `SIN_CUPOS`, ...).
        """
        request = cupos_pb2.OcuparRequest(curso_id=curso_id, idempotency_key=idempotency_key)
        response = self._call("ocuparCupo", request, curso_id=curso_id)
        estado = cupos_pb2.Estado.Name(response.estado)
        if estado == "SIN_CUPOS":
            raise SinCuposError(f"El curso {curso_id} no tiene cupos disponibles")
        if estado == "CURSO_NO_ENCONTRADO":
            raise CursoNoEncontradoError(f"El curso {curso_id} no existe en Cupos")
        return estado

    def liberar_cupo(self, curso_id: int, idempotency_key: str) -> str:
        request = cupos_pb2.LiberarRequest(curso_id=curso_id, idempotency_key=idempotency_key)
        response = self._call("liberarCupo", request, curso_id=curso_id)
        estado = cupos_pb2.Estado.Name(response.estado)
        if estado == "CURSO_NO_ENCONTRADO":
            raise CursoNoEncontradoError(f"El curso {curso_id} no existe en Cupos")
        return estado


cupos_client = CuposClient()
