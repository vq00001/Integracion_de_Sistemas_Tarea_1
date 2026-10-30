# Datos archivados (anteriores a la revisión)

Estos CSV y las figuras de `../../results/archivo_rev/` se generaron con la versión del código
ANTERIOR a las correcciones de la revisión 3 y **ya no son comparables** con el sistema actual:

- `timeout2s_*.csv`: el circuit breaker se quedó con umbral 1000 (heredado de otro experimento), así
  que nunca abrió; el control 404 estaba mezclado con las mediciones; las latencias medidas fueron
  ~92-94 % del retraso inyectado (deriva de reloj sin calibrar).
- `consistencia_*.csv`: Cupos confirmaba la operación aunque el cliente ya se hubiera ido (por eso
  aparecían huérfanos en todas las corridas) y el conciliador hacía ocupar+liberar con TTL 6 s.
- `throughput_*.csv`: una sola repetición, sin registro del equipo.

Se conservan solo como evidencia histórica. Los datos vigentes se regeneran con
`python experiment/ejecutar_todo.py` y quedan en `../raw/`.
