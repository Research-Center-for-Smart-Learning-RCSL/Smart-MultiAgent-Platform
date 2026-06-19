"""Shared FastAPI dependencies for the v1 API layer."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Reusable limit/offset pagination extracted from query-string params."""

    limit: int = Query(100, ge=1, le=500, description="Max items to return")  # noqa: RUF009
    offset: int = Query(0, ge=0, description="Number of items to skip")  # noqa: RUF009
