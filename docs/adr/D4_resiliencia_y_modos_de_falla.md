# D4 - Resiliencia y modos de falla

El servicio Matrículas depende de Cupos en el camino crítico de cada matrícula: antes de registrar a un estudiante debe consultar y ocupar un cupo a través de gRPC, por lo que si Cupos no responde, responde con error o responde lentamente, la API REST de Matrículas no puede quedar esperando de forma indefinida ni fallar en silencio. Sin embargo, es necesario que la API se comporte de forma razonable ante esta falla y que el código de estado devuelto esté justificado.

El propio servicio de Cupos incorpora una variable de entorno (`SIMULAR_LATENCIA_MS`) pensada para inyectar latencia artificial, para realizar el experimento de efecto del timeout.

## Alternativas consideradas

- Esperar sin límite de tiempo y propagar el error de gRPC tal cual. No requiere lógica adicional, pero puede mantener conexiones y hebras de Matrículas bloqueadas indefinidamente, sin dar al consumidor un código de estado claro.
- Definir un tiempo límite (*deadline*) explícito en cada llamada gRPC y traducir su resultado a un código HTTP específico. Acota el tiempo de espera y permite responder de forma predecible, a costa de poder rechazar solicitudes que habrían tenido éxito si se hubiese esperado un poco más.
- Sumar a la segunda opción un *circuit breaker* y una caché de disponibilidad (Redis). Es la solución más robusta ante fallas sostenidas, pero excede el alcance mínimo de la implementación realizada, por lo que se menciona como evolución futura.

Como decisión tomada, cada llamada gRPC desde Matrículas hacia Cupos se realiza con un tiempo límite explícito. Si el plazo se agota o Cupos no está disponible, la API REST de Matrículas responde con un código de error HTTP claro en lugar de propagar el error interno de gRPC o dejar la solicitud pendiente.

Como justificación, establecer un tiempo límite por llamada acota el consumo de recursos, protege las hebras de trabajo de Matrículas y produce un comportamiento de falla determinista en lugar de uno indefinido. Sin ese límite, una dependencia lenta puede agotar el pool de conexiones o hebras disponibles y degradar a Matrículas por completo, aunque el propio servicio funcione bien.

Para el código de respuesta conviene distinguir el tipo de falla. Cuando el tiempo límite se agota porque Cupos no respondió a tiempo, el código semánticamente correcto es `504 Gateway Timeout`, reservado para cuando un servidor que actúa como intermediario no recibe una respuesta a tiempo de un servicio del que depende, a diferencia de una respuesta inválida (`502`) o de un rechazo explícito por sobrecarga (`503`). Cuando la conexión hacia Cupos directamente no puede establecerse, el código apropiado es `503 Service Unavailable`, pensado para indicar que el servidor no está en condiciones de aceptar la solicitud de forma temporal. Los errores de dominio que Cupos ya reporta de forma explícita (`NOT_FOUND` cuando el curso no existe, `FAILED_PRECONDITION` cuando una operación es inconsistente con el estado actual) no son fallas de la dependencia y deben traducirse a códigos 4xx propios de Matrículas, no a 503/504.

Como costo aceptado, un tiempo límite fijo puede rechazar solicitudes que habrían tenido éxito si Cupos hubiese respondido solo un poco más tarde. Si el límite se fija demasiado corto, aumentan las matrículas rechazadas innecesariamente. Si se fija demasiado largo, se pierde parte de la protección que el propio límite busca dar. El valor elegido debe calibrarse con datos, no fijarse arbitrariamente.

Como consecuencias, el valor del tiempo límite debe medirse empíricamente, usando `SIMULAR_LATENCIA_MS` para simular distintos escenarios de lentitud, antes de fijarse en Matrículas. Esta medición es, a su vez, insumo directo para el experimento de la Competencia 6. Esta decisión deja abierta la incorporación de una caché de disponibilidad (O1) y de un *circuit breaker* si se observan fallas sostenidas en producción.