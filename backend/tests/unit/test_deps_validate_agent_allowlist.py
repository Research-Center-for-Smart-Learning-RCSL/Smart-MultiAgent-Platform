"""deps.validate_agent_allowlist — the shared allowlist-validation gate.

Was hand-copied identically (differing only in the config-id attribute name)
across rag.py and knowmap.py; collapsed here (code review, 2026-07-10). This
tests the generic implementation directly; test_rag_allowlist_validation.py
and test_knowmap_authz.py test each module's thin wrapper.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.deps import validate_agent_allowlist

_CONFIG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _patch_agents(agents: list[SimpleNamespace]):
    facade = MagicMock()
    facade.return_value.list_agents_for_project = AsyncMock(return_value=agents)
    return patch("contexts.agents.interfaces.facade.AgentsFacade", facade)


@pytest.mark.parametrize("attr", ["rag_config_id", "knowmap_config_id"])
class TestConfigIdAttrIsRespected:
    async def test_bound_agent_passes(self, attr: str) -> None:
        a = uuid.uuid4()
        agent = SimpleNamespace(id=a, **{attr: _CONFIG_ID})
        with _patch_agents([agent]):
            out = await validate_agent_allowlist(
                db=AsyncMock(),
                config_id=_CONFIG_ID,
                project_id=_PROJECT_ID,
                agent_ids=[a],
                config_id_attr=attr,
            )
        assert out == [a]

    async def test_agent_bound_to_a_different_attr_value_is_rejected(self, attr: str) -> None:
        a = uuid.uuid4()
        agent = SimpleNamespace(id=a, **{attr: uuid.uuid4()})
        with _patch_agents([agent]), pytest.raises(HTTPException) as exc:
            await validate_agent_allowlist(
                db=AsyncMock(),
                config_id=_CONFIG_ID,
                project_id=_PROJECT_ID,
                agent_ids=[a],
                config_id_attr=attr,
            )
        assert exc.value.status_code == 422
