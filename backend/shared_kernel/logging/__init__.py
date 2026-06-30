"""Structured JSON logging + redaction (R17.03 / O1.03-O1.05)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared_kernel.logging.context import request_id_var, session_id_var, user_id_var
from shared_kernel.logging.redaction import redact

if TYPE_CHECKING:
    from shared_kernel.logging.setup import configure_logging

__all__ = [
    "configure_logging",
    "redact",
    "request_id_var",
    "session_id_var",
    "user_id_var",
]


def __getattr__(name: str) -> Any:
    # Lazy re-export: `setup` pulls in loguru + orjson, which lightweight
    # consumers (e.g. the egress proxy, which only needs `redact`/`mask_str`)
    # must not be forced to install. Importing this package no longer drags in
    # those deps; they load only when `configure_logging` is actually accessed.
    if name == "configure_logging":
        from shared_kernel.logging.setup import configure_logging

        return configure_logging
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
