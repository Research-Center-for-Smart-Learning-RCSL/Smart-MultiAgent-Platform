"""Shipped agent packs: read the catalogue, install one into a project ([R30.35]).

Copy-on-import, not a shared platform row. An `Agent` needs a project-owned key
group carrying a real provider key (`AgentService.assert_key_group_in_project`,
`_assert_key_group_has_provider`), and under BYO-key the platform owns no key, so
the platform-scoped model the activity examples use has no analogue here. The
consequence is a feature rather than a compromise: an installed agent's prompt,
model and temperature are exactly what a project is expected to tune, and a copy
is already tunable.

Nothing is automatic. Installing is a deliberate act by someone who could create
the same agents by hand, and every created agent carries that person as its audit
actor via `AgentService.create`'s own emission.

This service never commits. The caller owns the transaction, which is also what
makes a failed install leave nothing behind: the agents, the group and its
memberships are one unit of work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.interfaces.facade import AgentGroupFacade
from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.errors import AgentPackNotFound, KeyGroupNoMatchingProvider
from contexts.agents.domain.models import AgentDraft, AgentModelHint
from contexts.agents.infrastructure.examples.catalogue import (
    AgentPackDefinition,
    PackAgent,
    PackFileInvalid,
    available_packs,
    load_pack,
)
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.keys.interfaces.facade import KeysFacade

# Shipped packs cannot change without a redeploy, so parsing them once per process
# is safe. Only successful parses are cached: a failure that depended on process
# state must stay retryable rather than frozen in for the worker's lifetime. Same
# shape, and the same reasoning, as the course catalogue's cache.
_PARSED: dict[str, AgentPackDefinition] = {}


def _load_cached(pack_key: str) -> AgentPackDefinition:
    cached = _PARSED.get(pack_key)
    if cached is None:
        cached = load_pack(pack_key)
        _PARSED[pack_key] = cached
    return cached


def clear_pack_cache() -> None:
    """Test hook -- drop the process-global parsed-pack cache."""
    _PARSED.clear()


@dataclass(frozen=True, slots=True)
class CatalogueAgent:
    """One agent a pack would install, and whether this project already has it."""

    key: str
    name: str
    room_role: str | None
    preferred_model_hint: AgentModelHint
    binds_activity_types: tuple[str, ...]
    installed: bool


@dataclass(frozen=True, slots=True)
class CataloguePack:
    pack_key: str
    title: str
    source: str
    for_course: str
    group_name: str
    agents: tuple[CatalogueAgent, ...]

    @property
    def fully_installed(self) -> bool:
        return all(a.installed for a in self.agents)


@dataclass(frozen=True, slots=True)
class InstalledAgent:
    key: str
    name: str
    agent_id: uuid.UUID
    # The hint actually used, which is not always the pack's preference -- the
    # route reports it so the installer sees what was chosen rather than assuming
    # the pack decided.
    model_hint: AgentModelHint


@dataclass(frozen=True, slots=True)
class PackInstallReport:
    pack_key: str
    created: tuple[InstalledAgent, ...]
    # Names, not keys: idempotency is by name (see `install_pack`).
    already_present: tuple[str, ...]
    group_id: uuid.UUID
    # The group is matched by name, so renaming it makes the next install create a
    # second one holding the same agents. That is left as it is (see `_group_for`);
    # without this flag the report would read "created nothing" for such a run,
    # which is the one outcome an installer must not be told wrongly.
    group_created: bool


class AgentExampleService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._agents = AgentService(db)
        self._agent_repo = AgentRepository(db)
        self._groups = AgentGroupFacade(db)
        self._keys = KeysFacade(db)

    # -- Catalogue ----------------------------------------------------------

    async def list_catalogue(self, project_id: uuid.UUID) -> tuple[CataloguePack, ...]:
        """Every shipped pack, annotated with what this project already has.

        A pack that fails to parse is skipped rather than failing the listing: one
        malformed file must not hide the packs that could still be installed. It is
        logged rather than swallowed, because an omitted pack looks exactly like a
        pack that was never shipped and an operator would have nothing to go on.
        `install_pack` still surfaces the parse failure when a caller names that
        specific pack.

        Only `PackFileInvalid` is caught. A broader `ValueError` here would also
        swallow a genuine defect in this method and report it as a missing pack.
        """
        packs: list[AgentPackDefinition] = []
        for pack_key in available_packs():
            try:
                packs.append(_load_cached(pack_key))
            except PackFileInvalid:
                logger.warning("shipped agent pack {} is not loadable; omitted from the catalogue", pack_key)
                continue

        existing = {a.name for a in await self._agent_repo.list_for_project(project_id)}

        return tuple(
            CataloguePack(
                pack_key=pack.pack_key,
                title=pack.title,
                source=pack.source,
                for_course=pack.for_course,
                group_name=pack.group_name,
                agents=tuple(
                    CatalogueAgent(
                        key=agent.key,
                        name=agent.name,
                        room_role=agent.room_role,
                        preferred_model_hint=agent.preferred_model_hint,
                        binds_activity_types=agent.binds_activity_types,
                        installed=agent.name in existing,
                    )
                    for agent in pack.agents
                ),
            )
            for pack in packs
        )

    # -- Install ------------------------------------------------------------

    async def install_pack(
        self,
        *,
        project_id: uuid.UUID,
        pack_key: str,
        key_group_id: uuid.UUID,
        model_hint: AgentModelHint | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> PackInstallReport:
        """Instantiate a pack's agents into one project, plus one agent group.

        Idempotent **by agent name within the project**, which is weaker than the
        course installer's key idempotency and deliberately so: `agents` has no
        `key` column and no name uniqueness, and adding one for the sake of an
        example would be the wrong trade. The consequence is stated rather than
        hidden -- renaming an installed agent and re-installing produces a second
        copy.

        Re-installing does converge on the group, though: every agent the pack
        names is added to it, whether this run created it or found it already
        there. Recovering a deleted group, or folding in an agent someone had
        hand-created under the pack's name, both work.

        Creates no chatroom and binds no room. Installing is the owner's act;
        starting a class is the facilitator's.
        """
        if pack_key not in available_packs():
            # Distinguished from a malformed shipped file, which is a server fault
            # and must not be reported to a client as "no such pack".
            raise AgentPackNotFound(pack_key)
        pack = _load_cached(pack_key)

        # Ownership before capability, and the order is load-bearing.
        # `has_carried_provider_in_group` answers for *any* group id, so probing
        # first and checking ownership second would let a member of project A
        # learn which providers project B's key group carries by reading which
        # refusal came back. `AgentService.create` re-checks this authoritatively;
        # running it here is what keeps the probe below from ever seeing a group
        # the caller does not own.
        await self._agents.assert_key_group_in_project(
            key_group_id=key_group_id,
            project_id=project_id,
        )

        # Every provider question answered before anything is created, so a key
        # group that cannot serve the pack is refused rather than half-installed.
        usable = await self._usable_hints(key_group_id)
        if not usable:
            raise KeyGroupNoMatchingProvider(
                f"key_group {key_group_id} carries no provider key, so no agent could run"
            )
        if model_hint is not None and model_hint not in usable:
            raise KeyGroupNoMatchingProvider(
                f"key_group {key_group_id} has no carried keys for provider {model_hint.value!r}"
            )

        existing = {a.name: a.id for a in await self._agent_repo.list_for_project(project_id)}

        created: list[InstalledAgent] = []
        already_present: list[str] = []
        # Every agent the pack names, created here or found already present. The
        # group is the pack's end state, not a record of what this run happened to
        # create: an agent the project already had under the pack's name -- from an
        # earlier install, or hand-authored -- belongs in it too, and leaving it out
        # would be an omission nothing in the report mentions.
        members: list[uuid.UUID] = []
        for pack_agent in pack.agents:
            if pack_agent.name in existing:
                already_present.append(pack_agent.name)
                members.append(existing[pack_agent.name])
                continue
            hint = self._hint_for(pack_agent, override=model_hint, usable=usable)
            # create() emits agent.created with the installer as actor, so the
            # install needs no audit event of its own per agent.
            agent = await self._agents.create(
                project_id=project_id,
                draft=AgentDraft(
                    name=pack_agent.name,
                    model_hint=hint,
                    key_group_id=key_group_id,
                    system_prompt=pack_agent.system_prompt,
                    temperature=pack_agent.temperature,
                    wakeup_config=pack_agent.wakeup_config,
                ),
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            created.append(
                InstalledAgent(
                    key=pack_agent.key,
                    name=pack_agent.name,
                    agent_id=agent.id,
                    model_hint=hint,
                )
            )
            members.append(agent.id)

        group_id, group_created = await self._group_for(
            project_id=project_id,
            name=pack.group_name,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        for agent_id in members:
            # Idempotent, and only a real insert emits `agent_group.member_added`
            # (`AgentGroupService.add_member`), so re-installing adds no audit
            # noise for memberships that already existed.
            await self._groups.add_member(
                group_id=group_id,
                agent_id=agent_id,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        return PackInstallReport(
            pack_key=pack.pack_key,
            created=tuple(created),
            already_present=tuple(already_present),
            group_id=group_id,
            group_created=group_created,
        )

    # -- Internals ----------------------------------------------------------

    async def _usable_hints(self, key_group_id: uuid.UUID) -> tuple[AgentModelHint, ...]:
        """Providers this key group can actually serve, in enum order.

        Three indexed reads, once, before any write. `AgentService.create` asks the
        same question per agent, so this is a pre-check rather than the
        authoritative gate -- the point is to fail before the first create rather
        than during the third.
        """
        return tuple(
            [
                hint
                for hint in AgentModelHint
                if await self._keys.has_carried_provider_in_group(key_group_id, hint.value)
            ]
        )

    @staticmethod
    def _hint_for(
        agent: PackAgent,
        *,
        override: AgentModelHint | None,
        usable: tuple[AgentModelHint, ...],
    ) -> AgentModelHint:
        """An explicit choice wins, then the pack's preference, then what works.

        The fallback is what keeps a shipped example installable: hard-coding a
        provider would make the pack uninstallable for any project holding a
        different vendor's key, and an example nobody can install teaches nothing.
        """
        if override is not None:
            return override
        if agent.preferred_model_hint in usable:
            return agent.preferred_model_hint
        return usable[0]

    async def _group_for(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> tuple[uuid.UUID, bool]:
        """Reuse the pack's group if it is already there, else create it.

        By name, matching how agents are deduplicated, so a re-install lands its
        new agents in the same group rather than creating a second one beside it.
        Renaming the group is a supported owner route, and once the name has moved
        this creates a second group rather than finding the first -- the caller is
        told which happened so the report can say so, because matching on something
        stabler would mean re-populating a group the owner deliberately renamed
        away from.
        """
        for group in await self._groups.list_groups(project_id):
            if group.name == name:
                return group.id, False
        group_id = await self._groups.create_group(
            project_id=project_id,
            name=name,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        return group_id, True


__all__ = [
    "AgentExampleService",
    "CatalogueAgent",
    "CataloguePack",
    "InstalledAgent",
    "PackInstallReport",
    "clear_pack_cache",
]
