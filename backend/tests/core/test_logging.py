import io
import json
import logging
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI

from app.core.config import Environment
from app.core.logging import REDACTED, configure_logging, redact_sensitive_data
from tests.conftest import SettingsFactory


def redact(**event: Any) -> dict[str, Any]:
    return dict(redact_sensitive_data(None, "info", event))


@pytest.fixture
def json_log(make_settings: SettingsFactory) -> io.StringIO:
    stream = io.StringIO()
    configure_logging(
        make_settings(
            environment=Environment.TEST,
            database_url="postgresql+asyncpg://app:strong@localhost/seans",
            migration_database_url="postgresql+asyncpg://owner:strong@localhost/seans",
        ),
        stream,
    )
    return stream


def log_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "new_password",
        "password_hash",
        "token",
        "session_token",
        "authorization",
        "cookie",
        "email",
        "phone",
        "first_name",
        "last_name",
        "birth_date",
        "ip_address",
        "totp_secret",
        "csrf_token",
        "session_notes",
        "cancellation_reason",
    ],
)
def test_sensitive_keys_are_redacted(key: str) -> None:
    assert redact(event="x", **{key: "value"})[key] == REDACTED


def test_nested_values_are_redacted() -> None:
    result = redact(event="x", payload={"user": [{"email": "a@b.co", "id": 1}]})

    assert result["payload"] == {"user": [{"email": REDACTED, "id": 1}]}


@pytest.mark.parametrize(
    "text",
    [
        "login failed for ayse.yilmaz@example.com.tr",
        "client phone +90 532 123 45 67",
        "client phone 05321234567",
        "header Bearer abc.def-123",
    ],
)
def test_sensitive_patterns_in_text_are_redacted(text: str) -> None:
    result = redact(event=text)

    assert REDACTED in result["event"]
    for fragment in ("ayse.yilmaz", "532", "abc.def"):
        assert fragment not in result["event"]


def test_uuids_and_timestamps_are_not_redacted() -> None:
    values = {
        "user_id": "3f2b8a4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b",
        "occurred_at": "2026-11-02T10:15:30.123456+00:00",
        "status_code": 200,
    }

    assert redact(event="x", **values) == {"event": "x", **values}


def test_output_is_json_with_bound_request_id(json_log: io.StringIO) -> None:
    with structlog.contextvars.bound_contextvars(request_id="abc"):
        structlog.get_logger("test").info("something_happened", user_id="123")

    line = log_lines(json_log)[0]
    assert line["event"] == "something_happened"
    assert line["request_id"] == "abc"
    assert line["user_id"] == "123"
    assert line["level"] == "info"


def test_standard_library_logs_are_also_redacted(json_log: io.StringIO) -> None:
    logging.getLogger("third_party").warning("mail failed for user@example.com")

    assert "user@example.com" not in json_log.getvalue()
    assert log_lines(json_log)[0]["event"] == f"mail failed for {REDACTED}"


def test_exception_messages_are_dropped_from_logs(json_log: io.StringIO) -> None:
    try:
        try:
            raise ValueError("Key (full_text)=(Ayşe) already exists")
        except ValueError as error:
            raise RuntimeError("wrapped Ayşe") from error
    except RuntimeError as error:
        structlog.get_logger("test").error("failed", exc_info=error)

    assert "Ayşe" not in json_log.getvalue()
    line = log_lines(json_log)[0]
    assert line["exception_type"] == "RuntimeError <- ValueError"
    assert "test_logging.py" in line["stack"][0]


async def test_access_log_uses_route_template_without_query_string(
    json_log: io.StringIO, app: FastAPI, client: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    stream = io.StringIO()
    configure_logging(
        make_settings(
            environment=Environment.TEST,
            database_url="postgresql+asyncpg://app:strong@localhost/seans",
            migration_database_url="postgresql+asyncpg://owner:strong@localhost/seans",
        ),
        stream,
    )

    @app.get("/clients/{client_slug}")
    async def read_client(client_slug: str) -> dict[str, str]:
        return {"slug": client_slug}

    response = await client.get("/clients/ayse-yilmaz?token=secret-token")
    await client.get("/unknown/ayse-yilmaz")

    lines = [line for line in log_lines(stream) if line["event"] == "request_completed"]
    assert lines[0]["route"] == "/clients/{client_slug}"
    assert lines[0]["status_code"] == 200
    assert lines[0]["request_id"] == response.headers["X-Request-ID"]
    assert lines[1]["route"] == "unmatched"
    assert "ayse-yilmaz" not in stream.getvalue()
    assert "secret-token" not in stream.getvalue()


async def test_unhandled_error_log_has_type_but_no_message(
    app: FastAPI, client: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    stream = io.StringIO()
    configure_logging(
        make_settings(
            environment=Environment.TEST,
            database_url="postgresql+asyncpg://app:strong@localhost/seans",
            migration_database_url="postgresql+asyncpg://owner:strong@localhost/seans",
        ),
        stream,
    )

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    await client.get("/boom")

    assert "secret internal detail" not in stream.getvalue()
    errors = [line for line in log_lines(stream) if line["event"] == "unhandled_error"]
    assert errors[0]["exception_type"] == "RuntimeError"
