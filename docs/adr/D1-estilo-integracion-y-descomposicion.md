# D1 - Estilo de integración y descomposición

**Estado:** aceptada (revisada: análisis de los datos duplicados; revisión 3: se expone la lectura de disponibilidad con un caché de TTL corto y se declaran sus límites).

El Centro de Formación Técnica AprendeMás opera dos sistemas que nacieron y evolucionaron de manera independiente: *Cupos*, responsable de administrar cursos y disponibilidad, y *Matrículas*, encargado del registro de estudiantes y matrículas. Para confirmar una matrícula, Matrículas necesita consultar frecuentemente el estado de los cupos disponibles y actualizar dicha información de forma consistente.

El problema principal de diseño consiste en determinar si ambos dominios deben integrarse como una única aplicación o mantenerse separados mediante mecanismos de comunicación explícitos.

Se analizaron distintas alternativas arquitectónicas. La integración orientada a eventos fue descartada como mecanismo principal de coordinación, debido a que el proceso de matrícula requiere consultas síncronas y respuestas inmediatas sobre la disponibilidad de cupos. Un enfoque basado únicamente en eventos introduciría consistencia eventual, dificultando garantizar que la información presentada al usuario corresponda al estado actual de los cupos.

También se descartó la adopción de una solución *Serverless*, ya que el alcance del proyecto requiere servicios con estado persistente, comunicación continua entre componentes y despliegue controlado dentro de un entorno basado en contenedores, alineado con los requisitos técnicos del encargo.

**Alternativas consideradas.**
- **Monolito único.** Fusionar Cupos y Matrículas en una única aplicación y una base de datos compartida. Esta alternativa simplifica la comunicación entre módulos y reduce la infraestructura necesaria, pero incrementa el acoplamiento entre dominios que originalmente fueron desarrollados por separado. Además, dificulta la evolución independiente de cada sistema y aumenta el impacto de fallas o cambios sobre el conjunto completo de la aplicación.
- **Dos Servicios independientes, cada uno con su propia base de datos, comunicados mediante contratos explícitos.** Mantiene la separación entre ambos dominios, permite la evolución independiente de cada servicio y satisface el requisito de base de datos por servicio. Como contrapartida, introduce mayor complejidad de integración, coordinación y manejo de errores.



Se adopta la segunda alternativa, implementando dos servicios independientes: Cupos y Matrículas, cada uno con su propia base de datos y comunicados exclusivamente mediante contratos versionados. Matrículas expone una API REST como interfaz pública, mientras que Cupos expone un servicio gRPC para consultas y modificaciones de disponibilidad.

Esta decisión se justifica porque ambos sistemas representan dominios distintos, fueron desarrollados en momentos diferentes y no comparten directamente sus datos. El alcance del proyecto se limita a resolver la interacción necesaria para la gestión de cupos durante el proceso de matrícula, sin requerir una unificación completa de ambos sistemas.

La separación en servicios establece fronteras de responsabilidad claras y permite que cada componente evolucione de manera independiente, siempre que se respeten los contratos de integración. Esta decisión corresponde al patrón de API REST para consumidores externos y gRPC para comunicación interna entre servicios.

Como consecuencia, se acepta una mayor complejidad operacional respecto de un monolito, incluyendo la necesidad de administrar comunicación remota, contratos de integración, manejo de errores, timeouts y recuperación frente a fallos de dependencias, aspectos abordados en los ADR posteriores.

Finalmente, las responsabilidades quedan definidas de la siguiente forma:
- **Matrículas:** administración de estudiantes, matrículas y estados asociados, además de exponer la API pública del sistema.
- **Cupos:** administración de cursos, cupos totales, cupos disponibles y operaciones de ocupación y liberación de cupos.


Matrículas consulta siempre a Cupos mediante los contratos definidos y nunca accede directamente a su base de datos.

## Datos duplicados a propósito y su costo

Separar los servicios obliga a que cada uno guarde las referencias que necesita del otro. La duplicación es deliberada y se limita a lo mínimo:

| Dato | Dónde vive la verdad | Dónde se duplica | Por qué se duplica |
|---|---|---|---|
| Identificador del curso (`curso_id`) | Cupos (catálogo y cupos) | Columna `curso_id` de cada matrícula en Matrículas, **sin clave foránea** | Una matrícula debe saber a qué curso pertenece para poder consultarlo y liberar su cupo. Matrículas no tiene tabla de cursos: no administra el catálogo. |
| Clave de idempotencia de la operación | Matrículas (la genera) | Registro de operaciones procesadas en Cupos (`operaciones_idempotentes`) | Permite reintentar ocupar o liberar sin duplicar el efecto. Es un UUID opaco, no contiene datos personales. |
| Estado de la ocupación (cupo retenido sí/no) | Cupos | Estado de la matrícula (`PENDIENTE`/`CONFIRMADA`/`ANULADA`) en Matrículas | La matrícula refleja el resultado de la operación en Cupos para responder consultas sin llamarlo. |

**Costos aceptados de esta duplicación.**

- *Referencia colgante:* si un curso se elimina o se renumera en Cupos, las matrículas existentes conservan un `curso_id` que ya no significa lo mismo. Matrículas no puede detectarlo por sí sola: valida el curso solo en el momento de matricular, consultando a Cupos. Mitigación: el contrato establece que los `curso_id` de Cupos son estables y no se reutilizan.
- *Dos fuentes que pueden divergir:* el cupo puede quedar retenido en Cupos mientras Matrículas no tiene una matrícula confirmada (por ejemplo, tras un timeout). Esto no se ignora: se modela con el estado `PENDIENTE` y se corrige con un conciliador (ver D4), en lugar de suponer que ambos estados siempre coinciden.
- *Sin transacción distribuida:* no existe una transacción entre las dos bases de datos. La consistencia se obtiene con una secuencia de pasos idempotentes y compensables (saga), no con atomicidad.

**Qué se revisaría si cambia el contexto.** Si el catálogo de cursos pasara a tener cambios frecuentes (renombres, bajas), convendría que Cupos publicara eventos de cambio y que Matrículas mantuviera una copia de solo lectura del catálogo, aceptando consistencia eventual en esa parte.

## Lectura de disponibilidad: la consulta "muy frecuente" y su caché
El encargo subraya que Matrículas consulta la disponibilidad **con muchísima frecuencia**, pero hasta la revisión 3 la API pública no exponía ninguna ruta de lectura: los RPC `obtenerCupo`, `listarCursos` y `listarCursosDisponibles` solo se usaban en pruebas, y un portal no podía ni descubrir qué `curso_id` existen. Ahora existen `GET /v1/courses` (con `?disponibles=true`) y `GET /v1/courses/{curso_id}`.

**Decisión.** Esas rutas se sirven de un **caché en memoria de la API con TTL corto** (`CURSOS_CACHE_TTL_SECONDS`, 5 s por defecto; `Cache-Control: private, max-age=<TTL>` y `X-Cache: HIT|MISS` en la respuesta). El caché se **invalida** cuando esta API ocupa o libera un cupo, y evita la estampida: si el TTL vence con muchas solicitudes en vuelo, solo una va a Cupos.

**Por qué es seguro hacerlo aquí y no en la escritura.** El número que devuelve la lectura es **referencial**: quien decide si hay cupo es siempre `POST /v1/enrollments` → `ocuparCupo` (atómico, con `UPDATE ... WHERE cupos_libres > 0`). Un dato cacheado puede decir "queda 1" y la matrícula responder `409 SIN_CUPOS`; eso es correcto. El camino de escritura **no** pasa por el caché.

**Costos y límites declarados.**
- Consistencia eventual acotada por el TTL: con varias réplicas de la API, las demás ven el dato viejo hasta que venza el TTL (la invalidación es por proceso). Una matrícula hecha por otro canal directo a Cupos tampoco invalida el caché.
- **No** es la caché con Redis del requisito opcional O1: es un caché por proceso. No se afirma cumplir O1.
- Si Cupos está caído, las lecturas con caché vencido fallan con 503 (no se sirve un dato viejo "por si acaso").
- No se midió aún cuánto reduce las llamadas a Cupos bajo carga real; el diseño lo permite (N consultas/s → ~1/TTL llamadas/s) pero queda sin medir.
