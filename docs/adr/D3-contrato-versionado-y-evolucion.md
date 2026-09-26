# D3 - Contrato, versionado y evolución

Dado que a medida que Cupos y Matrículas sigan creciendo, será necesario modificar sus contratos: agregar campos, cambiar operaciones o corregir el modelo de datos. Sin una estrategia de versionado, un cambio en cualquiera de los dos contratos puede romper a los consumidores existentes sin aviso.

**Alternativas consideradas.**
- **Sin estrategia de versionado formal.** Se descarta porque no da garantía de compatibilidad ni forma de revertir un cambio problemático.
- **Versionar el contrato completo ante cualquier cambio.** Es la opción más segura, pero impone a todos los consumidores una migración incluso cuando el cambio era compatible en la práctica.
- **Versionado explícito de la API REST combinado con las reglas de compatibilidad binaria de Protocol Buffers para el contrato gRPC.** Reserva el versionado mayor para cambios incompatibles y permite evolucionar el contrato sin romperlo mientras el cambio respete esas reglas.


Se decidió versionar explícitamente la URL de la API REST de Matrículas (`/v1`, y en el futuro `/v2` de ser necesario). El contrato `.proto` de Cupos evoluciona siguiendo las reglas de compatibilidad binaria propias de Protocol Buffers [protoguide], reservando una nueva versión del servicio o del paquete solo para cambios incompatibles.

Ya que Protocol Buffers fue diseñado explícitamente para permitir que un contrato se extienda con información nueva sin invalidar los datos existentes ni forzar una actualización simultánea de todo el código que lo consume. Esto es posible porque cada campo de un mensaje se identifica por un número, no por su posición ni su nombre, y ese número --- no el campo en sí --- es lo que define la compatibilidad binaria del contrato.

Son cambios compatibles los que no requieren de una nueva versión del contrato, como:
- Agregar un campo nuevo con un número de campo no utilizado previamente, de forma que los lectores antiguos puedan simplemente ignorarlo.    
- Eliminar un campo que ya no se usa, siempre que su número se marque como `reserved` para que no vuelva a reutilizarse por error.
- Ampliar el rango de un tipo numérico (por ejemplo, de `int32` a `int64`), mientras los valores existentes permanezcan dentro del rango del tipo original.
- Agregar nuevos valores a una enumeración, como los estados definidos en `cupos.proto` (`CUPO\_OCUPADO`, `SIN\_CUPOS`, `CURSO\_NO\_ENCONTRADO`, `CUPO\_LIBERADO`): un estado adicional futuro no rompe a los clientes existentes.


Son cambios incompatibles los que exigen una nueva versión del contrato: reutilizar o reasignar el número de un campo eliminado a un campo distinto, ya que para el receptor el dato antiguo y el nuevo son indistinguibles en el mensaje binario, cambiar el tipo de un campo de forma incompatible con el original, y eliminar o renombrar un método RPC que los clientes existentes siguen invocando.

Del lado de la API REST, el versionado por URL (`/v1`) exigido por el encargo cumple el mismo propósito de forma explícita y visible para cualquier consumidor externo.

El mantener compatibilidad binaria en `.proto` exige que cada campo eliminado deba reservarse en lugar de simplemente borrarse, y cada cambio debe evaluarse contra estas reglas antes de integrarse. Es por esto que un cambio compatible se adopta simplemente regenerando el código cliente contra la versión más reciente del `.proto`. Un cambio incompatible se comunica mediante una nueva versión explícita del contrato, documentada en el repositorio junto al historial de commits. Ambos contratos (`openapi.yaml` y `.proto`) se mantienen versionados en el repositorio como fuente de verdad.
