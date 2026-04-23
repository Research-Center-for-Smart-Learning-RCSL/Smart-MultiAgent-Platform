"""Request-scoped contextvars propagated into every log line."""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("smap_request_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("smap_session_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("smap_user_id", default=None)
