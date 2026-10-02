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

    # What the seed command loads on startup: "none", "reference" (catalog data) or
    # "full" (catalog + ~12 weeks of simulated usage for the analytics).
    seed_mode: Literal["none", "reference", "full"] = "none"

    # --- Authentication ---
    # "firebase": verify Firebase ID tokens. "dev": accept "dev:<email>" tokens (local only).
    auth_provider: Literal["firebase", "dev"] = "dev"
    # Required when AUTH_PROVIDER=firebase (Firebase console > Project settings > Project ID).
    firebase_project_id: str | None = None
    # Comma-separated list of email domains allowed to sign in.
    allowed_email_domains: str = "uniandes.edu.co"
    # Reject accounts whose email the identity provider has not verified.
    require_verified_email: bool = True

    # --- Push notifications ---
    # "none": notifications are in-app only. "fcm": also push through Firebase Cloud Messaging.
    push_provider: Literal["none", "fcm"] = "none"
    # Firebase service-account JSON (required for PUSH_PROVIDER=fcm). Keep it out of git.
    firebase_credentials_path: str | None = None

    # --- Campus ---
    # Local time zone for class schedules and "free time" calculations.
    campus_timezone: str = "America/Bogota"
    # Check-in opens this many minutes before an event starts and closes this many after it ends.
    check_in_opens_minutes: int = 30
    check_in_closes_minutes: int = 15
    # When the phone shares its location, check-in requires being this close to the venue.
    check_in_max_distance_m: float = 500

    # Run background jobs (re-engagement, recommender training) inside the API process.
    scheduler_enabled: bool = False

    # Comma-separated emails with access to analytics/operations endpoints.
    admin_emails: str = "admin@uniandes.edu.co"

    @property
    def admin_emails_list(self) -> list[str]:
        return [email.lower() for email in _split_csv(self.admin_emails)]

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
        if self.push_provider == "fcm" and not self.firebase_credentials_path:
            raise ValueError("FIREBASE_CREDENTIALS_PATH is required when PUSH_PROVIDER=fcm")
        return self


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
