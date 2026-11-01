"""Application settings, loaded from the environment and validated at startup."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

MIN_SECRET_LENGTH = 32
INSECURE_MARKER = "change-me"


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    # Defaults to production so that a missing value enables every startup check.
    environment: Environment = Environment.PRODUCTION

    database_url: SecretStr
    migration_database_url: SecretStr
    test_database_url: SecretStr | None = None

    csrf_secret: SecretStr
    totp_encryption_key: SecretStr

    require_staff_2fa: bool = False

    frontend_origin: str = "http://localhost:3000"
    log_level: LogLevel = LogLevel.INFO

    @model_validator(mode="after")
    def reject_insecure_values(self) -> Self:
        if self.environment is Environment.DEV:
            return self

        problems = [
            name
            for name, secret in (
                ("CSRF_SECRET", self.csrf_secret),
                ("TOTP_ENCRYPTION_KEY", self.totp_encryption_key),
            )
            if _is_insecure_secret(secret)
        ]
        problems += [
            name
            for name, url in (
                ("DATABASE_URL", self.database_url),
                ("MIGRATION_DATABASE_URL", self.migration_database_url),
            )
            if INSECURE_MARKER in url.get_secret_value()
        ]
        if self.environment is Environment.PRODUCTION:
            if not self.require_staff_2fa:
                problems.append("REQUIRE_STAFF_2FA")
            if not self.frontend_origin.startswith("https://"):
                problems.append("FRONTEND_ORIGIN")

        if problems:
            raise ValueError(f"Insecure settings: {', '.join(problems)}")
        return self


def _is_insecure_secret(secret: SecretStr) -> bool:
    value = secret.get_secret_value()
    return len(value) < MIN_SECRET_LENGTH or INSECURE_MARKER in value


@lru_cache
def get_settings() -> Settings:
    return Settings()
