"""Global request-body size cap for JSON payloads (API-7).

Per-field Pydantic validators bound individual inputs, but a coarse catch-all
keeps an oversized body from being buffered and parsed in the first place — the
JSON parser runs *before* field validation, so a 50 MB body costs memory even if
every field would later be rejected. This middleware refuses such bodies up
front with a 413.

Scope: only ``application/json`` requests are capped. File uploads use
``multipart/form-data`` / tus and have their own size controls, so they bypass
this check untouched.

The check is by ``Content-Length`` header. Clients that omit it (chunked
transfer) fall through to the per-field validators, which still bound the parsed
result.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from shared_kernel.errors.handlers import problem_response
from shared_kernel.errors.problem import Problem, problem_type

# 1 MiB comfortably exceeds the largest legitimate JSON body (a workflow
# definition is capped at ~512 KB; chat/system-prompt text at 100 KB) while
# refusing anything that could only be an attempt to stress storage/memory.
_MAX_JSON_BODY_BYTES = 1_048_576

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        if request.method not in _BODY_METHODS:
            return await call_next(request)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return await call_next(request)
        raw_len = request.headers.get("content-length")
        if raw_len is None:
            return await call_next(request)
        try:
            length = int(raw_len)
        except ValueError:
            return await call_next(request)
        # A malformed (negative) Content-Length is left for the ASGI server to
        # reject; we only short-circuit a well-formed body that is over the cap.
        if length < 0 or length <= _MAX_JSON_BODY_BYTES:
            return await call_next(request)
        problem = Problem(
            type=problem_type("payload-too-large"),
            title="Payload Too Large",
            status=413,
            detail=f"JSON body of {length} bytes exceeds the {_MAX_JSON_BODY_BYTES}-byte limit",
            extras={"limit_bytes": _MAX_JSON_BODY_BYTES},
        )
        return problem_response(problem, instance=request.url.path)
