"""
Configuración centralizada de la API de Matrículas.

Todos los valores se leen de variables de entorno (con defaults sensatos
para desarrollo local) siguiendo la práctica de 12-factor app. Ver
`.env.example` para la lista completa de variables soportadas.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Autenticación: API Key ---
    # Lista de API keys válidas separadas por coma. En un entorno real
    # esto viviría en una tabla con hashes + rotación, no en texto plano
    # por env var; para esta tarea se mantiene simple mostrando el
    # mecanismo de forma explícita (ver README, sección "Autenticación").
    api_keys: str = "dev-matriculas-key-123"

    # --- Metadata de la app ---
    app_name: str = "Matrículas API"
    app_version: str = "1.0.0"

    @property
    def valid_api_keys(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = Settings()
