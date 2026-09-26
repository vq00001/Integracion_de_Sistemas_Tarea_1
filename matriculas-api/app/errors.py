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


class DependencyUnavailableError(ApiError):
    """El servicio gRPC de Cupos no respondió a tiempo o está caído/circuito abierto."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "CUPOS_NO_DISPONIBLE"


def _error_json(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        headers = {}
        if isinstance(exc, DependencyUnavailableError):
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_json(
                "VALIDATION_ERROR",
                "La solicitud no cumple con el esquema esperado",
                {"errors": exc.errors()},
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
        503: "SERVICE_UNAVAILABLE",
    }.get(status_code, "ERROR")
