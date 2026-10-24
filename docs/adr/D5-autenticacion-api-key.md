# D5 - Autenticación de la API REST (T6)

**Estado:** aceptada. (ADR complementario: el encargo exige cuatro ADR, D1 a D4; este documenta la decisión del requisito técnico T6, que antes solo estaba justificada en un comentario del código.)

## Contexto

La API de Matrículas es el único punto de entrada público del sistema y maneja datos personales de estudiantes (RUT, nombre, correo, teléfono). Sus consumidores previstos son sistemas de AprendeMás (la interfaz de secretaría académica y un futuro portal web), no usuarios finales anónimos de internet. El encargo exige un mecanismo de autenticación declarado en el contrato y justificado.

## Alternativas consideradas

- **Basic Auth.** Simple y estándar, pero implica un par usuario/contraseña que sugiere una identidad humana que esta API no gestiona, y las credenciales viajan en cada solicitud sin posibilidad de acotarlas por integración.
- **API Key (`X-API-Key`).** Una clave por sistema cliente: barata de verificar (sin firmas ni servidor de autorización), fácil de emitir y revocar por integración, y declarable en OpenAPI como esquema `apiKey`. No identifica a un usuario final ni permite permisos granulares.
- **Bearer / JWT (OAuth2).** Permite identidad de usuario, caducidad y *scopes*, pero exige un servidor de autorización, gestión de claves de firma y un flujo de login que hoy no existe. Es desproporcionado para integraciones máquina a máquina.

## Decisión

API Key en la cabecera `X-API-Key`, declarada en el contrato (`securitySchemes.ApiKeyAuth`), con una clave por sistema cliente, comparada en tiempo constante. Los códigos son:

- `401 Unauthorized` (con `WWW-Authenticate: ApiKey`) cuando la clave falta **o no autentica** (inexistente o inválida).
- `403 Forbidden` queda reservado para un cliente autenticado sin permiso para una operación. Hoy no hay roles, así que la API no responde 403. (La primera versión respondía 403 ante una clave inválida, lo que es semánticamente incorrecto: el servidor no sabe quién es el cliente.)

## Justificación

El modelo de confianza es máquina a máquina, igual que la relación entre Matrículas y Cupos. Una clave por integración permite auditar y revocar a un cliente sin afectar a los demás. El factor de privacidad (ver "Consideraciones de contexto" del informe) pesó en tres decisiones relacionadas: toda ruta de negocio exige la clave, la clave no se versiona en el repositorio, y los errores de autenticación son uniformes y no revelan información.

## Costo aceptado

- Sin identidad de usuario final: no se sabe qué persona hizo una matrícula, solo qué sistema.
- Sin permisos granulares: cualquier clave válida puede ejecutar todas las operaciones, incluida la lectura de datos personales de todos los estudiantes.
- Las claves viven en una variable de entorno en texto plano; una clave filtrada exige rotarla en todos los clientes que la usan.
- Sin TLS en el entorno de desarrollo: la clave viajaría en claro. En producción la API debe publicarse detrás de HTTPS.

## Consecuencias

Si aparece un portal con usuarios finales, se agrega `Authorization: Bearer <JWT>` junto a `X-API-Key` sin romper a los clientes existentes, y recién ahí tendría sentido introducir roles y el código 403. Para producción, las claves deberían guardarse como hash en una tabla con rotación, y publicarse detrás de TLS.
