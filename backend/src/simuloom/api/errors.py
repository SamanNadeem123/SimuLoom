"""Structured error responses (spec 6.1). No stack traces ever reach the client."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("simuloom.api")

VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
EXPERIMENT_NOT_READY = "EXPERIMENT_NOT_READY"
TOO_MANY_PENDING = "TOO_MANY_PENDING"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        field_errors: Sequence[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field_errors = list(field_errors or [])
        self.headers = headers or {}


def not_found(what: str) -> ApiError:
    return ApiError(404, NOT_FOUND, f"{what} was not found.")


def field_errors_from(errors: Sequence[Any], prefix: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in errors:
        loc = [str(x) for x in e.get("loc", ()) if x != "body"]
        path = ".".join(([prefix] if prefix else []) + loc)
        out.append({"path": path, "code": str(e.get("type", "invalid")), "message": str(e.get("msg", "Invalid value."))})
    return out


def validation_api_error(exc: ValidationError, prefix: str, message: str) -> ApiError:
    return ApiError(
        422, VALIDATION_ERROR, message, field_errors_from(exc.errors(include_url=False, include_context=False), prefix)
    )


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return str(rid) if rid else uuid.uuid4().hex


def _body(request: Request, code: str, message: str, field_errors: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": request_id(request)}
    if field_errors:
        err["field_errors"] = list(field_errors)
    return {"error": err}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(request, exc.code, exc.message, exc.field_errors), exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _req(request: Request, exc: RequestValidationError) -> JSONResponse:
        fe = field_errors_from(exc.errors())
        return JSONResponse(_body(request, VALIDATION_ERROR, "The request is invalid.", fe), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: NOT_FOUND, 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        msg = "The resource was not found." if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(_body(request, code, msg), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = request_id(request)
        log.exception("unhandled error request_id=%s path=%s", rid, request.url.path)
        return JSONResponse(_body(request, INTERNAL_ERROR, "An internal error occurred."), 500)
