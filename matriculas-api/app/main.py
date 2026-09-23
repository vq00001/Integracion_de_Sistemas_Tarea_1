from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .config import settings
from .database import init_db
from .errors import register_exception_handlers
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

### Disponibilidad de Cupos
Si el servicio gRPC de Cupos está caído, lento, o su circuit breaker está
abierto, los endpoints que dependen de él (`POST /v1/enrollments`,
`POST /v1/enrollments/{id}/revert`) responden `503 Service Unavailable`
con `Retry-After`, sin registrar una matrícula inconsistente. La lectura
de datos ya existentes (estudiantes y matrículas) no se ve afectada.
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


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
