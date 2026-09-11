"""Canvas search query shape: ordering, snippet delimiters, REGCONFIG cast.

Mirrors `test_message_repo.py` for the canvas FTS query ([R13.62]/[R13.63]).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from contexts.canvas.infrastructure.repositories.canvas_repo import CanvasRepository


def _empty_result() -> MagicMock:
    result = MagicMock()
    result.all.return_value = []
    return result


async def _compiled_search_sql() -> str:
    db = AsyncMock()
    db.execute.side_effect = [_empty_result()]

    repo = CanvasRepository(db)
    await repo.search(uuid.uuid4(), "test query", limit=10)

    stmt = db.execute.await_args_list[0].args[0]
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _order_by_clause(compiled: str) -> str:
    _, _, tail = compiled.partition("ORDER BY")
    assert tail, "statement has no ORDER BY"
    return tail.partition("LIMIT")[0]


class TestCanvasSearchOrdering:
    async def test_search_order_is_total(self) -> None:
        compiled = await _compiled_search_sql()
        order_by = _order_by_clause(compiled)

        assert "rank DESC" in order_by
        assert "canvas_objects.created_at DESC" in order_by
        assert "canvas_objects.id DESC" in order_by

    async def test_search_orders_before_limiting(self) -> None:
        compiled = await _compiled_search_sql()
        assert compiled.index("ORDER BY") < compiled.index("LIMIT")


class TestCanvasSearchSnippet:
    async def test_snippet_uses_mark_delimiters(self) -> None:
        compiled = await _compiled_search_sql()
        assert "StartSel=<mark>" in compiled
        assert "StopSel=</mark>" in compiled

    async def test_snippet_keeps_headline_options(self) -> None:
        compiled = await _compiled_search_sql()
        assert "MaxWords=35" in compiled
        assert "MinWords=15" in compiled
        assert "ShortWord=3" in compiled


class TestCanvasSearchParameterTypes:
    async def test_search_casts_the_text_search_config(self) -> None:
        compiled = await _compiled_search_sql()
        assert compiled.upper().count("AS REGCONFIG") >= 1


class TestCanvasSearchFilter:
    async def test_search_filters_by_canvas_id(self) -> None:
        compiled = await _compiled_search_sql()
        assert "canvas_objects.canvas_id" in compiled

    async def test_search_uses_tsquery_match(self) -> None:
        compiled = await _compiled_search_sql()
        assert "@@" in compiled

    async def test_search_uses_plainto_tsquery(self) -> None:
        compiled = await _compiled_search_sql()
        assert "plainto_tsquery" in compiled
