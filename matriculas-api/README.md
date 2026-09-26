# Matrículas API (REST, Python/FastAPI)

API REST pública del sistema de Matrículas de AprendeMás. Gestiona
estudiantes y sus matrículas, validando en tiempo real la disponibilidad
de cupos contra el servicio **Cupos** (gRPC, ya existente en `cupos/`)
antes de confirmar cada matrícula.

```
matriculas-api/
├── Dockerfile
├── requirements.txt
├── .env.example
└── app/
    ├── main.py            # bootstrap FastAPI, OpenAPI, exception handlers
    ├── config.py          # settings vía variables de entorno
    ├── database.py        # engine/session SQLAlchemy
    ├── models.py           # ORM: Student, Enrollment
    ├── schemas.py          # Pydantic: request/response, documentados para OpenAPI
    ├── security.py         # autenticación por API Key
    ├── errors.py           # excepciones de dominio + sobre JSON uniforme de error
    ├── circuit_breaker.py  # circuit breaker en memoria hacia Cupos
    ├── grpc_client.py      # cliente gRPC con timeout + circuit breaker
    ├── grpc_stubs/         # cupos_pb2.py / cupos_pb2_grpc.py (generados, ver abajo)
    └── routers/
        ├── students.py
        ├── enrollments.py
        └── health.py
```

## Por qué este servicio y no el CLI de Node existente

El repositorio traía un CLI en Node (`matriculas/`) que reproduce a
propósito el problema original: "se matricula sin verificar cupos". Esta
API en Python es la pieza de integración que la tarea pide: expone
matrícula como **API REST pública** y, del lado del servidor, consulta
el gRPC de Cupos para decidir si el curso está disponible antes de
persistir la matrícula. El CLI de Node se deja en el repo sin levantarse
por defecto (`profiles: ["legacy"]` en `docker-compose.yml`) solo como
referencia de "antes de la integración"; el modelo de datos de esta API
reemplaza `codigo_curso` (texto libre, sin validar) por `curso_id`
(entero, siempre el mismo id que usa Cupos), porque ahora sí existe una
fuente de verdad contra la cual validarlo.

## Cómo correrlo

### Con Docker Compose (recomendado)

Desde la raíz del repo:

```bash
cp .env.example .env
docker compose up --build -d cupos matriculas-db matriculas-api
```

La API queda en `http://localhost:8000`. Documentación interactiva
(Swagger UI) en `http://localhost:8000/docs`, esquema OpenAPI crudo en
`http://localhost:8000/openapi.json`.

Todos los endpoints de negocio requieren la cabecera `X-API-Key`. Con la
key de desarrollo por defecto:

```bash
curl -X POST http://localhost:8000/v1/students \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-matriculas-key-123" \
  -d '{"rut":"19011022-3","nombre":"Ana","apellido":"Pérez","email":"ana@example.com"}'
```

### Localmente, sin Docker

```bash
cd matriculas-api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Generar los stubs de gRPC desde proto/cupos.proto (una vez, o cuando
# cambie el .proto):
python -m grpc_tools.protoc -I ../proto \
  --python_out=app/grpc_stubs --grpc_python_out=app/grpc_stubs \
  ../proto/cupos.proto
# nota: los imports generados usan "import cupos_pb2 as cupos__pb2";
# dentro de app/grpc_stubs/cupos_pb2_grpc.py cambiarlo a
# "from . import cupos_pb2 as cupos__pb2" para que funcione como paquete.

cp .env.example .env   # ajustar CUPOS_GRPC_TARGET si Cupos corre en otro host/puerto
uvicorn app.main:app --reload --port 8000
```

Requiere que el servicio Cupos esté corriendo (`docker compose up cupos`,
o `python cupos/src/server.py` localmente tras generar sus propios
stubs con `cupos/src/generar.py`).

## Endpoints

Todos bajo `/v1`, todos requieren `X-API-Key` salvo `/v1/health`.

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/v1/students` | Crear estudiante → `201` |
| GET | `/v1/students/{id}` | Consultar estudiante → `200` / `404` |
| GET | `/v1/students` | Listar (paginado, filtro `rut`) → `200` |
| POST | `/v1/enrollments` | Matricular (valida cupos vía Cupos) → `201` / `404` / `409` / `503` |
| POST | `/v1/enrollments/{id}/revert` | Revertir matrícula (libera el cupo) → `200` / `404` / `409` / `503` |
| GET | `/v1/enrollments/{id}` | Consultar matrícula → `200` / `404` |
| GET | `/v1/enrollments` | Listar (paginado, filtros `student_id`, `curso_id`, `estado`) → `200` |
| GET | `/v1/health` | Estado del servicio y del circuit breaker hacia Cupos (sin auth) |

El contrato completo, con schemas, ejemplos y todos los códigos de
respuesta, está en el OpenAPI servido en `/openapi.json` / `/docs` — es
la fuente de verdad del contrato, generada directamente desde el código
(no puede desincronizarse de la implementación).

### Formato de error uniforme

Todas las respuestas de error (validación, 401/403, negocio, 503, 500)
usan el mismo sobre:

```json
{"error": {"code": "SIN_CUPOS", "message": "El curso 5 no tiene cupos disponibles", "details": null}}
```

Códigos usados: `VALIDATION_ERROR`, `UNAUTHORIZED`, `FORBIDDEN`,
`NOT_FOUND`, `STUDENT_ALREADY_EXISTS`, `ALREADY_ENROLLED`,
`ALREADY_REVERTED`, `SIN_CUPOS`, `CURSO_NO_ENCONTRADO`,
`CUPOS_NO_DISPONIBLE`, `INTERNAL_ERROR`.

## Autenticación: API Key

Se eligió **API Key** (header `X-API-Key`) en vez de OAuth2/JWT o Basic
Auth. Razones (detalladas también en `app/security.py`):

1. **Modelo de confianza correcto**: los consumidores de esta API son
   sistemas cliente (front-end de secretaría académica, futuras
   integraciones), no usuarios finales anónimos que necesiten un flujo
   de login interactivo, sesiones o scopes de usuario — es
   máquina-a-máquina, igual que la relación entre esta misma API y el
   gRPC de Cupos.
2. **Simplicidad y costo**: verificar una API key es una comparación
   contra un set en memoria; no hay que validar firmas ni consultar un
   Authorization Server en cada request. Importa porque el endpoint de
   matrícula ya paga el costo/latencia de una llamada gRPC externa.
3. **Trazabilidad y revocación por integración**: una key por sistema
   cliente permite auditar quién hizo qué y cortar acceso a un
   consumidor problemático sin afectar a los demás — suficiente para el
   nivel de granularidad que pide el problema.
4. **Evoluciona sin romper el contrato**: si más adelante se necesita
   identidad de usuario final real, se puede añadir JWT sobre el mismo
   mecanismo (`Authorization: Bearer ...` conviviendo con `X-API-Key`)
   sin cambiar los endpoints existentes.

Trade-off reconocido: no hay identidad de usuario final ni scopes
granulares por operación; para esta tarea (integrar dos sistemas
internos) no hacían falta.

Implementación: `app/security.py`, dependency de FastAPI
(`require_api_key`) inyectada en todos los routers de negocio. Las keys
válidas vienen de la variable `API_KEYS` (coma-separada); en producción
esto debería vivir en una tabla con hashes y rotación, no en texto plano
en una env var — se mantiene simple aquí para que el mecanismo sea
explícito y fácil de revisar.

## Manejo de fallas del gRPC de Cupos

Requisito: la API debe comportarse de forma razonable si Cupos está
caído. Estrategia (implementada en `grpc_client.py` +
`circuit_breaker.py`, ver también los docstrings de ambos archivos):

1. **Timeout por llamada** (`CUPOS_GRPC_TIMEOUT_SECONDS`, default 2s):
   ninguna matrícula queda esperando indefinidamente.
2. **Circuit breaker** (`CUPOS_CB_FAILURE_THRESHOLD` fallas
   consecutivas → circuito `OPEN` por `CUPOS_CB_RECOVERY_SECONDS`):
   evita seguir golpeando un servicio caído y hace que las siguientes
   llamadas fallen en milisegundos en vez de esperar el timeout de cada
   una. Pasado el tiempo de recuperación pasa a `HALF_OPEN` (una
   llamada de prueba) y vuelve a `CLOSED` si tiene éxito.
3. **Fail-closed, no fail-open**: si no se puede confirmar contra Cupos
   que hay cupos, la matrícula **no se registra como CONFIRMADA** — se
   responde `503 CUPOS_NO_DISPONIBLE` con header `Retry-After`, sin
   persistir nada. Esto es intencional: la regla de negocio ("solo se
   matricula si Cupos confirma cupos") no se puede satisfacer sin
   respuesta de Cupos, así que ante la duda se rechaza en vez de
   arriesgar una sobre-matrícula.
4. **Los endpoints de solo lectura no dependen de Cupos**: consultar o
   listar estudiantes/matrículas ya registradas sigue funcionando
   normalmente aunque Cupos esté caído.
5. **Idempotencia**: cada matrícula genera un `idempotency_key` (el
   propio id de la matrícula) que se envía a `ocuparCupo` /
   `liberarCupo`. Si una llamada se reintenta (p. ej. porque el cliente
   de Matrículas tuvo un timeout pero Cupos sí alcanzó a procesarla),
   Cupos no ocupa/libera el cupo dos veces — aprovecha el mecanismo de
   idempotencia que ya expone el contrato `.proto`.
6. **Condición de carrera evitada**: en vez de "consultar disponibilidad
   y luego reservar" (dos llamadas, con ventana de carrera entre
   medio), se llama directamente a `ocuparCupo`, que en Cupos hace la
   verificación + decremento en una sola transacción SQLite.

`GET /v1/health` expone el estado del circuit breaker
(`CLOSED`/`OPEN`/`HALF_OPEN`) para observabilidad/monitoreo.

### Cómo se probó esto

Se levantó el servidor gRPC de Cupos real (`cupos/src/server.py`, sin
modificar) junto a esta API, y se verificó con llamadas reales:

- Matrícula exitosa decrementa el cupo en Cupos.
- Curso sin cupos → `409 SIN_CUPOS`, sin persistir matrícula.
- Curso inexistente → `404 CURSO_NO_ENCONTRADO`.
- Matrícula duplicada del mismo estudiante/curso → `409 ALREADY_ENROLLED`.
- Revertir libera el cupo (un tercer estudiante pudo matricularse
  después en el cupo liberado); revertir dos veces → `409 ALREADY_REVERTED`.
- Con el proceso de Cupos apagado: primeras llamadas → `503` tras
  ~2s (timeout); a la 5ª falla consecutiva el circuito abre y las
  siguientes llamadas fallan en ~9ms; `GET /v1/students/{id}` sigue
  respondiendo `200` con Cupos caído.
- Al reiniciar Cupos y esperar `CUPOS_CB_RECOVERY_SECONDS`, el circuito
  pasa a `HALF_OPEN` y la siguiente matrícula exitosa lo cierra.

## Notas de diseño adicionales

- **Aislamiento de datos (principio T5 del repo)**: esta API usa su
  propia base de datos (`matriculas-db`, Postgres, vía `DATABASE_URL`;
  también soporta SQLite para desarrollo local rápido) y nunca accede
  directo a la base de Cupos — todo pasa por el contrato gRPC.
- **Semántica HTTP**: `POST` para creación (`201` + recurso creado),
  `GET` para lectura (`200`/`404`), acciones de negocio no
  idempotentes-por-URL como revertir se modelan como sub-recurso de
  acción (`POST /v1/enrollments/{id}/revert`) en vez de sobrecargar
  `DELETE` con semántica de "anular" (la matrícula no se borra: queda
  auditada con `estado=ANULADA`).
- **Paginación** simple por `limit`/`offset` en los listados, con
  `total` en la respuesta.
