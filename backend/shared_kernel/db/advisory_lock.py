"""Transaction-scoped Postgres advisory locks shared across contexts.

A ``pg_advisory_xact_lock`` is held until the current transaction commits or
rolls back, and Postgres stacks re-acquisitions of the same key within one
transaction, so a caller that already holds a lock may take it again cheaply.
Keying on ``hashtext(:name)`` lets unrelated subsystems share one namespace by
string convention without colliding numerically.

The Knowledge-Map builder/consumer isolation (R11.25) uses this so the config
builder-key-group change and every agent attach/re-key against that map
serialise on one key — the loser of a race observes the winner's committed
state (F-14).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["advisory_xact_lock", "knowmap_builder_lock_key"]


async def advisory_xact_lock(db: AsyncSession, name: str) -> None:
    """Take the transaction-scoped advisory lock keyed on ``hashtext(name)``.

    Held until the current transaction ends; re-entrant within a transaction.
    """
    await db.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": name})


def knowmap_builder_lock_key(config_id: uuid.UUID) -> str:
    """Canonical lock name serialising a Knowledge Map's builder-group change
    against every agent attach/re-key bound to that map (F-14 / R11.25)."""
    return f"knowmap_builder:{config_id}"
