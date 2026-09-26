# D2 - REST frente a gRPC

Asumiendo que Matrículas expone la única API pública del sistema, punto de entrada para el personal de la organización y para un futuro portal web, y Cupos es un servicio interno consultado con una significativamente alta frecuencia por Matrículas para verificar disponibilidad antes de cada matrícula. 

**Alternativas consideradas.**
- **REST/JSON en ambas caras.** Tecnología uniforme, de lectura amigable, fácil de testear e inspeccionar con herramientas estándar, pero con payloads de texto más grandes y sin tipado fuerte para la comunicación interna de alto volumen.
- **gRPC en ambas caras.** Contratos fuertemente tipados y mensajes binarios compactos también hacia el exterior, pero los navegadores no soportan gRPC de forma nativa y requieren una capa adicional (grpc-web) para consumirse desde un portal web, lo que complica innecesariamente la API pública.
- **REST hacia afuera, gRPC hacia adentro.** Usa cada protocolo donde sus ventajas pesan más.


Es por esto que se decidió optar por la tercera opción. Matrículas expone su API pública mediante REST sobre HTTP/JSON. Cupos expone su servicio interno mediante gRPC sobre Protocol Buffers, consumido únicamente por Matrículas.

Protocol Buffers serializa los mensajes en formato binario en lugar de texto, lo que produce payloads considerablemente más pequeños que su equivalente en JSON y acelera la serialización y deserialización en ambos extremos de la comunicación. En un caso de referencia con datos de producción, migrar de REST/JSON a gRPC con Protobuf sobre la misma carga de trabajo redujo el tamaño de los mensajes y mejora la latencia. Además, gRPC corre sobre HTTP/2, que permite multiplexar solicitudes sobre una misma conexión, y genera automáticamente el código cliente y servidor a partir de un contrato `.proto` fuertemente tipado, lo que reduce errores de integración entre lenguajes distintos. Estas características son las que permiten a Cupos responder con baja latencia al alto volumen de consultas que recibe de Matrículas.

Del lado público, REST no exige herramientas adicionales para consumirse desde un navegador, es fácil de inspeccionar y depurar con herramientas HTTP estándar, y permite aprovechar directamente el modelo de caché de HTTP si en el futuro se agrega una capa de caché sobre las consultas de disponibilidad.

A consecuencia de esta decisión se acepta mantener dos tecnologías de comunicación en el sistema en lugar de una sola, lo que implica mantener dos formas de definir y versionar contratos (OpenAPI y `.proto`) y depurar la comunicación interna con herramientas menos accesibles.

Como resultado, el contrato de Cupos se define en un archivo `.proto` versionado en el repositorio, y el de Matrículas en un archivo `openapi.yaml`. Una falla o lentitud del servicio gRPC de Cupos impacta directamente la disponibilidad de la API REST de Matrículas.
