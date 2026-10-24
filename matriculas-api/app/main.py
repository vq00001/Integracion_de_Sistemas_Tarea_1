from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .config import settings
from .database import init_db
from .errors import register_exception_handlers
from .reconciler import Conciliador
from .routers import enrollments, health, students

DESCRIPTION = """
API REST del sistema de **Matrículas** del Centro de Formación Técnica AprendeMás.

Permite gestionar estudiantes y sus matrículas. Antes de confirmar una
matrícula, valida en tiempo real la disponibilidad de cupos consultando
al servicio **Cupos** a través de gRPC (contrato: `proto/cupos.proto`).

### Autenticación
Todos los endpoints de negocio requieren la cabecera `X-API-Key` con una
API key válida (ver sección "Autenticación" del README para el porqué de
esta elección).

### Errores
Todas las respuestas de error usan el mismo formato JSON:

```json
{"error": {"code": "SIN_CUPOS", "message": "...", "details": null}}
```

### Idempotencia
`POST /v1/enrollments` acepta la cabecera `Idempotency-Key`. Reintentar con la
misma clave devuelve la misma matrícula y nunca consume un segundo cupo.

### Disponibilidad de Cupos
Los endpoints que dependen de Cupos (`POST /v1/enrollments`,
`POST /v1/enrollments/{id}/revert`) distinguen tres fallas de la dependencia:

* `504 Gateway Timeout`: Cupos no respondió a tiempo. El resultado es
  **incierto**; la matrícula queda `PENDIENTE` y se concilia (ver más abajo).
* `503 Service Unavailable`: Cupos caído o circuito abierto (falla rápida).
  Incluye `Retry-After`.
* `502 Bad Gateway`: Cupos respondió con un error interno inesperado.

Una matrícula `PENDIENTE` se resuelve reintentando con la misma
`Idempotency-Key` (dentro de la ventana de reintento) o, si no se reintenta, el
conciliador la anula y libera el cupo. La lectura de datos ya existentes
(estudiantes y matrículas) no se ve afectada por una caída de Cupos.
"""

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=DESCRIPTION,
    contact={"name": "AprendeMás - Equipo de Integración de Sistemas"},
)

register_exception_handlers(app)

app.include_router(health.router)
app.include_router(students.router)
app.include_router(enrollments.router)


_conciliador: Conciliador | None = None


@app.on_event("startup")
def on_startup() -> None:
    global _conciliador
    init_db()
    if settings.reconcile_interval_seconds > 0:
        _conciliador = Conciliador(settings.reconcile_interval_seconds)
        _conciliador.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    if _conciliador is not None:
        _conciliador.detener()


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }
    for path in schema.get("paths", {}).values():
        for operation in path.values():
            if operation.get("tags") in (["Salud"],):
                continue
            operation.setdefault("security", [{"ApiKeyAuth": []}])
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
