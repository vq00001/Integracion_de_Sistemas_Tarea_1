from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi

from .config import settings, validar_claves_admin
from .database import init_db
from .errors import register_exception_handlers
from .reconciler import Conciliador
from .routers import courses, enrollments, health, students

DESCRIPTION = """
API REST del sistema de **Matrículas** del Centro de Formación Técnica AprendeMás.

Permite gestionar estudiantes y sus matrículas. Antes de confirmar una
matrícula, valida en tiempo real la disponibilidad de cupos consultando
al servicio **Cupos** a través de gRPC (contrato: `proto/cupos.proto`).

### Autenticación
Todos los endpoints de negocio requieren la cabecera `X-API-Key`. Hay dos tipos de clave:

* **Administrador** (variable `API_KEYS`): acceso total. Es la única que puede crear y
  listar estudiantes y emitir o revocar claves de estudiante.
* **Estudiante** (emitida a demanda con `POST /v1/students/{id}/api-key`): solo permite
  operar sobre los datos de ese estudiante (su ficha y sus matrículas). Con otra
  persona responde `403`; las matrículas ajenas responden `404`.

Ver la sección "Autenticación" del README. Los intentos de autenticación fallidos se limitan
por IP (`429` + `Retry-After`) y un estudiante con clave propia tiene un tope de escrituras por minuto.

### Cursos y disponibilidad
`GET /v1/courses` y `GET /v1/courses/{curso_id}` informan los cupos libres (dato **referencial**,
con caché de unos segundos). Quien decide si hay cupo es siempre `POST /v1/enrollments`.

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
* `503 Service Unavailable`: Cupos caído o circuito abierto (falla rápida). Si la conexión
  se rechazó (la solicitud nunca salió) no queda nada pendiente y se puede reintentar con
  una Idempotency-Key nueva o la misma. Incluye `Retry-After`.
* `502 Bad Gateway`: Cupos respondió con un error interno inesperado. Por precaución la
  matrícula también queda `PENDIENTE` y se concilia.

Una matrícula `PENDIENTE` se resuelve reintentando con la misma
`Idempotency-Key` (dentro de la ventana de reintento) o, si no se reintenta, el
conciliador la anula y libera el cupo. La lectura de datos ya existentes
(estudiantes y matrículas) no se ve afectada por una caída de Cupos.
"""

_conciliador: Conciliador | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _conciliador
    # Fail-closed: sin clave admin propia (o con una pública/corta) la API NO arranca.
    validar_claves_admin(settings.admin_api_keys)
    init_db()
    if settings.reconcile_interval_seconds > 0:
        _conciliador = Conciliador(settings.reconcile_interval_seconds)
        _conciliador.start()
    yield
    if _conciliador is not None:
        _conciliador.detener()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=DESCRIPTION,
    contact={"name": "AprendeMás - Equipo de Integración de Sistemas"},
    lifespan=lifespan,
    # En producción DOCS_ENABLED=false: no se publica el contrato ni Swagger sin autenticación.
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

register_exception_handlers(app)


@app.middleware("http")
async def cabeceras_de_deprecacion(request: Request, call_next):
    """Política de deprecación hecha código (D3): las rutas listadas en DEPRECATED_ROUTES
    responden con `Deprecation`, `Sunset` y `Link` para que los consumidores se enteren."""
    response = await call_next(request)
    try:
        rutas = json.loads(settings.deprecated_routes or "{}")
    except ValueError:
        logging.getLogger("matriculas").error("DEPRECATED_ROUTES no es JSON válido")
        return response
    for prefijo, meta in rutas.items():
        if request.url.path.startswith(prefijo):
            response.headers["Deprecation"] = meta.get("deprecation", "true")
            if meta.get("sunset"):
                response.headers["Sunset"] = meta["sunset"]
            if meta.get("link"):
                response.headers["Link"] = f'<{meta["link"]}>; rel="deprecation"'
            break
    return response


app.include_router(health.router)
app.include_router(students.router)
app.include_router(courses.router)
app.include_router(enrollments.router)


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
