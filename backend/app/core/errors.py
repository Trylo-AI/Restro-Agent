"""Error body and status codes (docs/data-and-api.md#errors-and-idempotency).

Body: {"error": {"code", "message", "details"?}}. `message` is safe to show to staff.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    status = 500
    code = "internal"
    default_message = "Something went wrong. Please try again."

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details = details


class BadRequest(AppError):
    status, code, default_message = 400, "bad_request", "That request isn't valid."


class Unauthenticated(AppError):
    status, code, default_message = 401, "unauthenticated", "Please sign in again."


class Forbidden(AppError):
    status, code, default_message = 403, "forbidden", "You don't have access to this."


class NotFound(AppError):
    status, code, default_message = 404, "not_found", "Not found."


class VersionConflict(AppError):
    status, code, default_message = 409, "version_conflict", "This changed since you loaded it."


class WindowClosed(AppError):
    status, code = 409, "window_closed"
    default_message = "WhatsApp only allows a reply after the customer messages again."


class InvalidTransition(AppError):
    status, code, default_message = 422, "invalid_transition", "This order was already updated."


class RateLimited(AppError):
    status, code, default_message = 429, "rate_limited", "Too many requests. Try again shortly."


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if details:
        err["details"] = details
    return {"error": err}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations only; never echo submitted values (they may hold text or numbers).
        fields = [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()]
        return JSONResponse(error_body("bad_request", "That request isn't valid.", {"fields": fields}), status_code=400)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_class=type(exc).__name__)
        return JSONResponse(error_body("internal", AppError.default_message), status_code=500)
