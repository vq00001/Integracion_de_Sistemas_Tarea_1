# D1 - Estilo de integración y descomposición

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
