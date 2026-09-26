# Experimento de timeout

Esta carpeta contiene los experimentos 3 y 5 de la pauta de Competencia 6.
Ambos requieren que los servicios estén levantados:

```bash
docker compose up -d --build cupos matriculas-db matriculas-api
```

Las dependencias de los scripts se instalan con:

```bash
pip install -r tests/requirements.txt
```

## 1. Efecto del timeout (`timeout.py`)

### Hipótesis

A medida que aumenta la latencia de Cupos, la latencia de
`POST /v1/enrollments` aumenta hasta alcanzar `CUPOS_GRPC_TIMEOUT_SECONDS`.
A partir de ese punto, Matrículas-API corta la espera y responde `503`.

### Variables

- Independiente: `SIMULAR_LATENCIA_MS` en Cupos.
- Dependiente: código de respuesta y latencia de `POST /v1/enrollments`.
- Controladas: `CUPOS_GRPC_TIMEOUT_SECONDS`, curso y hardware.

Cupos incluye el parámetro `SIMULAR_LATENCIA_MS`, por lo que no se necesita
un proxy externo. El script ejecuta 25 peticiones por escenario y prueba
latencias de `0`, `250`, `1000`, `1500`, `1900`, `2050`, `2500` y `4000 ms`.
Los puntos de `1900` y `2050 ms` rodean el timeout predeterminado de `2 s`.

Entre escenarios se reinicia Matrículas-API para que el circuit breaker
comience cerrado. Cada fila registra el código de error y el estado del
circuito, permitiendo distinguir un timeout real (`CLOSED`) de un rechazo
rápido por circuito abierto (`OPEN`). El script también imprime el
promedio, p50 y p95 de cada escenario.

```bash
python experiment/scripts/timeout.py --label timeout2s
```

Para comparar otro timeout, cambia `CUPOS_GRPC_TIMEOUT_SECONDS` en `.env`,
reconstruye `matriculas-api` y ejecuta el script con otra etiqueta:

```bash
docker compose up -d --build matriculas-api
python experiment/scripts/timeout.py --label timeout5s
```

<!-- ## 2. Throughput bajo carga (`throughput.py`)

### Hipótesis

La latencia se mantiene estable con poca concurrencia, pero el throughput
deja de crecer y el p95 aumenta cuando aparece un cuello de botella, por
ejemplo en las escrituras de SQLite, el pool de Postgres o los hilos gRPC.

Cada petición crea un estudiante, realiza una matrícula y la revierte para
no agotar cupos ni dejar datos de prueba activos. Se utilizan varios cursos
para evitar el límite del curso con solo dos cupos.

El script mide:

- throughput en peticiones por segundo (`rps`);
- latencia p50 y p95 en milisegundos;
- tasa de éxito.

Los resultados se guardan en `experiment/data/raw/`.

```bash
python experiment/scripts/throughput.py
```

El punto de saturación se identifica cuando el `rps` deja de aumentar o el
`p95` se eleva considerablemente al subir la concurrencia. -->
