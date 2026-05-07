"""Attach a per-request `request_id` to contextvars for structured logging.

Accepts an inbound `X-Request-Id` header (if present and sane); otherwise
generates a UUID4. Always echoes the chosen id on the response.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from shared_kernel.logging.context import request_id_var

_HEADER = "x-request-id"
_SAFE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(_HEADER, "")
        rid = incoming if _SAFE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[_HEADER] = rid
        return response
