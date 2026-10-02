from functools import lru_cache
from typing import Literal

from pydantic import model_validator
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

    # What the seed command loads on startup: "none" or "reference" (catalog data).
    seed_mode: Literal["none", "reference"] = "none"

    # --- Authentication ---
    # "firebase": verify Firebase ID tokens. "dev": accept "dev:<email>" tokens (local only).
    auth_provider: Literal["firebase", "dev"] = "dev"
    # Required when AUTH_PROVIDER=firebase (Firebase console > Project settings > Project ID).
    firebase_project_id: str | None = None
    # Comma-separated list of email domains allowed to sign in.
    allowed_email_domains: str = "uniandes.edu.co"
    # Reject accounts whose email the identity provider has not verified.
    require_verified_email: bool = True

    @property
    def cors_origins_list(self) -> list[str]:
        return _split_csv(self.cors_origins)

    @property
    def allowed_email_domains_list(self) -> list[str]:
        return [domain.lower() for domain in _split_csv(self.allowed_email_domains)]

    @model_validator(mode="after")
    def check_auth_settings(self) -> "Settings":
        if self.app_env == "production" and self.auth_provider == "dev":
            raise ValueError("AUTH_PROVIDER=dev is not allowed when APP_ENV=production")
        if self.auth_provider == "firebase" and not self.firebase_project_id:
            raise ValueError("FIREBASE_PROJECT_ID is required when AUTH_PROVIDER=firebase")
        return self


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
