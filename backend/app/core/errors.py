"""Application errors and the single JSON error shape."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    message = "The request could not be processed."

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


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    message = "The service is temporarily unavailable."


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests."


def error_response(
    status_code: int,
    code: str,
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
    return error_response(422, "VALIDATION_ERROR", "The request is invalid.", {"fields": fields})


HTTP_ERROR_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


async def handle_http_exception(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)  # noqa: S101
    return error_response(
        error.status_code,
        HTTP_ERROR_CODES.get(error.status_code, "HTTP_ERROR"),
        HTTPStatus(error.status_code).phrase,
        headers=error.headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
