"""Structured logging with a request ID and redaction of sensitive data."""

import logging
import re
import sys
import traceback
from collections.abc import Mapping
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from app.core.config import Environment, Settings

REDACTED = "[REDACTED]"

SENSITIVE_KEY_PARTS = (
    "address",
    "authorization",
    "birth",
    "cookie",
    "csrf",
    "email",
    "name",
    "note",
    "otp",
    "password",
    "phone",
    "reason",
    "secret",
    "session",
    "token",
)
SAFE_KEYS = frozenset({"event", "exception_type", "logger", "logger_name"})

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_PATTERN = re.compile(r"(?<![\w-])\+?\d(?:[\s().-]?\d){9,14}(?![\w-])")
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return key not in SAFE_KEYS and any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _scrub_text(value: str) -> str:
    for pattern in (EMAIL_PATTERN, PHONE_PATTERN, BEARER_PATTERN):
        value = pattern.sub(REDACTED, value)
    return value


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_scrub(item) for item in value]
    return value


def redact_sensitive_data(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    scrubbed: EventDict = _scrub(event_dict)
    return scrubbed


def summarize_exception(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Replace exc_info with types and code locations, dropping exception messages.

    Messages can carry personal data, for example a Postgres unique violation
    quotes the conflicting email.
    """
    exc_info = event_dict.pop("exc_info", None)
    if not exc_info:
        return event_dict

    if exc_info is True:
        exc_info = sys.exc_info()
    error = exc_info if isinstance(exc_info, BaseException) else exc_info[1]
    if error is None:
        return event_dict

    chain: list[str] = []
    current: BaseException | None = error
    while current is not None:
        chain.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    frames = traceback.extract_tb(error.__traceback__)

    event_dict["exception_type"] = " <- ".join(chain)
    event_dict["stack"] = [f"{f.filename}:{f.lineno} in {f.name}" for f in frames]
    return event_dict


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        summarize_exception,
        redact_sensitive_data,
    ]
    renderer: Processor = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.environment is Environment.DEV
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # Uvicorn's handlers would bypass redaction, and its access log prints query strings.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False

    # HTTP client libraries log full URLs at INFO.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
