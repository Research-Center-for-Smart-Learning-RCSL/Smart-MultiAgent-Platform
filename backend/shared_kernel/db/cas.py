"""Compare-and-set UPDATE — the shared shape behind every state-machine guard.

Several repositories enforce "a state transition is only legal from certain
predecessor states" as a predicate in the UPDATE's ``WHERE`` clause rather
than a read-then-write in application code, so the invariant holds under
concurrent writers and no caller can bypass it by skipping a check. This
factors out the identical SQL shape; each repository still owns its own
transition table and its own policy for what a miss means (an absent row and
a rejected transition are indistinguishable from ``rowcount`` alone, and
callers differ on whether that distinction matters to them).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from shared_kernel.db.rowcount import rowcount


async def cas_update(
    db: AsyncSession,
    table: sa.Table,
    *,
    row_id: Any,
    allowed_from: Iterable[str],
    values: dict[str, Any],
    id_column: sa.ColumnElement[Any] | None = None,
    state_column: sa.ColumnElement[Any] | None = None,
) -> bool:
    """``UPDATE table SET **values WHERE id_column == row_id AND state_column IN allowed_from``.

    ``id_column`` / ``state_column`` default to ``table.c.id`` / ``table.c.state`` — every
    current caller's table uses those names; pass them explicitly only when a table's
    primary key or state column is named differently.

    Returns whether the write happened.
    """
    id_col = table.c.id if id_column is None else id_column
    state_col = table.c.state if state_column is None else state_column
    result = await db.execute(
        table.update().where(id_col == row_id, state_col.in_(list(allowed_from))).values(**values),
    )
    return bool(rowcount(result))


__all__ = ["cas_update"]
