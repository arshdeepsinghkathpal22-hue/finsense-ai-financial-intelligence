"""Application errors and the uniform JSON error envelope.

Every error response has the shape::

    {"error": {"code": "not_found", "message": "...", "details": {...}}}

Unexpected exceptions are logged server-side and returned as a generic
500 without stack traces or internal details.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("finsense.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class AuthenticationError(AppError):
    status_code = 401
    code = "not_authenticated"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class InsufficientDataError(AppError):
    """Not enough observations to compute a metric or train a model."""

    status_code = 422
    code = "insufficient_data"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


class InfeasibleError(AppError):
    """An optimisation problem has no solution under the given constraints."""

    status_code = 422
    code = "infeasible"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitedError) and "retry_after_s" in exc.details:
            headers["Retry-After"] = str(int(exc.details["retry_after_s"]) + 1)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Echo field locations and messages, but never the submitted values
        # (they may contain passwords).
        problems = [
            {"location": [str(part) for part in err.get("loc", ())], "message": err.get("msg", "")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_envelope("validation_failed", "Request validation failed.", {"problems": problems}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "not_authenticated"}.get(
            exc.status_code, "http_error"
        )
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(status_code=exc.status_code, content=_envelope(code, message))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "An unexpected error occurred."),
        )
