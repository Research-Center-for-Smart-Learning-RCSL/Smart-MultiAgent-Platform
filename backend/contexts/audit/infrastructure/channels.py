"""Audit-context pub/sub channel constant."""

from __future__ import annotations

from typing import Final

AUDIT_TAIL_CHANNEL: Final = "ws:audit:tail"

__all__ = ["AUDIT_TAIL_CHANNEL"]
