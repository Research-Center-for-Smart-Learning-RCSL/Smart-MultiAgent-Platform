"""R9.18 — Agent sampling controls flow domain -> draft -> service -> repo.

Covers the AgentDraft defaults, the create path passing temperature/top_p/seed
to the repository, the patch path persisting set values, and the clear-sentinels
restoring provider-default behaviour (a null in the patch payload). Infrastructure
is mocked; these are field-mapping guardrails, not DB round-trips (that is the
integration layer).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from contexts.agents.domain.models import AgentDraft, AgentModelHint

from .test_agent_service import _KEY_GROUP_ID, _PROJECT_ID, _USER_ID, _make_agent, _make_service


def test_agent_draft_sampling_defaults() -> None:
    draft = AgentDraft()
    assert draft.temperature is None
    assert draft.top_p is None
    assert draft.seed is None
    assert draft.clear_temperature is False
    assert draft.clear_top_p is False
    assert draft.clear_seed is False


@patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
async def test_create_passes_sampling_to_repo(_audit) -> None:
    agent = _make_agent()
    agents = AsyncMock()
    agents.count_active.return_value = 0
    agents.create.return_value = agent
    keys = AsyncMock()
    keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
    svc = _make_service(agent_repo=agents, keys_facade=keys, tool_repo=AsyncMock())

    await svc.create(
        project_id=_PROJECT_ID,
        draft=AgentDraft(
            name="AA",
            model_hint=AgentModelHint.CLAUDE,
            key_group_id=_KEY_GROUP_ID,
            temperature=0.0,
            top_p=1.0,
            seed=42,
        ),
        actor_user_id=_USER_ID,
        actor_ip="1.2.3.4",
    )

    call = agents.create.call_args.kwargs
    assert call["temperature"] == 0.0
    assert call["top_p"] == 1.0
    assert call["seed"] == 42


@patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
async def test_patch_sets_sampling_values(_audit) -> None:
    current = _make_agent(version=1)
    agents = AsyncMock()
    agents.get.return_value = current
    agents.patch.return_value = _make_agent(version=2)
    svc = _make_service(agent_repo=agents)

    await svc.patch(
        agent_id=current.id,
        draft=AgentDraft(temperature=0.0, top_p=1.0, seed=42),
        expected_version=1,
        actor_user_id=_USER_ID,
        actor_ip=None,
    )

    values = agents.patch.call_args.kwargs["values"]
    assert values["temperature"] == 0.0
    assert values["top_p"] == 1.0
    assert values["seed"] == 42


@patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
async def test_patch_clear_sentinels_null_sampling(_audit) -> None:
    current = _make_agent(version=1)
    agents = AsyncMock()
    agents.get.return_value = current
    agents.patch.return_value = _make_agent(version=2)
    svc = _make_service(agent_repo=agents)

    await svc.patch(
        agent_id=current.id,
        draft=AgentDraft(clear_temperature=True, clear_top_p=True, clear_seed=True),
        expected_version=1,
        actor_user_id=_USER_ID,
        actor_ip=None,
    )

    values = agents.patch.call_args.kwargs["values"]
    assert values["temperature"] is None
    assert values["top_p"] is None
    assert values["seed"] is None


@patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
async def test_patch_omits_unset_sampling(_audit) -> None:
    # A patch that doesn't mention sampling must not touch those columns.
    current = _make_agent(version=1)
    agents = AsyncMock()
    agents.get.return_value = current
    agents.patch.return_value = _make_agent(version=2)
    svc = _make_service(agent_repo=agents)

    await svc.patch(
        agent_id=current.id,
        draft=AgentDraft(name="Renamed"),
        expected_version=1,
        actor_user_id=_USER_ID,
        actor_ip=None,
    )

    values = agents.patch.call_args.kwargs["values"]
    assert "temperature" not in values
    assert "top_p" not in values
    assert "seed" not in values
