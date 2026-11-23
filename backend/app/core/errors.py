"""Application errors and the single JSON error shape."""

from collections.abc import Mapping
from enum import StrEnum
from http import HTTPStatus
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(StrEnum):
    """Every `error.code` the API can return. The frontend maps each one to Turkish text."""

    APPOINTMENT_NOT_STARTED = "APPOINTMENT_NOT_STARTED"
    BAD_REQUEST = "BAD_REQUEST"
    CANCELLATION_WINDOW_CLOSED = "CANCELLATION_WINDOW_CLOSED"
    CANNOT_TARGET_SELF = "CANNOT_TARGET_SELF"
    CLIENT_HAS_ACTIVE_APPOINTMENTS = "CLIENT_HAS_ACTIVE_APPOINTMENTS"
    CSRF_INVALID = "CSRF_INVALID"
    DATE_RANGE_INVALID = "DATE_RANGE_INVALID"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    EMAIL_TAKEN = "EMAIL_TAKEN"
    FORBIDDEN = "FORBIDDEN"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_BIRTH_DATE = "INVALID_BIRTH_DATE"
    INVALID_CODE = "INVALID_CODE"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    INVALID_TIME_RANGE = "INVALID_TIME_RANGE"
    INVALID_TOKEN = "INVALID_TOKEN"  # noqa: S105  (an error code, not a secret)
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    MFA_REQUIRED = "MFA_REQUIRED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    NOT_FOUND = "NOT_FOUND"
    PSYCHOLOGIST_ID_REQUIRED = "PSYCHOLOGIST_ID_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    SLOT_TAKEN = "SLOT_TAKEN"
    SLOT_UNAVAILABLE = "SLOT_UNAVAILABLE"
    SLUG_TAKEN = "SLUG_TAKEN"
    TIME_OFF_CONFLICT = "TIME_OFF_CONFLICT"
    TWO_FACTOR_SETUP_REQUIRED = "TWO_FACTOR_SETUP_REQUIRED"
    TWO_FACTOR_STATE_INVALID = "TWO_FACTOR_STATE_INVALID"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    WEAK_PASSWORD = "WEAK_PASSWORD"  # noqa: S105  (an error code, not a secret)


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any]


class ErrorResponse(BaseModel):
    """The single shape of every error response."""

    error: ErrorBody


# Documents ErrorResponse, and with it every ErrorCode, on each route of the API.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {"model": ErrorResponse, "description": "Error"}
}


class AppError(Exception):
    status_code = 400
    code: ClassVar[ErrorCode] = ErrorCode.BAD_REQUEST
    message = "The request could not be processed."
    # Commit the transaction even though the request fails.
    keep_transaction = False

    def __init__(
        self,
        *,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = dict(details or {})
        self.headers = dict(headers or {})
        super().__init__(self.code)


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND
    message = "Not Found"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE
    message = "The service is temporarily unavailable."


class RateLimitedError(AppError):
    status_code = 429
    code = ErrorCode.RATE_LIMITED
    message = "Too many requests."


def error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": dict(details or {})}}
    return JSONResponse(jsonable_encoder(body), status_code=status_code, headers=headers)


async def handle_app_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, AppError)  # noqa: S101
    return error_response(
        error.status_code, error.code, error.message, error.details, error.headers
    )


async def handle_validation_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)  # noqa: S101
    # Only location and type: the submitted values may be passwords or personal data.
    fields = [
        {"field": ".".join(str(part) for part in item["loc"]), "code": item["type"]}
        for item in error.errors()
    ]
    return error_response(
        422, ErrorCode.VALIDATION_ERROR, "The request is invalid.", {"fields": fields}
    )


HTTP_ERROR_CODES = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.METHOD_NOT_ALLOWED}


async def handle_http_exception(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)  # noqa: S101
    return error_response(
        error.status_code,
        HTTP_ERROR_CODES.get(error.status_code, ErrorCode.HTTP_ERROR),
        HTTPStatus(error.status_code).phrase,
        headers=error.headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
