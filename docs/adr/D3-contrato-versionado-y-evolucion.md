# D3 - Contrato, versionado y evolución

**Estado:** aceptada (revisión 3: la compatibilidad ahora se **verifica automáticamente**, la política de deprecación está implementada como código y se documenta la convención de errores y la deuda de estilo del `.proto`).

Dado que a medida que Cupos y Matrículas sigan creciendo, será necesario modificar sus contratos: agregar campos, cambiar operaciones o corregir el modelo de datos. Sin una estrategia de versionado, un cambio en cualquiera de los dos contratos puede romper a los consumidores existentes sin aviso.

**Alternativas consideradas.**
- **Sin estrategia de versionado formal.** Se descarta porque no da garantía de compatibilidad ni forma de revertir un cambio problemático.
- **Versionar el contrato completo ante cualquier cambio.** Es la opción más segura, pero impone a todos los consumidores una migración incluso cuando el cambio era compatible en la práctica.
- **Versionado explícito de la API REST combinado con las reglas de compatibilidad binaria de Protocol Buffers para el contrato gRPC.** Reserva el versionado mayor para cambios incompatibles y permite evolucionar el contrato sin romperlo mientras el cambio respete esas reglas.


Se decidió versionar explícitamente la URL de la API REST de Matrículas (`/v1`, y en el futuro `/v2` de ser necesario). El contrato `.proto` de Cupos evoluciona siguiendo las reglas de compatibilidad binaria propias de Protocol Buffers [protoguide], reservando una nueva versión del paquete solo para cambios incompatibles. El paquete del contrato actual es `cupo.v1` (`package cupo.v1;` en `proto/cupos.proto`): el nombre completo del servicio, `/cupo.v1.Cupos/...`, viaja en cada llamada, de modo que un cliente `v1` y un servidor `v2` nunca se confunden y ambas versiones pueden convivir durante una migración.

Ya que Protocol Buffers fue diseñado explícitamente para permitir que un contrato se extienda con información nueva sin invalidar los datos existentes ni forzar una actualización simultánea de todo el código que lo consume. Esto es posible porque cada campo de un mensaje se identifica por un número, no por su posición ni su nombre, y ese número --- no el campo en sí --- es lo que define la compatibilidad binaria del contrato.

Son cambios compatibles los que no requieren de una nueva versión del contrato, como:
- Agregar un campo nuevo con un número de campo no utilizado previamente, de forma que los lectores antiguos puedan simplemente ignorarlo.    
- Eliminar un campo que ya no se usa, siempre que su número se marque como `reserved` para que no vuelva a reutilizarse por error.
- Ampliar el rango de un tipo numérico (por ejemplo, de `int32` a `int64`), mientras los valores existentes permanezcan dentro del rango del tipo original.
- Agregar nuevos valores a una enumeración, como los estados definidos en `cupos.proto` (`CUPO_OCUPADO`, `SIN_CUPOS`, `CURSO_NO_ENCONTRADO`, `CUPO_LIBERADO`): un estado adicional futuro no rompe a los clientes existentes.


Son cambios incompatibles los que exigen una nueva versión del contrato: reutilizar o reasignar el número de un campo eliminado a un campo distinto, ya que para el receptor el dato antiguo y el nuevo son indistinguibles en el mensaje binario, cambiar el tipo de un campo de forma incompatible con el original, y eliminar o renombrar un método RPC que los clientes existentes siguen invocando.

Del lado de la API REST, el versionado por URL (`/v1`) exigido por el encargo cumple el mismo propósito de forma explícita y visible para cualquier consumidor externo.

El mantener compatibilidad binaria en `.proto` exige que cada campo eliminado deba reservarse en lugar de simplemente borrarse, y cada cambio debe evaluarse contra estas reglas antes de integrarse. Es por esto que un cambio compatible se adopta simplemente regenerando el código cliente contra la versión más reciente del `.proto`. Un cambio incompatible se comunica mediante una nueva versión explícita del contrato, documentada en el repositorio junto al historial de commits. Ambos contratos (`openapi.yaml` y `.proto`) se mantienen versionados en el repositorio como fuente de verdad.

## Ejemplo de evolución: agregar un campo

Si mañana se necesita que `ObtenerResponse` informe también `cupos_totales`, se agrega `int32 cupos_totales = 2;` (número nuevo, nunca usado) dentro de `cupo.v1`. Los clientes antiguos ignoran el campo; los nuevos lo leen. Es un cambio compatible: no hay versión nueva, solo se regeneran los stubs. En cambio, cambiar `curso_id` de `int32` a `string` es incompatible y exige `cupo.v2` con ambas versiones en paralelo.

**Matiz sobre `int32` → `int64`.** En el cable de protobuf el cambio es compatible mientras los valores quepan en 32 bits, pero **no lo es en cualquier cliente**: el mapeo a JSON de protobuf serializa `int64` como *string*, y algunos lenguajes (p. ej. JavaScript) no representan `int64` sin pérdida. Por eso no basta con "el cable lo permite": se evalúa también cómo lo consume cada cliente. (La regla de la lista anterior debe leerse con este matiz.)

## Cambios en el contrato REST (`/v1`)

Esta revisión modificó el contrato público sin crear `/v2`. Cada cambio se clasificó así:

| Cambio | Clasificación | Justificación |
|---|---|---|
| Nueva cabecera opcional `Idempotency-Key` en `POST /v1/enrollments` | Compatible | Un cliente que no la envía se comporta como antes. |
| Nuevos códigos de respuesta `502` y `504` | Compatible | Eran situaciones que antes se agrupaban en `503`; un cliente que trata todo `5xx` como reintentable no se rompe. |
| Clave de API inválida: `403` → `401` | Cambio de comportamiento, corregido antes de tener consumidores externos | `403` era semánticamente incorrecto (RFC 9110). Se hizo ahora porque aún no existían clientes de producción; con clientes, habría exigido `/v2`. |
| Nuevo valor `PENDIENTE` en el estado de una matrícula | Compatible **solo si los clientes toleran valores de enumeración desconocidos** | Es el punto de mayor riesgo: se documenta en el OpenAPI y en este ADR. |
| RUT validado (formato y dígito verificador) | Restrictivo | Rechaza con `422` valores que antes se aceptaban pero eran inválidos. |

## Cómo se enteran los consumidores

1. **El contrato es el aviso.** `proto/cupos.proto` y `matriculas-api/docs/openapi.{json,yaml}` se versionan en el repositorio; una prueba de contrato falla si el OpenAPI versionado difiere del que publica la API, así que el archivo nunca queda desactualizado.
2. **Registro de cambios.** Todo cambio al contrato se anota en la sección "Historial de versiones del contrato" del README, con su clasificación (compatible/incompatible) y la fecha.
3. **Cambios incompatibles: período de convivencia.** Se publica la versión nueva (`/v2` o `cupo.v2`) junto a la anterior. La anterior se marca como obsoleta en el OpenAPI (`deprecated: true`) y, en REST, se agregan las cabeceras `Deprecation` y `Sunset` a sus respuestas; se retira solo después de la fecha anunciada.
4. **Consumidores conocidos.** Hoy el único consumidor interno del `.proto` es Matrículas y el equipo es el mismo; si aparecen consumidores externos, se mantiene una lista de contactos por contrato para avisar antes de publicar.

## Historial de versiones del contrato

| Versión | Fecha | `.proto` (`cupo.v1`) | REST (`/v1`) | Clasificación |
|---|---|---|---|---|
| 1.0.0 | 2026-10-21 | `obtenerCupo`, `listarCursos`, `listarCursosDisponibles`, `ocuparCupo`, `liberarCupo` | Estudiantes, matrículas, API keys, health | Línea base (`contrato-base/`) |
| 1.1.0 | 2026-10-29 | **RPC nuevo** `consultarOperacion` (+ mensajes `ConsultarOperacion*`) | **Rutas nuevas** `GET /v1/courses`, `GET /v1/courses/{id}`, `DELETE /v1/students/{id}`; `expires_at` en `ApiKeyInfo`/`ApiKeyIssued`; códigos `409 ENROLLMENT_ANNULLED`, `429 TOO_MANY_REQUESTS`; `offset` con máximo 100 000 | Aditivo y verificado por `scripts/verificar_compatibilidad.py` (ver abajo los **cambios de comportamiento**, que ninguna herramienta de esquema ve) |

### Cambios de comportamiento de la 1.1.0 (no son cambios de esquema)
El verificador compara esquemas; estos cambios no alteran ni el `.proto` ni el OpenAPI, así que se clasifican a mano:

| Cambio | Clasificación | Justificación |
|---|---|---|
| `liberarCupo` solo libera si **esa clave ocupó ese curso**; si no, `FAILED_PRECONDITION` | Restrictivo, **corrige un defecto de seguridad** | Antes bastaba con que el curso tuviera algún cupo ocupado: una clave inventada devolvía un cupo ajeno y permitía sobreventa. El único consumidor (Matrículas) ya liberaba con la clave con que ocupó, así que no se rompe. |
| `ocuparCupo`/`liberarCupo` con `idempotency_key` vacía → `INVALID_ARGUMENT` | Restrictivo | Sin clave la operación no era idempotente. Matrículas siempre envía clave. |
| `ocuparCupo` con una clave ya usada para otro curso → `INVALID_ARGUMENT` (antes `UNKNOWN` por choque de clave primaria) | Compatible (mejora el código de error) | |
| Cupos no confirma si el *deadline* del cliente ya venció | Compatible | Solo evita ocupaciones que el cliente ya no esperaba. |
| Repetir la `Idempotency-Key` de una matrícula **anulada**: `201` con estado `ANULADA` → `409 ENROLLMENT_ANNULLED` | Cambio de comportamiento | Un `201` con estado anulado parecía un éxito. Corregido antes de tener consumidores externos. |
| La `Idempotency-Key` es única **por estudiante** (antes global) | Compatible para quien ya la usaba bien | Elimina un oráculo (un estudiante podía saber si otro usó una clave). |
| Nombres y teléfono validados (rechazan `< > ; "` y formatos no telefónicos); correo normalizado a minúsculas | Restrictivo | Igual que la validación del RUT en la revisión anterior: antes se aceptaban valores inválidos o peligrosos. |
| Formato de clave de estudiante: `mat_<id>.<secreto>` (antes `mat_<secreto>`), con vencimiento | Compatible | Las claves ya emitidas siguen funcionando (se validan por hash). El valor de `key_prefix` cambia de significado (ahora es un id público, ya no parte del secreto). |

## Verificación automática de compatibilidad
Antes, "compatible" era una definición en este documento y nada la comprobaba. Ahora:
- `contrato-base/` guarda el último contrato **publicado** (descriptor del `.proto` y `openapi.base.json`).
- `python scripts/verificar_compatibilidad.py` compara el contrato actual con esa base y termina con código 1 si hay un cambio que rompe (campo, RPC, ruta o código de respuesta eliminado; propiedad obligatoria nueva en una petición; cambio de tipo; valor de enum eliminado). Las restricciones que se estrechan (p. ej. el `maximum` de `offset`) se reportan como **aviso**.
- `tests/unit/test_compatibilidad.py` ejecuta la comprobación en la suite y, además, la prueba con rupturas **simuladas** para que no sea un verde vacío.
- Al publicar una versión: `python scripts/verificar_compatibilidad.py --actualizar-base`.
- Es una herramienta propia y acotada (no `buf breaking` ni `oasdiff`): cubre las reglas de arriba, no todo. Si el contrato crece, conviene reemplazarla por esas herramientas.

## Convención de errores del contrato gRPC
Un resultado **de negocio esperable** viaja en el enum `Estado` (`SIN_CUPOS`, `CURSO_NO_ENCONTRADO`); una **precondición violada** (`liberarCupo` de lo que esa clave no ocupó) o un **argumento inválido** (clave vacía o usada con otro curso) viaja como código de estado gRPC (`FAILED_PRECONDITION`, `INVALID_ARGUMENT`). Una excepción histórica: `obtenerCupo` responde `NOT_FOUND` para un curso inexistente (en cambio `ocuparCupo` usa el enum). No se corrige dentro de `v1` porque cambiarlo rompe a los clientes; queda para `cupo.v2`.

## Deuda de estilo del `.proto` (a corregir en `cupo.v2`)
Los nombres de los RPC están en `lowerCamelCase` (`obtenerCupo`) y los valores del enum no llevan el prefijo del tipo (`CUPO_OCUPADO` en vez de `ESTADO_CUPO_OCUPADO`), contra la guía de estilo de protobuf (RPC en `PascalCase`; valores con prefijo). Renombrarlos **rompe** el contrato (cambia el nombre del método en el cable), así que no se hace en `v1`. Es deuda consciente: se agrupa con las demás correcciones para una única migración a `cupo.v2` en vez de romper dos veces.

## Política de deprecación, implementada
La sección "Cómo se enteran los consumidores" prometía las cabeceras `Deprecation` y `Sunset`; hasta la revisión 3 no existían. Ahora la variable `DEPRECATED_ROUTES` (JSON: ruta → `deprecation`, `sunset`, `link`) hace que la API las agregue a las respuestas de esas rutas (middleware en `app/main.py`, con prueba en `tests/unit/test_api_unidad.py`). Hoy no hay rutas deprecadas, así que la lista está vacía.
