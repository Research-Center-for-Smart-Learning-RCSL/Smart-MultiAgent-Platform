"""Room wake-up layers on real PostgreSQL ([R15.09], migration 0104).

Phase 2 AC-10/AC-13: the CAS write, the fresh incarnation a rebind creates,
the keyset page the refresh sweep reads, and the 0104 data step that restores
designer defaults from the authored snapshot. JSONB round-trips and tuple
comparison on ``(timestamptz, uuid)`` need a real engine, not literal binds.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.infrastructure import tables as agt
from contexts.conversation.infrastructure import tables as t
from contexts.conversation.infrastructure.repositories.chatroom_repo import ChatroomAgentRepository
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db


@pytest.fixture
async def room_agent(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """``(chatroom_id, second_chatroom_id, agent_id)``, the agent bound to both rooms."""
    project_id, user_id = project
    workspace_id, room_a, room_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    agent_id, key_group_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="wk-itest")
        )
        for room in (room_a, room_b):
            await session.execute(
                t.chatrooms.insert().values(
                    id=room,
                    workspace_id=workspace_id,
                    name="wk-itest",
                    guest_token=str(uuid.uuid4()),
                    created_by_user_id=user_id,
                )
            )
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="wk-kg")
        )
        await session.execute(
            agt.agents.insert().values(
                id=agent_id,
                project_id=project_id,
                name="wk-agent",
                model_hint="claude",
                key_group_id=key_group_id,
            )
        )
        for room in (room_a, room_b):
            await session.execute(t.chatroom_agents.insert().values(chatroom_id=room, agent_id=agent_id))
        await session.commit()
    try:
        yield room_a, room_b, agent_id
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(agt.agents.delete().where(agt.agents.c.id == agent_id))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == key_group_id))
            await cleanup.commit()


class TestBindingLayers:
    async def test_a_new_binding_starts_empty_with_its_own_id(
        self, sessionmaker: async_sessionmaker[AsyncSession], room_agent
    ) -> None:
        room_a, room_b, agent_id = room_agent
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            a = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            b = await repo.wakeup_state(chatroom_id=room_b, agent_id=agent_id)

        assert a is not None
        assert b is not None
        assert a.binding_id != b.binding_id
        assert (a.version, a.room_overrides, a.runtime_overrides, a.runtime_started_at) == (1, {}, {}, None)

    async def test_cas_writes_one_room_and_refuses_a_stale_version(
        self, sessionmaker: async_sessionmaker[AsyncSession], room_agent
    ) -> None:
        room_a, room_b, agent_id = room_agent
        started = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            a = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            assert a is not None
            written = await repo.cas_wakeup_layers(
                binding_id=a.binding_id,
                expected_version=1,
                room_overrides={"every_n_messages": 5},
                runtime_overrides={"silence_minutes_enabled": False},
                runtime_started_at=started,
            )
            stale = await repo.cas_wakeup_layers(
                binding_id=a.binding_id,
                expected_version=1,
                room_overrides={},
                runtime_overrides={},
                runtime_started_at=None,
            )
            await session.commit()
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            a2 = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            b2 = await repo.wakeup_state(chatroom_id=room_b, agent_id=agent_id)

        assert written is not None
        assert written.version == 2
        assert stale is None
        assert a2 is not None
        assert (a2.room_overrides, a2.runtime_overrides, a2.runtime_started_at) == (
            {"every_n_messages": 5},
            {"silence_minutes_enabled": False},
            started,
        )
        assert b2 is not None
        assert (b2.version, b2.room_overrides) == (1, {})

    async def test_unbind_and_rebind_is_a_new_incarnation(
        self, sessionmaker: async_sessionmaker[AsyncSession], room_agent
    ) -> None:
        room_a, _room_b, agent_id = room_agent
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            old = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            assert old is not None
            await repo.cas_wakeup_layers(
                binding_id=old.binding_id,
                expected_version=1,
                room_overrides={"every_n_messages": 5},
                runtime_overrides={},
                runtime_started_at=None,
            )
            await repo.remove(chatroom_id=room_a, agent_id=agent_id)
            await repo.add(chatroom_id=room_a, agent_id=agent_id)
            fresh = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            late_write = await repo.cas_wakeup_layers(
                binding_id=old.binding_id,
                expected_version=2,
                room_overrides={"every_n_messages": 9},
                runtime_overrides={},
                runtime_started_at=None,
            )
            await session.rollback()

        assert fresh is not None
        assert fresh.binding_id != old.binding_id
        assert (fresh.version, fresh.room_overrides) == (1, {})
        assert late_write is None

    async def test_a_role_change_clears_the_layers_and_bumps_the_version(
        self, sessionmaker: async_sessionmaker[AsyncSession], room_agent
    ) -> None:
        from contexts.conversation.domain.models import ChatroomAgentRole

        room_a, _room_b, agent_id = room_agent
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            state = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            assert state is not None
            await repo.cas_wakeup_layers(
                binding_id=state.binding_id,
                expected_version=1,
                room_overrides={"autostop_rounds": 7},
                runtime_overrides={"every_n_messages": 4},
                runtime_started_at=datetime(2026, 10, 16, tzinfo=UTC),
            )
            assert await repo.set_role(
                chatroom_id=room_a,
                agent_id=agent_id,
                expected_role=ChatroomAgentRole.NORMAL,
                role=ChatroomAgentRole.OBSERVER,
            )
            after = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            await session.rollback()

        assert after is not None
        assert (after.room_overrides, after.runtime_overrides, after.runtime_started_at) == ({}, {}, None)
        assert after.version == 3
        assert after.binding_id == state.binding_id

    async def test_the_refresh_page_reads_only_started_layers_in_keyset_order(
        self, sessionmaker: async_sessionmaker[AsyncSession], room_agent
    ) -> None:
        room_a, room_b, agent_id = room_agent
        t0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            a = await repo.wakeup_state(chatroom_id=room_a, agent_id=agent_id)
            b = await repo.wakeup_state(chatroom_id=room_b, agent_id=agent_id)
            assert a is not None
            assert b is not None
            for state, at in ((a, t0 + timedelta(hours=1)), (b, t0)):
                await repo.cas_wakeup_layers(
                    binding_id=state.binding_id,
                    expected_version=1,
                    room_overrides={},
                    runtime_overrides={"every_n_messages": 4},
                    runtime_started_at=at,
                )
            mine = {a.binding_id, b.binding_id}
            first = [s for s in await repo.runtime_layer_states(limit=1000) if s.binding_id in mine]
            after_b = [
                s
                for s in await repo.runtime_layer_states(limit=1000, after=(t0, b.binding_id))
                if s.binding_id in mine
            ]
            await session.rollback()

        assert [s.binding_id for s in first] == [b.binding_id, a.binding_id]
        assert [s.binding_id for s in after_b] == [a.binding_id]


class TestMigration0104DataStep:
    async def test_the_restore_statement_backs_up_then_restores_the_snapshot(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """The upgrade's two statements, re-run on rows written the old way: a
        drifted config is backed up and replaced by its snapshot, an explicit
        ``{}`` snapshot is restored too, a missing one leaves the config alone,
        and a second run is a no-op."""
        import importlib.util
        from pathlib import Path

        path = Path(__file__).parents[2] / "alembic" / "versions" / "0104_room_wakeup_layers.py"
        spec = importlib.util.spec_from_file_location("migration_0104", path)
        assert spec is not None
        assert spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        project_id, _ = project
        kg = uuid.uuid4()
        drifted, empty_snapshot, no_snapshot, json_null = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        rows = {
            drifted: (
                {"triggers": {"every_n_messages": {"n": 9}}},
                {"triggers": {"every_n_messages": {"n": 3}}},
            ),
            empty_snapshot: ({"triggers": {"every_n_messages": {"n": 9}}}, {}),
            no_snapshot: ({"triggers": {"every_n_messages": {"n": 9}}}, sa.null()),
            # What AgentRepository.create stores for no snapshot: pg.JSONB writes
            # Python None as the JSON value null, not SQL NULL.
            json_null: ({"triggers": {"every_n_messages": {"n": 9}}}, None),
        }
        async with sessionmaker() as session:
            await session.execute(key_groups_t.insert().values(id=kg, project_id=project_id, name="mig-kg"))
            for aid, (config, snapshot) in rows.items():
                await session.execute(
                    agt.agents.insert().values(
                        id=aid,
                        project_id=project_id,
                        name=f"mig-agent-{aid}",
                        model_hint="claude",
                        key_group_id=kg,
                        wakeup_config=config,
                        wakeup_authored_snapshot=snapshot,
                    )
                )
            await session.commit()
        try:
            statements = migration.data_step_sql()
            async with sessionmaker() as session:
                for _ in range(2):
                    for statement in statements:
                        await session.execute(sa.text(statement))
                got = {
                    r.id: (r.wakeup_config, r.version)
                    for r in (
                        await session.execute(
                            sa.select(
                                agt.agents.c.id, agt.agents.c.wakeup_config, agt.agents.c.version
                            ).where(agt.agents.c.id.in_(list(rows)))
                        )
                    ).all()
                }
                backup = {
                    r.agent_id: r.wakeup_config
                    for r in (
                        await session.execute(
                            sa.text(
                                "SELECT agent_id, wakeup_config FROM agent_wakeup_config_backup_0104 "
                                "WHERE agent_id = ANY(:ids)"
                            ),
                            {"ids": list(rows)},
                        )
                    ).all()
                }
                await session.rollback()
        finally:
            async with sessionmaker() as cleanup:
                await cleanup.execute(agt.agents.delete().where(agt.agents.c.id.in_(list(rows))))
                await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == kg))
                await cleanup.commit()

        assert got[drifted][0] == {"triggers": {"every_n_messages": {"n": 3}}}
        assert got[empty_snapshot][0] == {}
        assert got[no_snapshot][0] == got[json_null][0] == {"triggers": {"every_n_messages": {"n": 9}}}
        # Bumped once each, not again on the second run.
        assert got[drifted][1] == got[empty_snapshot][1] == 2
        assert got[no_snapshot][1] == got[json_null][1] == 1
        assert backup == {
            drifted: {"triggers": {"every_n_messages": {"n": 9}}},
            empty_snapshot: {"triggers": {"every_n_messages": {"n": 9}}},
        }
