"""The error-code contract: one enum, used by every error and published in the API document."""

import httpx

import app.main  # noqa: F401  (imports every module, so every AppError subclass exists)
from app.core.errors import AppError, ErrorCode

# Codes the error handlers and the middleware return without an AppError subclass.
HANDLER_CODES = {
    ErrorCode.VALIDATION_ERROR,
    ErrorCode.NOT_FOUND,
    ErrorCode.METHOD_NOT_ALLOWED,
    ErrorCode.HTTP_ERROR,
    ErrorCode.INTERNAL_ERROR,
}


def error_classes() -> set[type[AppError]]:
    found: set[type[AppError]] = {AppError}
    pending = [AppError]
    while pending:
        for subclass in pending.pop().__subclasses__():
            if subclass not in found and subclass.__module__.startswith("app."):
                found.add(subclass)
                pending.append(subclass)
    return found


def test_every_application_error_uses_the_enum() -> None:
    for error in error_classes():
        assert isinstance(error.code, ErrorCode), error


def test_every_code_in_the_enum_can_be_returned() -> None:
    used = {error.code for error in error_classes()} | HANDLER_CODES

    assert set(ErrorCode) == used


async def test_the_api_document_lists_every_code(client: httpx.AsyncClient) -> None:
    document = (await client.get("/openapi.json")).json()

    assert sorted(document["components"]["schemas"]["ErrorCode"]["enum"]) == sorted(ErrorCode)


async def test_every_operation_documents_the_error_shape(client: httpx.AsyncClient) -> None:
    document = (await client.get("/openapi.json")).json()

    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            schema = operation["responses"]["default"]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, (method, path)
            # The generic FastAPI 422 body does not match the error shape of this API.
            assert "422" not in operation["responses"], (method, path)
