import io
import json
import logging
from email.message import EmailMessage
from typing import Any

import httpx
import pytest
from cryptography.fernet import InvalidToken
from fastapi import FastAPI

from app.core.config import Environment
from app.core.logging import configure_logging
from app.core.security import (
    SecretBox,
    create_csrf_token,
    hash_password,
    verify_csrf_token,
    verify_password,
)
from app.modules.notifications.service import Email, EmailTemplate, SmtpMailer, render
from tests.conftest import (
    DEFAULT_PASSWORD,
    ClientFactory,
    FakeMailer,
    SettingsFactory,
    log_in,
)

SECRET = "x" * 32


async def test_password_hash_roundtrip() -> None:
    password_hash = await hash_password("correct horse")

    assert password_hash.startswith("$argon2id$")
    assert await verify_password(password_hash, "correct horse") is True
    assert await verify_password(password_hash, "wrong") is False


async def test_missing_or_invalid_hash_never_verifies() -> None:
    assert await verify_password(None, "anything") is False
    assert await verify_password("not-a-hash", "anything") is False


def test_secret_box_roundtrip_and_key_separation() -> None:
    box = SecretBox(SECRET)
    encrypted = box.encrypt("value")

    assert "value" not in encrypted
    assert box.decrypt(encrypted) == "value"
    with pytest.raises(InvalidToken):
        SecretBox("y" * 32).decrypt(encrypted)
    assert box.keyed_hash("value") != SecretBox("y" * 32).keyed_hash("value")


def test_csrf_token_is_bound_to_binding_and_secret() -> None:
    token = create_csrf_token(SECRET, "session-a")

    assert verify_csrf_token(SECRET, "session-a", token) is True
    assert verify_csrf_token(SECRET, "session-b", token) is False
    assert verify_csrf_token("y" * 32, "session-a", token) is False
    assert verify_csrf_token(SECRET, "session-a", "malformed") is False


@pytest.mark.parametrize("template", list(EmailTemplate))
def test_every_email_template_renders(template: EmailTemplate) -> None:
    subject, body = render(Email(to="a@example.com", template=template, link="https://x/y"))

    assert subject.startswith("Seans")
    assert "$" not in body


async def test_smtp_mailer_sends_rendered_message(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[EmailMessage, dict[str, Any]]] = []

    async def fake_send(message: EmailMessage, **options: Any) -> None:
        sent.append((message, options))

    monkeypatch.setattr("aiosmtplib.send", fake_send)

    await SmtpMailer(make_settings()).send(
        Email(to="a@example.com", template=EmailTemplate.VERIFY_EMAIL, link="https://x/y")
    )

    message, options = sent[0]
    assert message["To"] == "a@example.com"
    assert "https://x/y" in message.get_content()
    assert options["hostname"] == "localhost"
    assert options["port"] == 1025


async def test_smtp_failure_is_logged_without_the_recipient(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_send(*_: Any, **__: Any) -> None:
        raise OSError("connection refused for a@example.com")

    monkeypatch.setattr("aiosmtplib.send", failing_send)
    stream = io.StringIO()
    settings = make_settings(
        environment=Environment.TEST,
        database_url="postgresql+asyncpg://app:strong@localhost/seans",
        migration_database_url="postgresql+asyncpg://owner:strong@localhost/seans",
    )
    configure_logging(settings, stream)

    await SmtpMailer(settings).send(Email(to="a@example.com", template=EmailTemplate.INVITATION))

    assert "email_failed" in stream.getvalue()
    assert "a@example.com" not in stream.getvalue()


async def test_auth_flows_do_not_log_personal_data_or_secrets(
    api_app: FastAPI,
    new_client: ClientFactory,
    mailer: FakeMailer,
    make_settings: SettingsFactory,
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
    logging.getLogger().setLevel(logging.DEBUG)
    email = "private.person@example.com"
    client: httpx.AsyncClient = await new_client()

    await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": DEFAULT_PASSWORD,
            "first_name": "Secretfirst",
            "last_name": "Secretlast",
            "phone": "+905321234567",
            "kvkk_accepted": True,
        },
    )
    token = mailer.last_token(email)
    await client.post("/auth/verify-email", json={"token": token})
    await log_in(client, email, "wrong-password-value")
    await log_in(client, email)
    session_token = client.cookies["seans_session"]
    await client.post("/auth/password-reset/request", json={"email": email})
    await client.post("/auth/logout")

    logged = stream.getvalue()
    assert [json.loads(line) for line in logged.splitlines()]
    for secret in (
        email,
        DEFAULT_PASSWORD,
        "wrong-password-value",
        "Secretfirst",
        "Secretlast",
        "+905321234567",
        token,
        session_token,
    ):
        assert secret not in logged
