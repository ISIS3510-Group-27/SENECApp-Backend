from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SENECApp Backend"
    app_env: str = "local"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "*"

    database_url: str = "postgresql+psycopg://senecapp:senecapp@localhost:5433/senecapp"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
