"""AC-8/AC-12: installing a shipped agent pack into a project.

Runs against the *real* shipped packs rather than a fixture, because half of what
AC-8 asserts is that the created agents carry the pack's own values -- a fixture
would only prove the service copies whatever it was handed.

`AgentService` is doubled here rather than run for real: it takes a PostgreSQL
advisory lock (`agent_service.create`), which a unit-tier session cannot provide.
What that costs is visible and deliberate -- the `agent.created` audit emission
lives inside that service, so this file asserts the *call*, and the emission
itself stays covered by `test_agent_service.py`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.agent_groups.domain.models import AgentGroup
from contexts.agents.application import example_service
from contexts.agents.application.example_service import AgentExampleService
from contexts.agents.domain.errors import AgentPackNotFound, KeyGroupNoMatchingProvider
from contexts.agents.domain.models import Agent, AgentModelHint, ContextMode
from contexts.agents.infrastructure.examples.catalogue import load_pack

_NOW = dt.datetime(2026, 8, 13, tzinfo=dt.UTC)
_ROOM_PACK = "creative-thinking-room"
_DESIGN_PACK = "creative-thinking-design"


@pytest.fixture(autouse=True)
def _clean_pack_cache() -> Any:
    """The parsed-pack cache is process-global, so a pack one test patches must
    not leak into the next test or out of this module."""
    example_service.clear_pack_cache()
    yield
    example_service.clear_pack_cache()


def _agent_row(name: str) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name=name,
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        effort=None,
        key_group_id=uuid.uuid4(),
        system_prompt="",
        rag_config_id=None,
        knowmap_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        skill_index_token_cap=None,
        temperature=None,
        top_p=None,
        seed=None,
        a2a_enabled=False,
        wakeup_config={},
        wakeup_authored_snapshot=None,
        workflow_capabilities={},
        version=1,
        deleted_at=None,
        created_at=_NOW,
    )


def _service(
    *,
    existing_names: list[str] | None = None,
    providers: list[str] | None = None,
    groups: list[Any] | None = None,
) -> tuple[AgentExampleService, dict[str, Any]]:
    """A service with every collaborator doubled, plus the doubles for assertion."""
    service = AgentExampleService(MagicMock())

    agents = MagicMock()
    agents.create = AsyncMock(side_effect=lambda **kw: _agent_row(kw["draft"].name))
    service._agents = agents  # type: ignore[assignment]

    repo = MagicMock()
    repo.list_for_project = AsyncMock(return_value=[_agent_row(n) for n in (existing_names or [])])
    service._agent_repo = repo  # type: ignore[assignment]

    carried = set(providers if providers is not None else ["claude"])
    keys = MagicMock()
    keys.has_carried_provider_in_group = AsyncMock(side_effect=lambda _g, p: p in carried)
    service._keys = keys  # type: ignore[assignment]

    group_facade = MagicMock()
    group_facade.list_groups = AsyncMock(return_value=groups or [])
    group_facade.create_group = AsyncMock(return_value=uuid.uuid4())
    group_facade.add_member = AsyncMock()
    service._groups = group_facade  # type: ignore[assignment]

    return service, {
        "agents": agents,
        "repo": repo,
        "keys": keys,
        "groups": group_facade,
    }


async def _install(
    service: AgentExampleService,
    *,
    pack_key: str = _ROOM_PACK,
    model_hint: AgentModelHint | None = None,
) -> Any:
    return await service.install_pack(
        project_id=uuid.uuid4(),
        pack_key=pack_key,
        key_group_id=uuid.uuid4(),
        model_hint=model_hint,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )


class TestInstallPack:
    async def test_creates_every_agent_with_the_packs_own_values(self) -> None:
        """AC-8, asserted against the shipped file field for field."""
        service, doubles = _service()
        pack = load_pack(_ROOM_PACK)

        report = await _install(service)

        assert [a.name for a in report.created] == [a.name for a in pack.agents]
        assert report.already_present == ()
        drafts = [call.kwargs["draft"] for call in doubles["agents"].create.await_args_list]
        for draft, pack_agent in zip(drafts, pack.agents, strict=True):
            assert draft.system_prompt == pack_agent.system_prompt
            assert draft.temperature == pack_agent.temperature
            assert draft.wakeup_config == pack_agent.wakeup_config

    async def test_puts_every_created_agent_in_one_group_named_by_the_pack(self) -> None:
        service, doubles = _service()
        pack = load_pack(_ROOM_PACK)

        report = await _install(service)

        doubles["groups"].create_group.assert_awaited_once()
        assert doubles["groups"].create_group.await_args.kwargs["name"] == pack.group_name
        assert doubles["groups"].add_member.await_count == len(pack.agents)
        assert report.group_id == doubles["groups"].create_group.return_value

    async def test_reuses_an_existing_group_of_the_same_name(self) -> None:
        """A re-install lands its new agents beside the old ones, not in a second
        group sitting next to the first."""
        pack = load_pack(_ROOM_PACK)
        # The real dataclass, not a MagicMock: `name` is a constructor keyword on
        # MagicMock itself, so `MagicMock(name=...)` names the mock and leaves
        # `.name` returning a child mock that matches nothing.
        existing = AgentGroup(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name=pack.group_name,
            concept_map_enabled=False,
            created_at=_NOW,
        )
        service, doubles = _service(groups=[existing])

        report = await _install(service)

        doubles["groups"].create_group.assert_not_awaited()
        assert report.group_id == existing.id

    async def test_a_second_install_reports_already_present_and_creates_nothing(self) -> None:
        """Idempotency is by name, which is what `agents` can offer -- it has no
        key column and no name uniqueness."""
        pack = load_pack(_ROOM_PACK)
        service, doubles = _service(existing_names=[a.name for a in pack.agents])

        report = await _install(service)

        assert report.created == ()
        assert report.already_present == tuple(a.name for a in pack.agents)
        doubles["agents"].create.assert_not_awaited()

    async def test_a_partial_install_fills_only_the_gap(self) -> None:
        pack = load_pack(_ROOM_PACK)
        service, doubles = _service(existing_names=[pack.agents[0].name])

        report = await _install(service)

        assert [a.name for a in report.created] == [a.name for a in pack.agents[1:]]
        assert report.already_present == (pack.agents[0].name,)

    async def test_the_design_pack_installs_its_agent_alone(self) -> None:
        """AC-14: DA is not in the room pack, so installing that one cannot put a
        design agent in front of a class."""
        service, _ = _service()

        report = await _install(service, pack_key=_DESIGN_PACK)

        assert [a.key for a in report.created] == ["da-lesson-designer"]

    async def test_binds_no_room_and_creates_no_chatroom(self) -> None:
        """Installing is the owner's act; starting a class is the facilitator's.

        Asserted structurally: the service holds no conversation collaborator, so
        there is no path from here to a room binding.
        """
        service, _ = _service()

        await _install(service)

        assert not hasattr(service, "_conversation")


class TestModelHintResolution:
    async def test_uses_the_packs_preference_when_the_key_group_carries_it(self) -> None:
        service, _ = _service(providers=["claude", "openai"])

        report = await _install(service)

        assert {a.model_hint for a in report.created} == {AgentModelHint.CLAUDE}

    async def test_falls_back_to_a_provider_the_group_does_carry(self) -> None:
        """AC-12. A hard-coded provider would make the pack uninstallable for any
        project holding a different vendor's key."""
        service, _ = _service(providers=["gemini"])

        report = await _install(service)

        assert {a.model_hint for a in report.created} == {AgentModelHint.GEMINI}

    async def test_an_explicit_override_wins_over_the_preference(self) -> None:
        service, _ = _service(providers=["claude", "openai"])

        report = await _install(service, model_hint=AgentModelHint.OPENAI)

        assert {a.model_hint for a in report.created} == {AgentModelHint.OPENAI}

    async def test_an_override_the_group_cannot_serve_is_refused(self) -> None:
        """Silently overriding an explicit choice would be worse than refusing."""
        service, doubles = _service(providers=["claude"])

        with pytest.raises(KeyGroupNoMatchingProvider, match="openai"):
            await _install(service, model_hint=AgentModelHint.OPENAI)

        doubles["agents"].create.assert_not_awaited()

    async def test_a_key_group_carrying_nothing_is_refused_before_any_create(self) -> None:
        """AC-12's other half: no partial install."""
        service, doubles = _service(providers=[])

        with pytest.raises(KeyGroupNoMatchingProvider):
            await _install(service)

        doubles["agents"].create.assert_not_awaited()
        doubles["groups"].create_group.assert_not_awaited()

    async def test_the_provider_probe_runs_once_per_provider_not_once_per_agent(self) -> None:
        """Three reads before the first write, rather than one per agent."""
        service, doubles = _service(providers=["claude"])

        await _install(service)

        assert doubles["keys"].has_carried_provider_in_group.await_count == len(AgentModelHint)


class TestUnknownPack:
    async def test_an_unknown_pack_key_is_a_domain_not_found(self) -> None:
        """Distinct from a malformed shipped file, which is a server fault."""
        service, _ = _service()

        with pytest.raises(AgentPackNotFound):
            await _install(service, pack_key="no-such-pack")

    async def test_a_traversal_key_never_reaches_the_filesystem(self) -> None:
        """`pack_key` arrives from an HTTP path parameter."""
        service, _ = _service()

        with pytest.raises(AgentPackNotFound):
            await _install(service, pack_key="../secrets")


class TestCatalogue:
    async def test_lists_both_packs_with_their_install_state(self) -> None:
        pack = load_pack(_ROOM_PACK)
        service, _ = _service(existing_names=[pack.agents[0].name])

        catalogue = await service.list_catalogue(uuid.uuid4())

        by_key = {p.pack_key: p for p in catalogue}
        assert set(by_key) == {_ROOM_PACK, _DESIGN_PACK}
        room = by_key[_ROOM_PACK]
        assert [a.installed for a in room.agents] == [True, False, False]
        assert room.fully_installed is False

    async def test_reports_a_fully_installed_pack(self) -> None:
        pack = load_pack(_DESIGN_PACK)
        service, _ = _service(existing_names=[pack.agents[0].name])

        catalogue = await service.list_catalogue(uuid.uuid4())

        design = next(p for p in catalogue if p.pack_key == _DESIGN_PACK)
        assert design.fully_installed is True

    async def test_carries_the_course_link_and_the_bound_types_through(self) -> None:
        """What the dialog needs to say which activities a pack is written for."""
        service, _ = _service()

        catalogue = await service.list_catalogue(uuid.uuid4())

        room = next(p for p in catalogue if p.pack_key == _ROOM_PACK)
        assert room.for_course == "creative-thinking"
        assert all(a.binds_activity_types for a in room.agents)
