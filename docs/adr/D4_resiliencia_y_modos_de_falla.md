# D4 - Resiliencia y modos de falla

**Estado:** aceptada (revisada tras la evaluación: el código y el ADR ahora coinciden en los códigos 503/504/502 y se agregan la conciliación y la idempotencia por cliente).

## Contexto

Matrículas depende de Cupos en el camino crítico de cada matrícula: antes de confirmar debe ocupar un cupo por gRPC. Si Cupos está caído, lento o responde con error, la API REST no puede esperar indefinidamente ni fallar en silencio, y el código HTTP devuelto debe estar justificado.

Hay además una dificultad que la primera versión no consideraba: **un timeout no significa que la operación no ocurrió.** Cuando la API corta la espera a los 2 s, Cupos puede seguir procesando y terminar ocupando el cupo. El llamador queda con un resultado *incierto*. En la revisión se observó que, con Cupos lento, la API respondía 503 "la matrícula no fue confirmada" mientras el cupo quedaba ocupado sin ninguna matrícula, y que cada reintento del cliente ocupaba otro cupo.

Cupos incorpora la variable de entorno `SIMULAR_LATENCIA_MS` para inyectar latencia artificial y medir estos escenarios.

## Alternativas consideradas

- **Esperar sin límite y propagar el error de gRPC.** Sin lógica adicional, pero mantiene hebras y conexiones bloqueadas y no da al cliente un código claro.
- **Deadline por llamada + traducción a códigos HTTP.** Acota la espera y responde de forma predecible, a costa de rechazar solicitudes que habrían tenido éxito con un poco más de tiempo. Por sí sola, deja el resultado incierto sin resolver.
- **Deadline + circuit breaker + matrícula en dos pasos (intención y confirmación) con idempotencia y conciliación.** Es la única que además mantiene consistentes los cupos tras un timeout.
- **Agregar caché de disponibilidad (Redis, opcional O1).** Reduce la carga sobre Cupos, pero no resuelve la incertidumbre del resultado y agrega otro componente; queda como evolución futura.

## Decisión

Cada llamada gRPC a Cupos lleva un deadline explícito (2 s por defecto) y está protegida por un circuit breaker. Las fallas se traducen así:

| Situación | Código HTTP | Código de error | ¿Resultado incierto? |
|---|---|---|---|
| Cupos no responde dentro del deadline (`DEADLINE_EXCEEDED`) | **504** | `CUPOS_TIMEOUT` | Sí |
| Cupos inalcanzable (`UNAVAILABLE`) | **503** | `CUPOS_NO_DISPONIBLE` | Sí (pudo llegar a medias) |
| Circuito abierto (fail-fast) | **503** | `CUPOS_NO_DISPONIBLE` | No: la llamada ni se intentó |
| Cupos responde con error interno (`UNKNOWN`/`INTERNAL`) | **502** | `CUPOS_RESPUESTA_INVALIDA` | No |
| Curso inexistente (`NOT_FOUND`) / sin cupos | 404 / 409 | `CURSO_NO_ENCONTRADO` / `SIN_CUPOS` | No (error de dominio, no falla de la dependencia) |

La matrícula se registra en dos pasos: primero una *intención* `PENDIENTE` (con un índice único parcial que impide duplicados concurrentes) y luego la llamada a `ocuparCupo`, tras la cual pasa a `CONFIRMADA`. Si el resultado es incierto, la matrícula **queda `PENDIENTE`** y se informa su id en el error.

La incertidumbre se resuelve por dos vías, ambas posibles porque `ocuparCupo` y `liberarCupo` son idempotentes por clave:

1. **Reintento del cliente.** Con la misma cabecera `Idempotency-Key`, dentro de la mitad del TTL, la API vuelve a invocar `ocuparCupo` con la misma clave interna: obtiene el resultado real y confirma. Reintentar tres veces consume **un** cupo.
2. **Conciliador.** Si no hay reintento, pasado el TTL (30 s por defecto) un proceso en segundo plano cierra la matrícula: invoca `ocuparCupo` (que revela el estado real), libera el cupo si estaba retenido, y marca la matrícula `ANULADA` con su motivo. No confirma en silencio una matrícula que al cliente se le dijo que estaba incierta.

La ventana de reintento (TTL/2) y el conciliador (TTL) no se solapan, para que nunca actúen a la vez sobre la misma matrícula.

Si el guardado local falla después de ocupar el cupo, se compensa liberándolo con la misma clave.

**Qué cuenta como falla para el circuit breaker.** Solo los problemas de disponibilidad: timeout, `UNAVAILABLE` y `RESOURCE_EXHAUSTED`. Los errores de dominio y los internos del servidor (`UNKNOWN`/`INTERNAL`) significan que Cupos *respondió*; antes, un choque de claves dentro de Cupos llegaba como `UNKNOWN` y sumaba fallas aunque el servicio estuviera sano (la causa raíz se corrigió además en Cupos serializando las escrituras con `BEGIN IMMEDIATE`).

## Justificación

- **504 vs 503.** `504 Gateway Timeout` describe exactamente "el intermediario no recibió respuesta a tiempo de la dependencia" (RFC 9110) y le dice al cliente algo distinto que un 503: *la operación podría haberse realizado*. Por eso el cliente debe reintentar con la misma clave en lugar de crear una solicitud nueva. `503` se reserva para "no estoy en condiciones de atender ahora".
- **Deadline.** Acota el consumo de hebras y conexiones y hace determinista la falla. Sin él, una dependencia lenta puede agotar los recursos de Matrículas aunque Matrículas esté sana.
- **Circuit breaker.** Evita pagar el timeout completo en cada solicitud una vez confirmada una falla sostenida (medido en el experimento 1).
- **Intención + conciliación en lugar de "esperar más".** Aumentar el timeout no elimina la incertidumbre, solo la desplaza; la idempotencia hace que reintentar y conciliar sea seguro.
- **Evidencia.** Experimento 1 (`experiment/scripts/timeout.py`): comportamiento de la latencia y de los códigos alrededor del umbral. Experimento 2 (`experiment/scripts/consistencia.py`): cupos huérfanos tras un timeout, con y sin conciliador.

## Costo aceptado

- Un deadline fijo rechaza solicitudes que habrían tenido éxito con un poco más de tiempo; fijarlo corto aumenta los rechazos innecesarios y largo reduce la protección. El valor se calibra con el experimento, no arbitrariamente.
- La matrícula tiene un estado más (`PENDIENTE`), visible para el cliente, y hay un componente más (el conciliador) que mantener y monitorear.
- Durante la ventana de incertidumbre (hasta TTL + intervalo del conciliador) un cupo puede estar retenido sin matrícula confirmada. Se acepta porque es acotado en el tiempo y se corrige solo; antes era permanente.
- El estudiante no puede crear otra matrícula para el mismo curso mientras la anterior esté `PENDIENTE` (recibe `409 ENROLLMENT_IN_PROGRESS`).
- Los errores `UNKNOWN`/`INTERNAL` no abren el circuito: un Cupos que falle de forma rápida y persistente devolvería 502 sin ser aislado. Se acepta porque el circuito protege de la *latencia*, no de los errores rápidos, y porque el 502 es visible.

## Consecuencias

- Los clientes deben enviar `Idempotency-Key` para reintentar con seguridad; sin ella, el índice único parcial igualmente evita consumir más de un cupo, pero el cliente debe esperar a que la matrícula pendiente se resuelva.
- Un 504 pide al cliente consultar `GET /v1/enrollments/{id}` o reintentar con la misma clave.
- Si Cupos pasara a tener una operación de consulta del estado de una clave (p. ej. `obtenerOperacion`), el conciliador podría evitar el par ocupar/liberar. Si se agrega caché (O1), habría que invalidarla al ocupar y liberar.
- Si el volumen crece, el conciliador debería ejecutarse en un proceso aparte con bloqueo por fila, en vez de un hilo dentro de la API.
