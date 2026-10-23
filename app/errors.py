from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Domain error rendered as {"error": {"code", "message", "details?"}}."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def bad_request(code: str, msg: str, details: Any = None) -> AppError:
    return AppError(400, code, msg, details)


def unauthorized(msg: str = "Authentication required") -> AppError:
    return AppError(401, "unauthorized", msg)


def payment_required(msg: str = "Not enough credits") -> AppError:
    return AppError(402, "insufficient_credits", msg)


def forbidden(msg: str = "Forbidden") -> AppError:
    return AppError(403, "forbidden", msg)


def not_found(msg: str = "Not found") -> AppError:
    return AppError(404, "not_found", msg)


def conflict(code: str, msg: str) -> AppError:
    return AppError(409, code, msg)


def unprocessable(code: str, msg: str) -> AppError:
    return AppError(422, code, msg)


def too_many(msg: str) -> AppError:
    return AppError(429, "rate_limited", msg)


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"error": err}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=400, content=_body("validation_error", "Request validation failed", details))
