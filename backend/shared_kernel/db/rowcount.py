"""Typed access to a DML statement's affected-row count.

``AsyncSession.execute`` is typed to return ``Result[Any]``, which does not
expose ``rowcount``. An INSERT/UPDATE/DELETE always yields a ``CursorResult``,
so narrow to it in one place here rather than scattering casts or
``# type: ignore[attr-defined]`` across every repository.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, Result


def rowcount(result: Result[Any]) -> int:
    """Number of rows matched by a DML statement's ``result``.

    Valid only for the ``CursorResult`` an INSERT/UPDATE/DELETE ``execute``
    returns; a SELECT ``Result`` has no meaningful rowcount. May be ``-1`` when
    the driver cannot report it, so callers that need a non-negative count keep
    their own ``or 0`` / ``> 0`` handling.
    """
    return cast("CursorResult[Any]", result).rowcount
