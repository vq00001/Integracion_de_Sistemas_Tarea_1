# Tarea 1 Integración de Sistemas

La Centro de Formación Técnica AprendeMás opera hoy con dos sistemas que nacieron por separado y nunca conversaron entre sí:

- **Cupos**: un sistema que administra los cursos y los cupos disponibles de cada uno. Fue desarrollado internamente y su equipo lo mantiene activamente. Es donde vive la verdad sobre qué cursos se dictan y cuántos cupos quedan en cada uno.
- **Matrículas**: un sistema que registra a los estudiantes y sus matrículas. Necesita saber, para cada curso, si hay cupos disponibles antes de matricular a un estudiante, y consulta esa información con muchísima frecuencia. Hoy los dos sistemas no están integrados: se matricula sin verificar cupos y algunos cursos superan la capacidad de la sala.

Este proyecto implementa una solución de integración para el Sistema de Gestión de Cursos y Matrículas del AprendeMás, y prueba la resiliencia de esta a través de experimentos.

**La integración vive en `matriculas-api/`**: una API REST pública
(Python/FastAPI) que expone Matrículas y, del lado del servidor,
consulta el gRPC de Cupos para decidir si un curso tiene cupos antes de
confirmar cada matrícula. Ver `matriculas-api/README.md` para el detalle
completo (autenticación, manejo de fallas del gRPC, endpoints, OpenAPI).
El CLI de Node en `matriculas/` se conserva solo como referencia del
estado "antes de la integración" descrito más abajo; no se levanta por
defecto.

## Estructura del repositorio

```
.
├── docker-compose.yml     # orquesta TODOS los servicios (cupos + matriculas-db + matriculas-api [+ matriculas, legacy])
├── .env.example
├── proto/
│   └── cupos.proto          # contrato gRPC de Cupos (fuente de verdad de la integración)
├── cupos/                   # servicio Cupos (Python + gRPC + SQLite) - sin cambios
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/
│       ├── server.py
│       └── generar.py        # genera los stubs (*_pb2.py) desde proto/cupos.proto
├── matriculas-api/          # ★ API REST de Matrículas (Python + FastAPI) - la integración
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── README.md             # autenticación, resiliencia, endpoints, cómo correrla
│   └── app/
│       ├── main.py, config.py, database.py, models.py, schemas.py
│       ├── security.py       # autenticación por API Key
│       ├── errors.py         # errores JSON uniformes
│       ├── grpc_client.py + circuit_breaker.py  # cliente gRPC resiliente hacia Cupos
│       └── routers/          # students, enrollments, health
└── matriculas/               # CLI legado (Node.js + Postgres) - "antes de la integración"
    ├── Dockerfile
    ├── package.json
    └── src/
        ├── index.js            # bootstrap: conecta y sincroniza el esquema (no es un servidor)
        ├── config/database.js
        ├── models/              # Student, Enrollment (sin Course - ver mas abajo)
        ├── database/seed.js
        └── cli/                 # herramienta interactiva de uso interno (uso: ver mas abajo)
```

Cada servicio tiene **su propia base de datos** (principio T5: ningún
servicio accede directo a los datos del otro):
- `cupos` usa SQLite, en el volumen `cupos_data`.
- `matriculas-db` usa Postgres, en el volumen `matriculas_db_data`.

Ambos `Dockerfile` se construyen con el mismo patrón: `context: .` (la
raíz del repo) + `dockerfile: <servicio>/Dockerfile`, así que sus `COPY`
llevan el prefijo del servicio (`cupos/...`, `matriculas/...`).

### Matrículas no tiene tabla de cursos

Matrículas registra estudiantes y sus matrículas — no el catálogo de
cursos, que le pertenece a Cupos. Por eso `Enrollment` no tiene una
foreign key a ninguna tabla `Course` local: guarda directamente
`codigo_curso`, un texto libre (ej. `"PY-101"`) que quien matricula
escribe, sin validar contra nada. Es intencional: refleja el estado real
descrito en el enunciado, donde Matrículas "se matricula sin verificar
cupos" porque ni siquiera tiene forma de verificar que el curso exista.

## Instrucciones de ejecución

### Levantar la integración completa (Cupos + Matrículas API)

```bash
cp .env.example .env
docker compose up --build -d cupos matriculas-db matriculas-api
```

La API REST de Matrículas queda en `http://localhost:8000` (docs en
`/docs`). Ver `matriculas-api/README.md` para ejemplos de uso con
`curl`, la lista de endpoints y el mecanismo de autenticación.

### (Referencia) CLI legado de Matrículas, sin integración con Cupos

Se mantiene solo para comparar el comportamiento "antes de la
integración". No se levanta con `docker compose up` por defecto (usa un
profile):

```bash
docker compose --profile legacy run --rm matriculas node src/index.js   # prepara el esquema, una vez
docker compose --profile legacy run --rm -it matriculas npm run cli
```

Este CLI reproduce a propósito el problema descrito en el enunciado:
matricula sin verificar cupos disponibles contra Cupos (ver
`matriculas/src/cli/actions/enrollments.js`). Es intencional — es la
versión "antes de la integración", para poder comparar el comportamiento
contra `matriculas-api`, que sí valida contra Cupos.

### Servicio Cupos por separado

Para ejecutarlo solo:

```bash
docker compose up --build cupos
```

Prueba rápida por línea de comandos (usando `grpcurl`, si lo tienes instalado):

```bash
grpcurl -plaintext -import-path proto -proto cupos.proto \
  -d '{"curso_id": 5}' localhost:50051 cupo.Cupos/obtenerCupo
```

(El curso `5`, "Ciberseguridad Básica", viene precargado con solo 2 cupos — útil para probar el caso sin cupos.)
