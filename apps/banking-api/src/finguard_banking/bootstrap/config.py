from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del servicio. Se sobreescribe con variables BANKING_*."""

    model_config = SettingsConfigDict(env_prefix="BANKING_", env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql://finguard_banking:finguard_banking@127.0.0.1:5442/finguard_banking"
    )

    # Conexión de solo lectura para el motor de consultas (vision.md §8.6). Es un rol
    # distinto a propósito: con estas credenciales no se puede escribir ni leer una
    # tabla directamente, solo las vistas autorizadas.
    query_database_url: str = (
        "postgresql://finguard_query_reader:finguard_query_reader@127.0.0.1:5442/finguard_banking"
    )

    # Zona en la que se agrupan los informes por día, semana o mes. Con UTC, una
    # compra de las 23:30 en España caería en el día siguiente.
    reporting_timezone: str = "Europe/Madrid"


@lru_cache
def get_settings() -> Settings:
    return Settings()
