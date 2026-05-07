"""FastAPI exception handlers that emit ``application/problem+json``.

Every response body conforms to RFC 7807 and carries a `type` under the
canonical ``https://smap.local/problems/…`` prefix (R19.06).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared_kernel.errors.problem import Problem, SmapError, problem_type

_PROBLEM_MEDIA_TYPE = "application/problem+json"


def _respond(problem: Problem, request: Request) -> JSONResponse:
    body: dict[str, Any] = problem.dump()
    if "instance" not in body:
        body["instance"] = str(request.url.path)
    return JSONResponse(
        status_code=problem.status,
        content=body,
        media_type=_PROBLEM_MEDIA_TYPE,
    )


async def _smap_error_handler(request: Request, exc: SmapError) -> JSONResponse:
    logger.bind(event="smap_error", route=str(request.url.path)).info("handled SmapError", exc_info=False)
    return _respond(exc.problem, request)


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Canonical translation: RequestValidationError → validation problem.
    problem = Problem(
        type=problem_type("validation"),
        title="Validation Failed",
        status=422,
        detail="Request validation failed.",
        extras={"field_errors": exc.errors()},
    )
    return _respond(problem, request)


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """FastAPI/Starlette HTTPException → RFC 7807 problem+json.

    Detail may already be a Problem dict (from `_raise_forbidden`), in which
    case we serve it directly. Otherwise we wrap the raw detail string.
    """
    detail = exc.detail
    if isinstance(detail, dict) and "type" in detail and "status" in detail:  # type: ignore[unreachable]
        body: dict[str, Any] = dict(detail)  # type: ignore[unreachable]
        body.setdefault("instance", str(request.url.path))
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            media_type=_PROBLEM_MEDIA_TYPE,
        )
    slug = _status_slug(exc.status_code)
    problem = Problem(
        type=problem_type(slug),
        title=_status_title(exc.status_code),
        status=exc.status_code,
        detail=str(detail) if detail is not None else None,
    )
    return _respond(problem, request)


def _status_slug(code: int) -> str:
    if code == 401:
        return "auth/required"
    if code == 403:
        return "forbidden"
    if code == 404:
        return "not-found"
    if code == 409:
        return "conflict"
    if code == 412:
        return "precondition-failed"
    if code == 422:
        return "validation"
    if code == 429:
        return "rate-limited"
    return "http-error"


def _status_title(code: int) -> str:
    return {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        412: "Precondition Failed",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
    }.get(code, "HTTP Error")


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never leak exception details — bug was already logged by middleware.
    logger.bind(event="unhandled_exception", route=str(request.url.path)).exception("unhandled exception")
    problem = Problem(
        type=problem_type("internal"),
        title="Internal Server Error",
        status=500,
        detail="An unexpected error occurred. See request_id in logs.",
    )
    return _respond(problem, request)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(SmapError, _smap_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_handler)
