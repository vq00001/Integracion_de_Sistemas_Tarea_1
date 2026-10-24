"""
Manejo de errores centralizado.

Toda la API responde errores con el mismo sobre JSON:

    {"error": {"code": "SIN_CUPOS", "message": "...", "details": {...}}}

en vez de mezclar formatos según el framework o la librería que falló.
"""
from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """Excepción base de dominio. Cada subclase fija su propio status HTTP."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "ERROR"

    def __init__(self, message: str, *, details: dict | None = None, code: str | None = None):
        self.message = message
        self.details = details
        if code:
            self.code = code
        super().__init__(message)


class NotFoundError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(ApiError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class SinCuposError(ApiError):
    status_code = status.HTTP_409_CONFLICT
    code = "SIN_CUPOS"


class CursoNoEncontradoError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "CURSO_NO_ENCONTRADO"


class PersistenceError(ApiError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "PERSISTENCE_ERROR"


class EnrollmentInProgressError(ConflictError):
    code = "ENROLLMENT_IN_PROGRESS"


class IdempotencyKeyReusedError(ApiError):
    status_code = 422  # Unprocessable Content
    code = "IDEMPOTENCY_KEY_REUSED"


class DependencyError(ApiError):
    """Falla de la dependencia (Cupos), no del cliente.

    `enviado` indica si la solicitud pudo haber llegado a Cupos. Si es False
    (p. ej. circuito abierto: ni siquiera se intentó la llamada) se sabe con
    certeza que Cupos no retuvo ningún cupo. Si es True el resultado es
    INCIERTO y hay que conciliarlo.
    """

    enviado: bool = True

    def __init__(self, message: str, *, details: dict | None = None,
                 code: str | None = None, enviado: bool = True):
        super().__init__(message, details=details, code=code)
        self.enviado = enviado


class DependencyUnavailableError(DependencyError):
    """Cupos caído, inalcanzable o circuito abierto (fail-fast) → 503."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "CUPOS_NO_DISPONIBLE"


class DependencyTimeoutError(DependencyError):
    """Cupos no respondió dentro del plazo (deadline) → 504. Resultado incierto."""

    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "CUPOS_TIMEOUT"


class BadGatewayError(DependencyError):
    """Cupos respondió con un error interno inesperado (UNKNOWN/INTERNAL) → 502."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "CUPOS_RESPUESTA_INVALIDA"


def _error_json(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        headers = {}
        if isinstance(exc, (DependencyUnavailableError, DependencyTimeoutError)):
            # sugiere al cliente cuándo reintentar en vez de martillar el servicio caído
            headers["Retry-After"] = "5"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_json(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_json(
                "VALIDATION_ERROR",
                "La solicitud no cumple con el esquema esperado",
                # Solo ubicación, mensaje y tipo. Se omite `input` a propósito:
                # no se devuelve (ni se registra) el dato personal enviado.
                {"errors": [
                    {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Normaliza también las HTTPException "crudas" (401, 403, 404 por defecto, etc.)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_json(
                _code_for_status(exc.status_code), str(exc.detail), None
            ),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_json("INTERNAL_ERROR", "Error interno inesperado"),
        )


def _code_for_status(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "TOO_MANY_REQUESTS",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
        504: "GATEWAY_TIMEOUT",
    }.get(status_code, "ERROR")
