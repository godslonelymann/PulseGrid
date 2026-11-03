from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: Literal["development", "production", "test"] = "development"
    database_url: str = "postgresql+psycopg://pulsegrid:local-development-only@localhost:55432/pulsegrid"
    redis_url: str = "redis://localhost:56379/0"
    public_origin: str = "http://localhost:3000"
    google_client_id: str = ""
    google_client_secret: str = ""
    # Compatibility for isolated legacy verification fixtures; normal installs use Google only.
    enable_legacy_password_auth: bool = False
    jwt_secret: str
    encryption_key: str
    secure_cookies: bool = False
    access_seconds: int = Field(default=300, ge=5, le=3600)
    session_days: int = Field(default=14, ge=1, le=365)
    worker_concurrency: int = 20
    lease_seconds: int = 60
    private_target_allowlist: str = ""
    raw_retention_days: int = Field(default=30, ge=1, le=3650)
    security_retention_days: int = Field(default=90, ge=1, le=3650)
    incident_retention_days: int = Field(default=365, ge=1, le=3650)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    @model_validator(mode="after")
    def validate_secrets(self):
        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        Fernet(self.encryption_key.encode())
        if self.environment == "production" and (
            not self.secure_cookies or not self.public_origin.startswith("https://")
        ):
            raise ValueError("Production requires HTTPS PUBLIC_ORIGIN and SECURE_COOKIES=true")
        if self.lease_seconds < 30 or not 1 <= self.worker_concurrency <= 100:
            raise ValueError("Invalid worker capacity or lease")
        return self


@lru_cache
def settings():
    return Settings()
