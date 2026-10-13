"""The project agent-name projection (FU-12 of the observer-UI defect sweep).

``AgentOut`` carries ``system_prompt``, bounded at 100k characters. Its busiest
caller only ever built an id-to-name map out of it, on the path every chatroom
open runs, so a project near the pagination ceiling turned opening a room into a
multi-megabyte response for two fields per row.

What matters about the route is what it does *not* carry: the projection is the
point. Since matrix row 28 the full listing is editors-only, while the names
projection keeps the membership gate, because every room label reads it.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.api.v1 import agents as agents_api
from contexts.agents.infrastructure.repositories import AgentRepository

_PROJECT = uuid.UUID("33333333-3333-3333-3333-333333333333")
_AGENT = uuid.UUID("44444444-4444-4444-4444-444444444444")


class TestTheRouteReturnsOnlyALabel:
    async def test_it_carries_the_id_and_the_name_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = MagicMock()
        service.names_for_project = AsyncMock(return_value=[(_AGENT, "Watcher")])
        monkeypatch.setattr(agents_api, "AgentService", lambda _db: service)

        out = await agents_api.list_project_agent_names(
            project_id=_PROJECT,
            pagination=agents_api.PaginationParams(limit=500, offset=0),
            db=object(),
        )

        assert [o.model_dump() for o in out] == [{"id": _AGENT, "name": "Watcher"}]
        # The whole point: the field that made the full listing expensive is not
        # merely omitted from this response, it is absent from the model.
        assert set(agents_api.AgentNameOut.model_fields) == {"id", "name"}
        assert "system_prompt" in agents_api.AgentOut.model_fields

    async def test_pagination_reaches_the_service_rather_than_being_defaulted_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reason this route exists is that a caller can afford a large page;
        silently capping it here would leave the >100 case exactly where it was."""
        service = MagicMock()
        service.names_for_project = AsyncMock(return_value=[])
        monkeypatch.setattr(agents_api, "AgentService", lambda _db: service)

        await agents_api.list_project_agent_names(
            project_id=_PROJECT,
            pagination=agents_api.PaginationParams(limit=500, offset=40),
            db=object(),
        )

        service.names_for_project.assert_awaited_once_with(_PROJECT, limit=500, offset=40)

    def test_names_and_listing_are_gated_apart_by_design(self) -> None:
        """Matrix row 28: the listing carries the configuration and takes the
        edit capability; the names projection is what every room label reads and
        keeps the membership gate ([R5.07]). Dossier
        2026-10-13-agent-config-visibility-and-project-reads, AC-1/AC-2.

        Asserted on the closure each factory returns, so moving either route onto
        the other's gate fails here.
        """

        def gate_of(fn: Any) -> Any:
            return next(d.dependency for name, d in _defaults(fn).items() if name == "_")

        assert gate_of(agents_api.list_project_agent_names).__qualname__.startswith("require_membership")
        listing_gate = gate_of(agents_api.list_project_agents)
        assert listing_gate.__qualname__.startswith("require.")
        capability = next(
            c.cell_contents
            for c in listing_gate.__closure__ or ()
            if isinstance(c.cell_contents, agents_api.Capability)
        )
        assert capability is agents_api.Capability.RESOURCE_CREATE_EDIT


def _defaults(fn: Any) -> dict[str, Any]:
    import inspect

    return {
        name: param.default
        for name, param in inspect.signature(fn).parameters.items()
        if param.default is not inspect.Parameter.empty
    }


class TestTheQueryProjects:
    """Statement text only — the unit tier compiles, it does not execute."""

    @staticmethod
    def _sql(statement: Any) -> str:
        return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    async def test_it_selects_two_columns_scoped_to_live_agents_of_one_project(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        db.execute.return_value = result

        await AgentRepository(db).names_for_project(_PROJECT, limit=500, offset=0)

        sql = self._sql(db.execute.await_args_list[0].args[0])
        assert "SELECT agents.id, agents.name" in sql
        # Not "system_prompt is not in the response model" — not fetched at all.
        assert "system_prompt" not in sql
        assert str(_PROJECT) in sql
        assert "deleted_at IS NULL" in sql
        assert "LIMIT 500" in sql

    async def test_it_orders_the_same_way_the_full_listing_does(self) -> None:
        """The two paginate identically, so a caller can pair a page from either
        — and an order that is not total would make that false under LIMIT."""
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        db.execute.return_value = result
        repo = AgentRepository(db)

        await repo.names_for_project(_PROJECT, limit=10, offset=0)
        await repo.list_for_project(_PROJECT, limit=10, offset=0)

        projected, full = (self._sql(c.args[0]) for c in db.execute.await_args_list)
        order = "ORDER BY agents.created_at DESC, agents.id DESC"
        assert order in projected
        assert order in full

    async def test_a_soft_deleted_agent_is_absent_unlike_the_by_id_resolver(self) -> None:
        """`names_for_ids` deliberately includes removed agents so an old message
        stays labelled; it resolves a bounded set history already references. A
        project-wide listing has no such bound and would grow with every deletion
        forever, so the caller renders its own unknown-agent label instead."""
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        db.execute.return_value = result
        repo = AgentRepository(db)

        await repo.names_for_project(_PROJECT)
        await repo.names_for_ids([_AGENT])

        projected, by_id = (self._sql(c.args[0]) for c in db.execute.await_args_list)
        assert "deleted_at IS NULL" in projected
        assert "deleted_at IS NULL" not in by_id
