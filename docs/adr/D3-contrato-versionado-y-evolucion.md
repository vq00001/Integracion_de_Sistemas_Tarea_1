# D3 - Contrato, versionado y evolución

**Estado:** aceptada (revisada: el `package` del `.proto` ahora está versionado y se define cómo se avisa a los consumidores).

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
