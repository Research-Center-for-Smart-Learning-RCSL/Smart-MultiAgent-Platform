"""Store doubles shared by the two admin-reset suites.

Both products bind the same ``perform_admin_reset``, so their tests must exercise it
through the same doubles or "the two resets behave identically" stops being provable.
This module is deliberately NOT named ``test_*``: pytest would collect it, and a suite
importing its fixtures from a collection target couples the two in a way nothing names
(an edit to a Concept Map fake would silently move Knowledge Map assertions).
"""

from __future__ import annotations

import uuid
from typing import Any


class RecordingDb:
    """Minimal AsyncSession double — records commits; no real audit write."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.committed = False

    async def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
        self.calls.append(stmt)

        class _R:
            def one(_self) -> Any:  # noqa: N805
                return None

            def first(_self) -> Any:  # noqa: N805
                return None

            def all(_self) -> list[Any]:  # noqa: N805
                return []

        return _R()

    async def commit(self) -> None:
        self.committed = True


class FakeLockStore:
    def __init__(self, *, held: bool = False) -> None:
        self.held = held
        self.acquired = False
        self.released = False
        self.force_released = False

    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        if self.held:
            return False
        self.acquired = True
        return True

    async def release(self, config_id: uuid.UUID) -> None:
        self.released = True

    async def force_release(self, config_id: uuid.UUID) -> None:
        self.force_released = True
        self.held = False  # after breaking the lock, re-acquire succeeds


class FakeSnapshotStore:
    """The pointer and the snapshot are independent fields on purpose.

    They are two separately expiring Redis keys in production, so "pointer present,
    snapshot gone" — the lapsed-recovery-window case the reset must refuse — has to be
    expressible here.
    """

    def __init__(self, *, current: uuid.UUID | None = None, snapshot: dict[str, Any] | None = None) -> None:
        self.current = current
        self.snapshot = snapshot
        self.deleted: list[uuid.UUID] = []
        self.cleared = False

    async def get_current(self, *, config_id: uuid.UUID) -> uuid.UUID | None:
        return self.current

    async def get(self, *, config_id: uuid.UUID, build_id: uuid.UUID) -> dict[str, Any] | None:
        return self.snapshot

    async def delete(self, *, config_id: uuid.UUID, build_id: uuid.UUID) -> None:
        self.deleted.append(build_id)

    async def clear_current(self, *, config_id: uuid.UUID) -> None:
        self.cleared = True


class FakeNeo4j:
    def __init__(self, *, raise_on_restore: bool = False, raise_on_delete: bool = False) -> None:
        self.raise_on_restore = raise_on_restore
        self.raise_on_delete = raise_on_delete
        self.deleted: list[uuid.UUID] = []
        self.restored: list[dict[str, Any]] = []
        self.closed = False

    async def delete_by_build(self, *, config_id: uuid.UUID, build_id: uuid.UUID) -> None:
        if self.raise_on_delete:
            raise RuntimeError("neo4j delete down")
        self.deleted.append(build_id)

    async def restore_from_snapshot(self, *, config_id: uuid.UUID, snapshot: dict[str, Any]) -> None:
        if self.raise_on_restore:
            raise RuntimeError("neo4j restore down")
        self.restored.append(snapshot)

    async def close(self) -> None:
        self.closed = True
