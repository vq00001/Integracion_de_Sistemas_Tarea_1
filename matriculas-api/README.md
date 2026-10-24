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
    ├── grpc_stubs/         # cupos_pb2*.py: GENERADOS desde proto/cupos.proto (no se versionan)
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
docker compose up --build -d cupos matriculas-db matriculas-api
```

(`.env` es opcional: todos los valores tienen un valor por defecto de desarrollo.
Para personalizarlos, copia la plantilla con `cp .env.example .env`; el archivo
`.env` real **no se versiona**.)

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
# cambie el .proto). El script también corrige el import relativo:
python generar_stubs.py

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
| POST | `/v1/students` | Crear estudiante (valida RUT con dígito verificador) → `201` / `409` / `422` |
| GET | `/v1/students/{id}` | Consultar estudiante → `200` / `404` |
| GET | `/v1/students` | Listar (paginado, filtro `rut`) → `200` |
| POST | `/v1/enrollments` | Matricular (valida cupos vía Cupos; cabecera opcional `Idempotency-Key`) → `201` / `404` / `409` / `422` / `502` / `503` / `504` |
| POST | `/v1/enrollments/{id}/revert` | Revertir matrícula (libera el cupo) → `200` / `404` / `409` / `502` / `503` / `504` |
| GET | `/v1/enrollments/{id}` | Consultar matrícula → `200` / `404` |
| GET | `/v1/enrollments` | Listar (paginado, filtros `student_id`, `curso_id`, `estado`) → `200` |
| GET | `/v1/health` | Estado del servicio y del circuit breaker hacia Cupos (sin auth) |

El contrato completo, con schemas, ejemplos y todos los códigos de
respuesta, está en el OpenAPI servido en `/openapi.json` / `/docs`, y
versionado en `docs/openapi.json` y `docs/openapi.yaml` (se regeneran con
`python exportar_openapi.py --desde-url http://localhost:8000/openapi.json` (exporta lo que sirve la API real)). Una prueba de contrato (`tests/contract`)
falla si el archivo versionado difiere del que publica la API, y valida que
las respuestas reales cumplan el esquema declarado.

### Formato de error uniforme

Todas las respuestas de error (validación, 401, negocio, 502/503/504, 500)
usan el mismo sobre:

```json
{"error": {"code": "SIN_CUPOS", "message": "El curso 5 no tiene cupos disponibles", "details": null}}
```

Códigos usados: `VALIDATION_ERROR`, `UNAUTHORIZED`, `NOT_FOUND`,
`STUDENT_ALREADY_EXISTS`, `ALREADY_ENROLLED`, `ENROLLMENT_IN_PROGRESS`,
`ENROLLMENT_EXPIRED`, `ENROLLMENT_NOT_CONFIRMED`, `ALREADY_REVERTED`,
`IDEMPOTENCY_KEY_REUSED`, `SIN_CUPOS`, `CURSO_NO_ENCONTRADO`,
`CUPOS_NO_DISPONIBLE` (503), `CUPOS_TIMEOUT` (504),
`CUPOS_RESPUESTA_INVALIDA` (502), `PERSISTENCE_ERROR`, `INTERNAL_ERROR`.
(`FORBIDDEN` ya no se usa: ver `docs/adr/D5-autenticacion-api-key.md`.)

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

Requisito: la API debe comportarse de forma razonable si Cupos está caído
o lento. El razonamiento completo está en `docs/adr/D4_resiliencia_y_modos_de_falla.md`;
aquí el resumen operativo (implementado en `grpc_client.py`,
`circuit_breaker.py`, `routers/enrollments.py` y `reconciler.py`).

| Situación | HTTP | Código | ¿Qué pasó con el cupo? |
|---|---|---|---|
| Cupos no responde en `CUPOS_GRPC_TIMEOUT_SECONDS` (2 s) | **504** | `CUPOS_TIMEOUT` | **Incierto**: Cupos pudo ocuparlo. La matrícula queda `PENDIENTE`. |
| Cupos inalcanzable | **503** | `CUPOS_NO_DISPONIBLE` | Incierto (queda `PENDIENTE`). |
| Circuito abierto (fail-fast) | **503** | `CUPOS_NO_DISPONIBLE` | Ninguno: ni se intentó la llamada; no se persiste nada. |
| Error interno de Cupos | **502** | `CUPOS_RESPUESTA_INVALIDA` | Ninguno. |

Mecanismos:

1. **Timeout por llamada**: ninguna matrícula espera indefinidamente.
2. **Circuit breaker** (`CUPOS_CB_FAILURE_THRESHOLD` fallas consecutivas →
   `OPEN` durante `CUPOS_CB_RECOVERY_SECONDS`). Solo cuentan las fallas de
   *disponibilidad* (timeout, `UNAVAILABLE`, `RESOURCE_EXHAUSTED`); los
   errores de dominio e internos de Cupos significan que sí respondió.
3. **Fail-closed**: sin confirmación de Cupos, la matrícula nunca queda
   `CONFIRMADA`.
4. **Matrícula en dos pasos**: se registra una intención `PENDIENTE` (un
   índice único parcial sobre `(student_id, curso_id)` de los estados activos
   impide duplicados concurrentes sin tocar Cupos) y luego se ocupa el cupo.
   Rechazos definitivos (sin cupos, curso inexistente) borran la intención.
5. **Idempotencia de punta a punta**: cada matrícula tiene una clave interna
   que se envía a `ocuparCupo`/`liberarCupo`, y el cliente puede enviar
   `Idempotency-Key` para reintentar: la misma clave devuelve la misma
   matrícula (`Idempotent-Replayed: true`) y nunca consume otro cupo.
6. **Conciliación**: una matrícula `PENDIENTE` se resuelve reintentando con la
   misma `Idempotency-Key` dentro de `PENDING_TTL_SECONDS / 2`; si no, pasado
   `PENDING_TTL_SECONDS` un hilo en segundo plano (`reconciler.py`, cada
   `RECONCILE_INTERVAL_SECONDS`) la anula (`estado=ANULADA`, con `motivo`) y
   se asegura de liberar el cupo.
7. **Compensación**: si falla el guardado local después de ocupar el cupo, se
   libera el cupo con la misma clave.
8. **Lecturas sin dependencia**: consultar o listar estudiantes y matrículas
   funciona aunque Cupos esté caído.

`GET /v1/health` expone el estado del circuit breaker
(`CLOSED`/`OPEN`/`HALF_OPEN`).

### Cómo se probó esto

La suite `tests/` (ver README raíz) cubre, contra el sistema real:

- Matrícula, rechazo por falta de cupos, curso inexistente, duplicado y
  reversión; **matricular → revertir → matricular → revertir** el mismo curso.
- **Concurrencia**: 6 matrículas simultáneas del mismo estudiante → un `201` y
  cinco `409`, con un solo cupo consumido en Cupos; 6 reversiones simultáneas →
  un `200`, cinco `409` y un solo cupo liberado; estudiantes distintos
  compitiendo por un curso de capacidad baja no lo sobrepasan.
- **Idempotencia**: repetir la `Idempotency-Key` no duplica; reutilizarla con
  otro curso da `422`.
- **Fallas**: Cupos apagado → `503`/`504` sin colgarse y sin matrícula
  `CONFIRMADA`; Cupos lento → `504`, el cupo huérfano se concilia, y tres
  reintentos con la misma clave consumen un solo cupo.
- **Contrato**: el OpenAPI versionado coincide con el publicado y las
  respuestas reales cumplen el esquema declarado.

## Notas de diseño adicionales

- **Privacidad**: Cupos nunca recibe datos personales (solo `curso_id` y una
  clave opaca); el RUT se valida (módulo 11) y se guarda normalizado; los
  errores de validación no devuelven el dato enviado; Postgres y el gRPC de
  Cupos no se publican en el host. Ver "Consideraciones de contexto" del informe.
- **Esquema existente**: al arrancar sobre una base Postgres de la versión
  anterior, `database.py` migra la tabla `enrollments` (quita la restricción
  única antigua, agrega `client_key` y los índices). Con SQLite local, borra
  el archivo `matriculas.db` para recrearlo.
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
