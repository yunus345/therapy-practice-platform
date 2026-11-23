"""Email delivery. Messages are sent after the response, outside the transaction."""

from dataclasses import dataclass
from email.message import EmailMessage
from enum import StrEnum
from pathlib import Path
from string import Template
from typing import Protocol

import aiosmtplib
import structlog
from fastapi import BackgroundTasks

from app.core.config import Settings

logger = structlog.get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


class EmailTemplate(StrEnum):
    VERIFY_EMAIL = "verify_email"
    ACCOUNT_EXISTS = "account_exists"
    PASSWORD_RESET = "password_reset"  # noqa: S105
    INVITATION = "invitation"


@dataclass(frozen=True)
class Email:
    to: str
    template: EmailTemplate
    link: str | None = None


class Mailer(Protocol):
    async def send(self, email: Email) -> None: ...


class EmailQueue(Protocol):
    def enqueue(self, email: Email) -> None: ...


def render(email: Email) -> tuple[str, str]:
    """Return the subject and body. Templates are Turkish: line 1 is the subject."""
    text = (TEMPLATE_DIR / f"{email.template}.txt").read_text(encoding="utf-8")
    subject, _, body = text.partition("\n")
    return subject, Template(body.lstrip("\n")).substitute(link=email.link or "")


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, email: Email) -> None:
        settings = self._settings
        subject, body = render(email)
        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = email.to
        message["Subject"] = subject
        message.set_content(body)

        password = settings.smtp_password
        try:
            await aiosmtplib.send(
                message,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=password.get_secret_value() if password else None,
                start_tls=settings.smtp_start_tls,
            )
        except (aiosmtplib.SMTPException, OSError) as error:
            logger.error("email_failed", template=email.template, exc_info=error)


class BackgroundEmailQueue:
    """Sends after the response, so that timing does not reveal whether an email exists."""

    def __init__(self, background_tasks: BackgroundTasks, mailer: Mailer) -> None:
        self._background_tasks = background_tasks
        self._mailer = mailer

    def enqueue(self, email: Email) -> None:
        self._background_tasks.add_task(self._mailer.send, email)
