# D5 - Autenticación de la API REST (T6)

**Estado:** aceptada (revisión 3: clave admin sin valor por defecto y con arranque *fail-closed*, claves de estudiante con vencimiento e id público, límite de intentos, `Idempotency-Key` acotada por estudiante, y secciones nuevas de transporte, privacidad y operación segura). (ADR complementario: el encargo exige cuatro ADR, D1 a D4; este documenta la decisión del requisito técnico T6, que antes solo estaba justificada en un comentario del código.)

## Contexto

La API de Matrículas es el único punto de entrada público del sistema y maneja datos personales de estudiantes (RUT, nombre, correo, teléfono). Sus consumidores previstos son sistemas de AprendeMás (la interfaz de secretaría académica y un futuro portal web), no usuarios finales anónimos de internet. El encargo exige un mecanismo de autenticación declarado en el contrato y justificado.

## Alternativas consideradas

- **Basic Auth.** Simple y estándar, pero implica un par usuario/contraseña que sugiere una identidad humana que esta API no gestiona, y las credenciales viajan en cada solicitud sin posibilidad de acotarlas por integración.
- **API Key (`X-API-Key`).** Una clave por sistema cliente: barata de verificar (sin firmas ni servidor de autorización), fácil de emitir y revocar por integración, y declarable en OpenAPI como esquema `apiKey`. No identifica a un usuario final ni permite permisos granulares.
- **Bearer / JWT (OAuth2).** Permite identidad de usuario, caducidad y *scopes*, pero exige un servidor de autorización, gestión de claves de firma y un flujo de login que hoy no existe. Es desproporcionado para integraciones máquina a máquina.

## Decisión

API Key en la cabecera `X-API-Key`, declarada en el contrato (`securitySchemes.ApiKeyAuth`) y comparada en tiempo constante, con **dos tipos de clave**:

- **Admin** (`API_KEYS`, entorno): acceso total, para secretaría e integraciones internas. **No tiene valor por defecto**: si `API_KEYS` falta, es un valor público conocido (el que traía el repositorio, `dev-matriculas-key-123`, figura en una lista de prohibidos) o mide menos de 24 caracteres, **la API no arranca** (*fail-closed*; antes solo registraba un `WARNING` y seguía funcionando con una clave que cualquiera que leyera el repositorio conocía). `docker compose` tampoco levanta sin `MATRICULAS_API_KEYS`. Se genera con `python scripts/generar_env.py` (clave y contraseña de Postgres aleatorias). El `.env` está en `.gitignore` y **no debe viajar en el ZIP** (la entrega anterior lo incluía).
- **Estudiante** (tabla `api_keys`): emitida a demanda por un admin (`POST /v1/students/{id}/api-key`), **una activa por estudiante**, guardada solo como hash SHA-256 y mostrada una única vez. Solo permite operar sobre los datos de ese estudiante: su ficha y sus matrículas. Formato `mat_<id público>.<secreto>`: el id público son 8 hex **aleatorios e independientes del secreto** (antes `key_prefix` mostraba los primeros 4 caracteres del propio secreto, aunque el comentario del código decía que no revelaba nada). **Caduca** a los `STUDENT_KEY_TTL_DAYS` días (90 por defecto; `expires_at`); una clave vencida da `401`, y al emitir una nueva la vencida se revoca sola (rotación sin paso manual). Para rotar antes de tiempo: `DELETE` de la clave y emitir otra.

Códigos:

- `401 Unauthorized` (con `WWW-Authenticate: ApiKey`) cuando la clave falta **o no autentica** (inexistente, inválida o revocada).
- `403 Forbidden` cuando el cliente está autenticado pero no tiene permiso: una clave de estudiante que intenta crear/listar estudiantes, ver a otro estudiante o matricular a otro. (La primera versión respondía 403 ante una clave inválida, lo que es semánticamente incorrecto.)
- `404` para una matrícula ajena identificada por id, para no confirmar a un estudiante qué ids existen.
- `429 Too Many Requests` (con `Retry-After`): (a) una IP acumuló `AUTH_FAIL_LIMIT` (20) intentos de autenticación **fallidos** en `AUTH_FAIL_WINDOW_SECONDS` (60 s): se rechaza antes de comparar la clave, así que no regala un oráculo de fuerza bruta; (b) un estudiante con clave propia supera `WRITE_RATE_LIMIT_PER_MINUTE` (60) escrituras (matricular/revertir) por minuto: antes podía matricular y revertir en bucle sin freno. La clave admin no tiene tope de escrituras (integraciones internas de confianza). Detalles y límites en `app/ratelimit.py`: el estado es **por proceso** (con N réplicas el límite efectivo es N×), la IP es la del socket (tras un proxy hay que configurarlo para que sea la del cliente real; no se confía en `X-Forwarded-For` porque se podría falsificar para evadir el límite) y un bloqueo por IP también deja fuera a quien comparta esa IP (NAT).

## Justificación

El modelo de confianza sigue siendo máquina a máquina. La primera versión usaba una clave compartida que daba acceso a los datos personales de **todos** los estudiantes (RUT, correo, teléfono) a cualquiera que la tuviera; una clave filtrada exponía todo. Con una clave por estudiante, la filtración de una credencial se limita a los datos de una sola persona (principio de mínimo privilegio, relevante por tratarse de datos personales), se puede revocar sin afectar a nadie más y las matrículas quedan atribuibles a una credencial concreta. Sigue valiendo que toda ruta de negocio exige clave, que la clave no se versiona y que los errores de autenticación son uniformes.

Se guarda el hash y no el valor porque la clave es un token aleatorio de 256 bits: un hash rápido sin sal basta (no hay contraseñas débiles que atacar por diccionario) y permite buscarla por índice en cada request sin comparar contra todas.

## Costo aceptado

- Sin identidad de usuario final con login: la clave identifica a un estudiante, no a la persona que la usa; si se comparte, se comparte el acceso.
- Permisos de dos niveles (admin / dueño del estudiante), sin scopes por operación.
- El admin sigue siendo una clave compartida en una variable de entorno en texto plano; si se filtra, hay que rotarla en todos los clientes. Ya no puede ser un valor público ni corto, pero sigue siendo un único secreto de alto poder.
- Cada request con clave de estudiante hace una consulta indexada a la base.
- La clave de estudiante no se puede recuperar: si se pierde, el admin la revoca y emite otra.
- **Sin TLS** (ver la sección siguiente).

## Consecuencias

Si aparece un portal con usuarios finales, se agrega `Authorization: Bearer <JWT>` junto a `X-API-Key` sin romper a los clientes existentes. Para producción, las claves admin también deberían vivir como hash en una tabla con rotación, y publicarse detrás de TLS.

## Transporte: lo que NO está resuelto y por qué se acepta
- **La API habla HTTP en claro.** Una clave (y datos personales) viajan sin cifrar si se expone en una red no confiable. No se incluyó un proxy TLS porque no se pudo verificar en el entorno de desarrollo y una configuración de TLS no probada es peor que declararla ausente. **Mitigación aplicada:** el puerto 8000 se publica solo en `127.0.0.1` por defecto (`API_BIND`); exponerlo en una red exige `API_BIND=0.0.0.0` **y** poner delante un proxy inverso con TLS (nginx, Caddy o el balanceador), que además debe fijar la IP real del cliente para el límite de intentos. Es un **costo aceptado para el entorno de la entrega**, no una solución para producción.
- **El gRPC interno (Matrículas → Cupos) no tiene TLS ni autenticación.** Se apoya en aislamiento de red: el servicio `cupos` **no publica** su puerto (solo `docker-compose.test.yml`, que es para pruebas, lo publica en `127.0.0.1`). Quien alcance esa red podría llamar a Cupos directamente. Lo que **sí** cambió es que ese acceso ya no basta para sobrevender: Cupos exige que `liberarCupo` corresponda a una ocupación de esa clave, de modo que el invariante de capacidad no depende de la confianza en el llamador (D4). Con Cupos fuera de la red del compose habría que agregar mTLS o un token entre servicios.
- **`/v1/health` es público** y expone el estado del circuito hacia Cupos. Es lo que necesita un orquestador; se acepta que revele si la dependencia está degradada. **`/docs`, `/redoc` y `/openapi.json`** están habilitados por defecto (el contrato es la documentación de una API máquina a máquina); en producción se desactivan con `DOCS_ENABLED=false`.

## Datos personales: inventario, minimización, retención y supresión
La API almacena **RUT, nombre, apellido, correo y teléfono** de estudiantes y un historial de matrículas. (Es un análisis técnico del diseño, **no asesoría legal**; el marco aplicable es la Ley N° 19.628 y su reforma, la Ley N° 21.719, publicada el 13-12-2024, que según su texto entra en vigencia el 1-12-2026 —hay un proyecto en trámite que propone aplazarla a 2027—; el equipo debe confirmar con quien corresponda qué obligaciones le caben a AprendeMás.)

| Aspecto | Estado |
|---|---|
| Minimización | Solo se guardan los datos que el dominio necesita (identificar al estudiante y contactarlo). Las matrículas guardan referencias (`student_id`, `curso_id`), no copian datos personales. Los mensajes de error 422 no devuelven el valor enviado. |
| Supresión (derecho a que se eliminen los datos) | **Implementada:** `DELETE /v1/students/{id}` (solo admin) **anonimiza** al estudiante (RUT, nombre, apellido, correo y teléfono pasan a valores anónimos), revoca su clave y **conserva sus matrículas como historial sin datos personales** (no se borran, para no romper el registro de cupos). Es idempotente y no se puede deshacer. Probada en `tests/integration/test_endurecimiento.py`. |
| Retención | **No automatizada.** Hoy los datos se conservan mientras no se pida su supresión. Falta definir con AprendeMás un plazo (p. ej. N años tras el último curso) y un proceso que anonimice al vencerlo; queda como trabajo pendiente, no como funcionalidad. |
| Cifrado en reposo | **No hay cifrado a nivel de columna**: RUT, correo y teléfono están en texto plano en PostgreSQL. Cifrarlos impediría la búsqueda por RUT y la unicidad que usa la API. Se acepta; la mitigación es que la base **no se publica** fuera de la red de Docker y que el cifrado de disco/volumen corresponde a la infraestructura. |
| Acceso | Una clave de estudiante solo ve sus datos; el listado completo exige admin. Las lecturas no se registran en un log de auditoría (pendiente). |
| Entrada | Nombre/apellido y teléfono se validan contra caracteres de marcado e inyección (`< > ; " ...`) y el correo se normaliza a minúsculas (con índice único insensible a mayúsculas). Esto reduce el **XSS almacenado**, pero la defensa real es que cualquier cliente (portal) **escape al mostrar**: la API devuelve JSON y no sanea HTML. |

## Operación segura del entorno
- **Contenedores sin root** (Cupos y API usan un usuario sin privilegios; el volumen de Cupos pasó a ser de ese usuario: con un volumen creado por una versión anterior hay que recrearlo, ver el README). El ejecutor de pruebas y experimentos corre como root y **monta el socket de Docker**, lo que equivale a root en el anfitrión: por eso solo existe en los perfiles `tests` y `experimentos`, que nunca arrancan con `docker compose up`, y su limpieza destructiva (`RESET_ESTADO`) está **apagada por defecto**.
- **Versiones fijas** de dependencias Python (`requirements.txt` con `==`) y `package-lock.json` para el CLI legado (`npm ci`): construcciones reproducibles y sin sorpresas por una versión nueva. No se escaneó contra bases de vulnerabilidades (`pip-audit`/`npm audit`): queda pendiente.
- **Reinicio automático** de Cupos (`restart: unless-stopped`) y *healthcheck* de la API.
