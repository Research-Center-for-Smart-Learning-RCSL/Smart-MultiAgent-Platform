"""rag.py's validate_agent_allowlist — thin wrapper over the shared
deps.validate_agent_allowlist(config_id_attr="rag_config_id") (code review,
2026-07-10: was a hand copy of knowmap.py's equivalent). Mirrors
test_knowmap_authz.py's TestValidateAllowlist for the sibling attribute.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.rag import validate_agent_allowlist

_CONFIG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _agent(agent_id: uuid.UUID, config_id: uuid.UUID | None) -> SimpleNamespace:
    return SimpleNamespace(id=agent_id, rag_config_id=config_id)


def _patch_agents(agents: list[SimpleNamespace]):
    facade = MagicMock()
    facade.return_value.list_agents_for_project = AsyncMock(return_value=agents)
    return patch("contexts.agents.interfaces.facade.AgentsFacade", facade)


class TestValidateAllowlist:
    async def test_empty_is_allowed(self) -> None:
        out = await validate_agent_allowlist(
            db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[]
        )
        assert out == []

    async def test_bound_agents_pass_and_dedup(self) -> None:
        a = uuid.uuid4()
        with _patch_agents([_agent(a, _CONFIG_ID)]):
            out = await validate_agent_allowlist(
                db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[a, a]
            )
        assert out == [a]

    async def test_agent_bound_to_other_config_is_rejected(self) -> None:
        a = uuid.uuid4()
        with _patch_agents([_agent(a, uuid.uuid4())]), pytest.raises(HTTPException) as exc:
            await validate_agent_allowlist(
                db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[a]
            )
        assert exc.value.status_code == 422

    async def test_unknown_agent_is_rejected(self) -> None:
        with _patch_agents([]), pytest.raises(HTTPException) as exc:
            await validate_agent_allowlist(
                db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[uuid.uuid4()]
            )
        assert exc.value.status_code == 422

    async def test_does_not_bind_on_knowmap_config_id(self) -> None:
        # A rag.py allowlist check must ignore knowmap_config_id entirely —
        # otherwise an agent bound only to a Knowledge Map (not this RAG
        # config) would be wrongly accepted.
        a = uuid.uuid4()
        agent = SimpleNamespace(id=a, rag_config_id=None, knowmap_config_id=_CONFIG_ID)
        with _patch_agents([agent]), pytest.raises(HTTPException) as exc:
            await validate_agent_allowlist(
                db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[a]
            )
        assert exc.value.status_code == 422
