# D2 - REST frente a gRPC

Asumiendo que Matrículas expone la única API pública del sistema, punto de entrada para el personal de la organización y para un futuro portal web, y Cupos es un servicio interno consultado con una significativamente alta frecuencia por Matrículas para verificar disponibilidad antes de cada matrícula. 

**Alternativas consideradas.**
- **REST/JSON en ambas caras.** Tecnología uniforme, de lectura amigable, fácil de testear e inspeccionar con herramientas estándar, pero con payloads de texto más grandes y sin tipado fuerte para la comunicación interna de alto volumen.
- **gRPC en ambas caras.** Contratos fuertemente tipados y mensajes binarios compactos también hacia el exterior, pero los navegadores no soportan gRPC de forma nativa y requieren una capa adicional (grpc-web) para consumirse desde un portal web, lo que complica innecesariamente la API pública.
- **REST hacia afuera, gRPC hacia adentro.** Usa cada protocolo donde sus ventajas pesan más.


Es por esto que se decidió optar por la tercera opción. Matrículas expone su API pública mediante REST sobre HTTP/JSON. Cupos expone su servicio interno mediante gRPC sobre Protocol Buffers, consumido únicamente por Matrículas.

Protocol Buffers serializa los mensajes en formato binario en lugar de texto. Una versión anterior de este documento afirmaba que migrar a gRPC "redujo el tamaño de los mensajes y mejora la latencia" citando un "caso de referencia con datos de producción" **sin fuente ni datos**; esa frase se retiró porque no era verificable. Se reemplaza por una medición propia y reproducible (`experiment/scripts/tamano_mensaje.py`, datos en `experiment/data/raw/tamano_mensaje_*.csv`), hecha con las respuestas reales del contrato de Cupos:

| Respuesta | Protobuf | JSON | JSON + gzip | JSON / Protobuf | Ida y vuelta (µs): protobuf / JSON |
|---|---|---|---|---|---|
| `obtenerCupo` | 2 B | 19 B | 39 B | 9,5× | 0,38 / 2,26 |
| `ocuparCupo` | 2 B | 25 B | 43 B | 12,5× | 0,34 / 2,19 |
| `listarCursos` (5 cursos) | 151 B | 430 B | 209 B | 2,9× | 0,71 / 7,92 |

Lectura honesta de los datos: (1) el cuerpo en protobuf es entre 3 y 12 veces más pequeño, y la diferencia **se achica con gzip** en mensajes grandes (209 B vs 151 B) y se invierte en los muy pequeños (gzip agrega cabecera: 39 B > 19 B); (2) serializar y deserializar es unas 6 a 11 veces más barato, pero son **microsegundos**: frente a los ~8 ms de un ciclo completo de matrícula medidos en el experimento 3 son irrelevantes; (3) **no se midió la latencia de extremo a extremo** (HTTP/2 y multiplexación frente a HTTP/1.1 con keep-alive), así que no se afirma una mejora de latencia. El cuello de botella medido del sistema es la escritura serializada de SQLite en Cupos, no el formato del mensaje.

Entonces, ¿por qué gRPC hacia adentro? Con la evidencia de arriba, el argumento de rendimiento es **débil** y no sostiene la decisión por sí solo. Las razones que sí se sostienen son de ingeniería: (a) el contrato `.proto` es tipado y genera cliente y servidor (los stubs se regeneran, no se escriben a mano), (b) el estado del servicio y los plazos viajan en el protocolo (códigos de estado gRPC y `deadline` propagado, que Cupos ahora usa para no confirmar operaciones abandonadas), y (c) separa con claridad la API pública (REST) de la interna. Si el volumen real no justificara esa complejidad, REST/JSON interno también habría sido defendible; se mantiene gRPC por (a)-(c) y porque ya está implementado y probado.

Además, gRPC corre sobre HTTP/2, que permite multiplexar solicitudes sobre una misma conexión, y genera automáticamente el código cliente y servidor a partir de un contrato `.proto` fuertemente tipado, lo que reduce errores de integración entre lenguajes distintos.

Del lado público, REST no exige herramientas adicionales para consumirse desde un navegador, es fácil de inspeccionar y depurar con herramientas HTTP estándar, y permite aprovechar directamente el modelo de caché de HTTP: la consulta de disponibilidad (`GET /v1/courses`) ya responde con `Cache-Control: private, max-age=<TTL>` y `X-Cache: HIT|MISS`, respaldada por un caché en memoria de TTL corto (ver D1).

A consecuencia de esta decisión se acepta mantener dos tecnologías de comunicación en el sistema en lugar de una sola, lo que implica mantener dos formas de definir y versionar contratos (OpenAPI y `.proto`) y depurar la comunicación interna con herramientas menos accesibles.

Como resultado, el contrato de Cupos se define en un archivo `.proto` versionado en el repositorio, y el de Matrículas en un archivo `openapi.yaml`. Una falla o lentitud del servicio gRPC de Cupos impacta directamente la disponibilidad de la API REST de Matrículas.

## Limitaciones declaradas
- El tamaño de mensaje se midió sobre cuatro respuestas, no sobre una carga de producción. Es una medición, no una predicción.
- La comunicación interna no usa TLS ni autenticación propias: se apoya en que el puerto gRPC de Cupos **no se publica** fuera de la red de Docker (D5). Con Cupos fuera de esa red habría que agregar mTLS.
