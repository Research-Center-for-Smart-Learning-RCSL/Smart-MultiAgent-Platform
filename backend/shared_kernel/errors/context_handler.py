"""Shared RFC-7807 exception handler for per-context domain errors.

Each bounded context owns a ``BaseError -> (slug, http-status, title)`` map and
registers it via :func:`register_context_handler`. This module turns that map
into a single FastAPI exception handler with two properties the hand-rolled
per-context copies lacked (API-3):

* **MRO dispatch** — dispatch walks ``type(exc).__mro__`` and matches the
  *nearest mapped ancestor*. A subclass of a mapped error inherits its parent's
  status instead of falling through; a new error class only needs its own
  ancestor mapped, not an entry in every context table.
* **A 500 fallback** — an unmapped error is a server-side oversight, not a
  client mistake, so it surfaces as ``internal`` / 500 instead of a misleading
  400 that hides 404/409/500 conditions behind a generic slug.

``shared_kernel`` imports nothing from ``contexts.*``: the base class, map and
extras callback are supplied by the caller, so the SoC boundary holds.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

#: ``(slug, http-status, title)`` for one error class.
ErrorSpec = tuple[str, int, str]
#: Maps an exception class to its :data:`ErrorSpec`.
ErrorMap = dict[type[Exception], ErrorSpec]
#: Optional per-exception extra Problem members (e.g. ``retry_after_seconds``).
ExtrasFn = Callable[[Exception], dict[str, Any]]

#: Used when no ancestor of the raised exception is mapped. A 500 (not a 400)
#: because an unmapped domain error means the context forgot to map it.
_FALLBACK: ErrorSpec = ("internal", 500, "Internal error")


def resolve_spec(exc: Exception, error_map: ErrorMap) -> ErrorSpec:
    """Return the spec of the nearest mapped ancestor of ``exc``'s type.

    Walking the MRO (rather than ``error_map.get(type(exc))``) means a subclass
    of a mapped error inherits its mapping; an unmapped error falls back to
    :data:`_FALLBACK`.
    """
    for klass in type(exc).__mro__:
        spec = error_map.get(klass)
        if spec is not None:
            return spec
    return _FALLBACK


def make_context_handler(
    error_map: ErrorMap,
    extras: ExtrasFn | None = None,
) -> Callable[[Request, Exception], Any]:
    """Build a FastAPI exception handler that renders ``error_map`` as RFC-7807."""

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        slug, status_code, title = resolve_spec(exc, error_map)
        problem = Problem(
            type=problem_type(slug),
            title=title,
            status=status_code,
            detail=str(exc),
            extras=extras(exc) if extras is not None else {},
        )
        body = problem.dump()
        body["instance"] = str(request.url.path)
        return JSONResponse(status_code=status_code, content=body, media_type=_MEDIA)

    return _handler


def register_context_handler(
    app: FastAPI,
    base_exc: type[Exception],
    error_map: ErrorMap,
    extras: ExtrasFn | None = None,
) -> None:
    """Register a shared RFC-7807 handler for ``base_exc`` and its subclasses."""
    app.add_exception_handler(base_exc, make_context_handler(error_map, extras))


__all__ = [
    "ErrorMap",
    "ErrorSpec",
    "ExtrasFn",
    "make_context_handler",
    "register_context_handler",
    "resolve_spec",
]
