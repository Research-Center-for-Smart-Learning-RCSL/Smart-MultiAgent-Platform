"""shared_kernel.db.rowcount narrows a DML Result to CursorResult.rowcount.

The cast is a runtime no-op, so any object exposing ``rowcount`` stands in for
the real CursorResult here; the value must pass through unchanged, including the
driver's -1 "unknown" sentinel that callers guard with their own ``or 0``.
"""

from __future__ import annotations

from types import SimpleNamespace

from shared_kernel.db.rowcount import rowcount


def test_rowcount_returns_the_cursor_rowcount() -> None:
    assert rowcount(SimpleNamespace(rowcount=5)) == 5


def test_rowcount_passes_zero_through() -> None:
    assert rowcount(SimpleNamespace(rowcount=0)) == 0


def test_rowcount_passes_unknown_sentinel_through() -> None:
    assert rowcount(SimpleNamespace(rowcount=-1)) == -1
