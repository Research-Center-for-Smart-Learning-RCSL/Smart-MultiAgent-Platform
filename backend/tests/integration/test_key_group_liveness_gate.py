"""R7.09a — a soft-deleted Key Group must stop yielding carried members (§8.2/§8.4,
AC-1/AC-4/AC-6 of 2026-07-16-headless-turn-key-group-authz).

Every unit test of ``_load_eligible`` / ``resolve_embed_key`` / ``patch_member``
fakes the members repository, so none of them can see whether the real join
actually filters ``key_groups.deleted_at`` -- that is the root cause this task
fixes (``group_repository.py``'s ``list_ordered_carried``). A fake standing in
for that repository can only prove its *caller's* behaviour given a canned
result; proving the join itself gates on group liveness needs the real
statement against Postgres, which is what all three tests here exercise.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contexts.identity.infrastructure.tables import users as users_t
from contexts.keys.application.group_service import KeyGroupService
from contexts.keys.domain.errors import KeyNotFound
from contexts.keys.infrastructure import tables as t
from contexts.keys.infrastructure.group_repository import KeyGroupMemberRepository
from contexts.knowledge.application.embed_resolution import resolve_embed_key
from contexts.tenancy.infrastructure.tables import projects as projects_t


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from app.config.settings import get_settings

    engine = create_async_engine(get_settings().database.dsn)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def project(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """(project_id, owner_user_id) -- real FK targets for key_groups/api_keys."""
    pid, uid = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            users_t.insert().values(
                id=uid,
                email=f"kg-liveness-{uid}@test.invalid",
                password_hash="x",  # never authenticated against
            )
        )
        await session.execute(
            projects_t.insert().values(
                id=pid,
                name="kg-liveness",
                owner_user_id=uid,
                created_by_user_id=uid,
            )
        )
        await session.commit()
    try:
        yield pid, uid
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(projects_t.delete().where(projects_t.c.id == pid))
            await cleanup.execute(users_t.delete().where(users_t.c.id == uid))
            await cleanup.commit()


async def _seed_group_with_carried_key(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    group_id: uuid.UUID,
    key_id: uuid.UUID,
    provider: str = "openai",
) -> None:
    await session.execute(t.key_groups.insert().values(id=group_id, project_id=project_id, name="g"))
    await session.execute(
        t.api_keys.insert().values(
            id=key_id,
            owner_user_id=owner_user_id,
            provider=provider,
            name="k",
            ciphertext=b"ciphertext",
            nonce=b"nonce",
            dek_wrapped="wrapped",
            ciphertext_hmac=b"hmac",
            masked_preview="sk-...xyz",
        )
    )
    await session.execute(t.key_projects.insert().values(key_id=key_id, project_id=project_id, carried=True))
    await session.execute(t.key_group_members.insert().values(group_id=group_id, key_id=key_id, priority=1))


async def _soft_delete_group(session: AsyncSession, group_id: uuid.UUID) -> None:
    await session.execute(
        t.key_groups.update().where(t.key_groups.c.id == group_id).values(deleted_at=datetime.now(tz=UTC))
    )


@pytest.mark.asyncio
async def test_list_ordered_carried_excludes_soft_deleted_group(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """AC-1: the join returns a live group's carried member, and returns nothing
    once the group itself is soft-deleted -- the root cause this task fixes."""
    project_id, owner_user_id = project
    group_id, key_id = uuid.uuid4(), uuid.uuid4()

    async with sessionmaker() as setup:
        await _seed_group_with_carried_key(
            setup, project_id=project_id, owner_user_id=owner_user_id, group_id=group_id, key_id=key_id
        )
        await setup.commit()

    async with sessionmaker() as session:
        live = await KeyGroupMemberRepository(session).list_ordered_carried(group_id)
    assert [m.key_id for m in live] == [key_id]

    async with sessionmaker() as session:
        await _soft_delete_group(session, group_id)
        await session.commit()

    async with sessionmaker() as session:
        after_delete = await KeyGroupMemberRepository(session).list_ordered_carried(group_id)
    assert after_delete == []


@pytest.mark.asyncio
async def test_resolve_embed_key_selects_nothing_for_deleted_builder_group(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """AC-4: resolve_embed_key -- shared by the GraphRAG builder and RAG retrieval,
    both reached only through the same list_ordered_carried join -- must resolve
    nothing once the builder key group is deleted, not the last-known carried key."""
    project_id, owner_user_id = project
    group_id, key_id = uuid.uuid4(), uuid.uuid4()

    async with sessionmaker() as setup:
        await _seed_group_with_carried_key(
            setup, project_id=project_id, owner_user_id=owner_user_id, group_id=group_id, key_id=key_id
        )
        await setup.commit()

    async with sessionmaker() as session:
        resolved = await resolve_embed_key(session, group_id)
    assert resolved == ("openai", "text-embedding-3-small", key_id)

    async with sessionmaker() as session:
        await _soft_delete_group(session, group_id)
        await session.commit()

    async with sessionmaker() as session:
        after_delete = await resolve_embed_key(session, group_id)
    assert after_delete is None


@pytest.mark.asyncio
async def test_patch_member_on_deleted_group_raises_key_not_found(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """AC-6 (§9 risk, deliberate): patch_member's existence check is
    list_ordered_carried, unlike get_with_members it is not gated by get_active
    first either -- both share this one query. After the join gates on group
    liveness, patching a deleted group's member now 404s instead of succeeding.
    The direction is right (a deleted group's members should not be editable);
    this pins it as an accepted behaviour change, not a surprise."""
    project_id, owner_user_id = project
    group_id, key_id = uuid.uuid4(), uuid.uuid4()

    async with sessionmaker() as setup:
        await _seed_group_with_carried_key(
            setup, project_id=project_id, owner_user_id=owner_user_id, group_id=group_id, key_id=key_id
        )
        await _soft_delete_group(setup, group_id)
        await setup.commit()

    async with sessionmaker() as session:
        svc = KeyGroupService(session)
        with pytest.raises(KeyNotFound):
            await svc.patch_member(
                group_id=group_id,
                key_id=key_id,
                col_updates={"priority": 2},
                actor_user_id=owner_user_id,  # audit_logs.actor_user_id is a real FK
            )
