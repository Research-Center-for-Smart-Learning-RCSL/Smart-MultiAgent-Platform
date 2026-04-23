"""Audit-log plumbing (R17.01–R17.04).

The `audit_logs` table is a *cross-cutting* concern — every bounded context
writes to it — so the Table declaration, emitter, and redaction policy live
in `shared_kernel` rather than inside `contexts.audit`. That context exists
only to house Admin-facing query services and the nightly retention worker
(Phase I).

Key surface:

- `audit_logs` SQLAlchemy Table bound to the shared MetaData.
- `redact(payload)` — recursive secret stripping (R17.03).
- `AuditWriter` — async emitter that accepts a structured event and commits
  one row. Concrete implementations live here; tests may monkey-patch.

SoC: imports from `app.config.settings` for the problem URL base and from
the shared MetaData only. No FastAPI, no contexts.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from shared_kernel.db import metadata

# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

audit_logs = sa.Table(
    "audit_logs",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("actor_user_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("actor_ip", pg.INET(), nullable=True),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("resource_type", sa.Text, nullable=True),
    sa.Column("resource_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("metadata", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("session_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("request_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)


# ---------------------------------------------------------------------------
# Redaction (R17.03)
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_RE: Final = re.compile(
    r"^(authorization|api[_-]?key|secret|password|token|bearer|"
    r"private[_-]?key|cookie|session)$",
    re.IGNORECASE,
)
_SECRET_SHAPE_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$"),
    re.compile(r"^sk-[A-Za-z0-9_\-]{40,}$"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),           # AWS access key
    re.compile(r"^AIza[0-9A-Za-z_\-]{35}$"),      # Google API key
)

_REDACTED: Final = "<redacted>"


def redact(value: Any) -> Any:
    """Return a deep-copy of `value` with secrets scrubbed."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SENSITIVE_KEY_RE.match(k):
                out[k] = _REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for pat in _SECRET_SHAPE_RES:
            if pat.search(value):
                return _REDACTED
        return value
    return value


# ---------------------------------------------------------------------------
# Event + emitter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str
    actor_user_id: uuid.UUID | None = None
    actor_ip: str | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None


async def emit(session: AsyncSession, event: AuditEvent) -> None:
    """Insert one audit row using the caller's already-open transaction.

    Using the caller's session means the audit write joins the same unit of
    work as the domain change — so a rollback cleans up both, and the audit
    trail never describes an operation that the DB later un-did. `created_at`
    is left to the server default so clock-skew between app and DB never shows
    up in the append-only trail.
    """
    payload = redact(event.metadata or {})
    await session.execute(
        audit_logs.insert().values(
            actor_user_id=event.actor_user_id,
            actor_ip=event.actor_ip,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            metadata=payload,
            session_id=event.session_id,
            request_id=event.request_id,
        ),
    )


__all__ = ["AuditEvent", "audit_logs", "emit", "redact"]
