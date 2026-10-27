"""
Configuración centralizada de la API de Matrículas.

Todos los valores se leen de variables de entorno (con defaults sensatos
para desarrollo local) siguiendo la práctica de 12-factor app. Ver
`.env.example` para la lista completa de variables soportadas.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# Valor de DESARROLLO publicado en el repositorio (.env.example, docker-compose). Cualquiera
# que lea el repo lo conoce: usarlo fuera de una máquina local equivale a no tener clave.
DEFAULT_ADMIN_API_KEY = "dev-matriculas-key-123"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Base de datos propia de Matrículas (principio: cada servicio
    # accede solo a su propia base de datos) ---
    database_url: str = "sqlite:///./matriculas.db"

    # --- gRPC: cliente hacia el servicio Cupos ---
    cupos_grpc_target: str = "localhost:50051"
    cupos_grpc_timeout_seconds: float = 2.0

    # Circuit breaker hacia Cupos
    cupos_cb_failure_threshold: int = 5       # fallas consecutivas para abrir el circuito
    cupos_cb_recovery_seconds: float = 15.0   # tiempo antes de probar de nuevo (half-open)

    # --- Matrículas pendientes (resultado incierto de Cupos, p. ej. timeout) ---
    # Una matrícula queda PENDIENTE cuando no se sabe si Cupos retuvo el cupo.
    # - Dentro de la mitad del TTL el cliente puede reintentar con la MISMA
    #   Idempotency-Key y se completa con el resultado real.
    # - Pasado el TTL, el conciliador la cierra (ANULADA) y se asegura de que
    #   no quede ningún cupo retenido. Se separan ambas ventanas para que el
    #   reintento del cliente y el conciliador nunca actúen a la vez.
    pending_ttl_seconds: float = 30.0
    # Cada cuántos segundos corre el conciliador (0 lo desactiva).
    reconcile_interval_seconds: float = 10.0

    # --- Autenticación: API Key ---
    # Hay dos tipos de credencial (ver security.py y README, sección "Autenticación"):
    #  - API key ADMIN: la(s) de esta variable (separadas por coma). Acceso total
    #    (secretaría / integraciones internas). Si se usa el valor por defecto
    #    `DEFAULT_ADMIN_API_KEY`, se deja un WARNING en el log en cada uso.
    #  - API key de ESTUDIANTE: se emiten a demanda con
    #    POST /v1/students/{id}/api-key (solo admin), se guardan hasheadas en la
    #    base y solo permiten operar sobre los datos de ese estudiante.
    api_keys: str = DEFAULT_ADMIN_API_KEY

    # --- Metadata de la app ---
    app_name: str = "Matrículas API"
    app_version: str = "1.0.0"

    @property
    def admin_api_keys(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = Settings()
