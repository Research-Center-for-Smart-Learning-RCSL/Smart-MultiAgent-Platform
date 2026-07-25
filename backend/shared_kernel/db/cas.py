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


async def cas_update(
    db: AsyncSession,
    table: sa.Table,
    *,
    id_column: sa.ColumnElement[Any],
    row_id: Any,
    state_column: sa.ColumnElement[Any],
    allowed_from: Iterable[str],
    values: dict[str, Any],
) -> bool:
    """``UPDATE table SET **values WHERE id_column == row_id AND state_column IN allowed_from``.

    Returns whether the write happened.
    """
    result = await db.execute(
        table.update().where(id_column == row_id, state_column.in_(list(allowed_from))).values(**values),
    )
    return (result.rowcount or 0) > 0


__all__ = ["cas_update"]
