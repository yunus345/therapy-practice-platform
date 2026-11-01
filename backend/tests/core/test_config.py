import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings
from app.main import create_app

STRONG_SECRET = "x" * 32
PLACEHOLDER_SECRET = "change-me-to-a-long-random-value-please"
SAFE_URL = "postgresql+asyncpg://app:strong-password@localhost:5432/seans"


def set_valid_env(monkeypatch: pytest.MonkeyPatch, environment: Environment) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("DATABASE_URL", SAFE_URL)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", SAFE_URL)
    monkeypatch.setenv("CSRF_SECRET", STRONG_SECRET)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", STRONG_SECRET)
    monkeypatch.setenv("REQUIRE_STAFF_2FA", "true")
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://app.example.com")


def load_settings() -> Settings:
    return Settings(_env_file=None)


def test_production_accepts_strong_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    set_valid_env(monkeypatch, Environment.PRODUCTION)

    assert load_settings().environment is Environment.PRODUCTION


def test_environment_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    set_valid_env(monkeypatch, Environment.PRODUCTION)
    monkeypatch.delenv("ENVIRONMENT")

    assert load_settings().environment is Environment.PRODUCTION


@pytest.mark.parametrize(
    "name",
    ["DATABASE_URL", "MIGRATION_DATABASE_URL", "CSRF_SECRET", "TOTP_ENCRYPTION_KEY"],
)
@pytest.mark.parametrize("environment", list(Environment))
def test_missing_required_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, environment: Environment
) -> None:
    set_valid_env(monkeypatch, environment)
    monkeypatch.delenv(name)

    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.parametrize("environment", [Environment.TEST, Environment.PRODUCTION])
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CSRF_SECRET", PLACEHOLDER_SECRET),
        ("CSRF_SECRET", "too-short"),
        ("TOTP_ENCRYPTION_KEY", PLACEHOLDER_SECRET),
        ("TOTP_ENCRYPTION_KEY", "too-short"),
        ("DATABASE_URL", "postgresql+asyncpg://app:change-me@localhost/seans"),
        ("MIGRATION_DATABASE_URL", "postgresql+asyncpg://app:change-me@localhost/seans"),
    ],
)
def test_insecure_value_is_rejected_outside_dev(
    monkeypatch: pytest.MonkeyPatch, environment: Environment, name: str, value: str
) -> None:
    set_valid_env(monkeypatch, environment)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=name):
        load_settings()


def test_placeholder_values_are_allowed_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    set_valid_env(monkeypatch, Environment.DEV)
    monkeypatch.setenv("CSRF_SECRET", PLACEHOLDER_SECRET)
    monkeypatch.setenv("REQUIRE_STAFF_2FA", "false")

    assert load_settings().environment is Environment.DEV


def test_production_requires_staff_2fa(monkeypatch: pytest.MonkeyPatch) -> None:
    set_valid_env(monkeypatch, Environment.PRODUCTION)
    monkeypatch.setenv("REQUIRE_STAFF_2FA", "false")

    with pytest.raises(ValidationError, match="REQUIRE_STAFF_2FA"):
        load_settings()


def test_production_requires_https_frontend_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    set_valid_env(monkeypatch, Environment.PRODUCTION)
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://app.example.com")

    with pytest.raises(ValidationError, match="FRONTEND_ORIGIN"):
        load_settings()


def test_validation_error_does_not_contain_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_valid_env(monkeypatch, Environment.PRODUCTION)
    monkeypatch.setenv("CSRF_SECRET", "short-secret-value")

    with pytest.raises(ValidationError) as error:
        load_settings()

    assert "short-secret-value" not in str(error.value)
    assert "strong-password" not in str(error.value)


def test_settings_repr_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    set_valid_env(monkeypatch, Environment.PRODUCTION)

    settings_repr = repr(load_settings())

    assert STRONG_SECRET not in settings_repr
    assert "strong-password" not in settings_repr


def test_app_refuses_to_start_with_invalid_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_valid_env(monkeypatch, Environment.PRODUCTION)
    monkeypatch.setenv("CSRF_SECRET", PLACEHOLDER_SECRET)
    monkeypatch.setattr("app.main.get_settings", load_settings)

    with pytest.raises(ValidationError):
        create_app()


@pytest.mark.parametrize(
    ("environment", "docs_url"),
    [(Environment.DEV, "/docs"), (Environment.PRODUCTION, None)],
)
def test_docs_are_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch, environment: Environment, docs_url: str | None
) -> None:
    set_valid_env(monkeypatch, environment)
    monkeypatch.setattr("app.main.get_settings", load_settings)

    app = create_app()

    assert app.docs_url == docs_url
    assert (app.openapi_url is None) == (docs_url is None)
