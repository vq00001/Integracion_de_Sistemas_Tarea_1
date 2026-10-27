# D5 - Autenticación de la API REST (T6)

**Estado:** aceptada. (ADR complementario: el encargo exige cuatro ADR, D1 a D4; este documenta la decisión del requisito técnico T6, que antes solo estaba justificada en un comentario del código.)

## Contexto

La API de Matrículas es el único punto de entrada público del sistema y maneja datos personales de estudiantes (RUT, nombre, correo, teléfono). Sus consumidores previstos son sistemas de AprendeMás (la interfaz de secretaría académica y un futuro portal web), no usuarios finales anónimos de internet. El encargo exige un mecanismo de autenticación declarado en el contrato y justificado.

## Alternativas consideradas

- **Basic Auth.** Simple y estándar, pero implica un par usuario/contraseña que sugiere una identidad humana que esta API no gestiona, y las credenciales viajan en cada solicitud sin posibilidad de acotarlas por integración.
- **API Key (`X-API-Key`).** Una clave por sistema cliente: barata de verificar (sin firmas ni servidor de autorización), fácil de emitir y revocar por integración, y declarable en OpenAPI como esquema `apiKey`. No identifica a un usuario final ni permite permisos granulares.
- **Bearer / JWT (OAuth2).** Permite identidad de usuario, caducidad y *scopes*, pero exige un servidor de autorización, gestión de claves de firma y un flujo de login que hoy no existe. Es desproporcionado para integraciones máquina a máquina.

## Decisión

API Key en la cabecera `X-API-Key`, declarada en el contrato (`securitySchemes.ApiKeyAuth`) y comparada en tiempo constante, con **dos tipos de clave**:

- **Admin** (`API_KEYS`, entorno): acceso total, para secretaría e integraciones internas. Si se usa el valor predeterminado público del repositorio (`dev-matriculas-key-123`), la API registra un `WARNING` al arrancar y en cada request que la use.
- **Estudiante** (tabla `api_keys`): emitida a demanda por un admin (`POST /v1/students/{id}/api-key`), **una activa por estudiante**, guardada solo como hash SHA-256 y mostrada una única vez. Solo permite operar sobre los datos de ese estudiante: su ficha y sus matrículas.

Códigos:

- `401 Unauthorized` (con `WWW-Authenticate: ApiKey`) cuando la clave falta **o no autentica** (inexistente, inválida o revocada).
- `403 Forbidden` cuando el cliente está autenticado pero no tiene permiso: una clave de estudiante que intenta crear/listar estudiantes, ver a otro estudiante o matricular a otro. (La primera versión respondía 403 ante una clave inválida, lo que es semánticamente incorrecto.)
- `404` para una matrícula ajena identificada por id, para no confirmar a un estudiante qué ids existen.

## Justificación

El modelo de confianza sigue siendo máquina a máquina. La primera versión usaba una clave compartida que daba acceso a los datos personales de **todos** los estudiantes (RUT, correo, teléfono) a cualquiera que la tuviera; una clave filtrada exponía todo. Con una clave por estudiante, la filtración de una credencial se limita a los datos de una sola persona (principio de mínimo privilegio, relevante por tratarse de datos personales), se puede revocar sin afectar a nadie más y las matrículas quedan atribuibles a una credencial concreta. Sigue valiendo que toda ruta de negocio exige clave, que la clave no se versiona y que los errores de autenticación son uniformes.

Se guarda el hash y no el valor porque la clave es un token aleatorio de 256 bits: un hash rápido sin sal basta (no hay contraseñas débiles que atacar por diccionario) y permite buscarla por índice en cada request sin comparar contra todas.

## Costo aceptado

- Sin identidad de usuario final con login: la clave identifica a un estudiante, no a la persona que la usa; si se comparte, se comparte el acceso.
- Permisos de dos niveles (admin / dueño del estudiante), sin scopes por operación.
- El admin sigue siendo una clave compartida en una variable de entorno en texto plano; si se filtra, hay que rotarla en todos los clientes. El log de uso del valor predeterminado reduce el riesgo de olvidarla, pero no lo elimina.
- Cada request con clave de estudiante hace una consulta indexada a la base.
- La clave de estudiante no se puede recuperar: si se pierde, el admin la revoca y emite otra.
- Sin TLS en el entorno de desarrollo: la clave viajaría en claro. En producción la API debe publicarse detrás de HTTPS.

## Consecuencias

Si aparece un portal con usuarios finales, se agrega `Authorization: Bearer <JWT>` junto a `X-API-Key` sin romper a los clientes existentes. Para producción, las claves admin también deberían vivir como hash en una tabla con rotación, y publicarse detrás de TLS.
