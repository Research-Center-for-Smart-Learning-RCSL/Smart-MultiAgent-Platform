"""Structured JSON logging + redaction (R17.03 / O1.03-O1.05)."""

from shared_kernel.logging.context import request_id_var, session_id_var, user_id_var
from shared_kernel.logging.redaction import redact
from shared_kernel.logging.setup import configure_logging

__all__ = [
    "configure_logging",
    "redact",
    "request_id_var",
    "session_id_var",
    "user_id_var",
]
