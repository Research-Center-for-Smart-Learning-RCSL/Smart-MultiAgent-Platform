"""Real-Postgres barrier tests for the restore/teardown race (F-7).

Spec: ``docs/tasks/2026-07-17-retention-restore-teardown-race/spec.md`` §8.2-§8.5.

The unit tests pin the *ordering* — teardown follows the committed delete — but
they cannot show that the ordering is enforceable, because a mock session has no
row locks and no transaction isolation. What actually makes the fix safe is a
Postgres property: a ``DELETE ... WHERE deleted_at IS NOT NULL`` blocks on a
concurrent uncommitted restore, then re-evaluates its predicate under READ
COMMITTED and matches nothing. These tests prove that property holds, in both
directions, for direct projects, org cascades, and the admin GDPR path.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.identity.infrastructure.tables import users as users_t
from contexts.tenancy.infrastructure.tables import orgs as orgs_t
from contexts.tenancy.infrastructure.tables import projects as projects_t

# Comfortably past SOFT_DELETE_RETENTION_DAYS (60), so the real cutoff applies
# and no test has to patch `now`.
_LONG_AGO = datetime.now(UTC) - timedelta(days=100)


class _Tenancy:
    """Rows created by one test, cleaned up by id.

    Deleting by ``creator_user_id``/``created_by_user_id`` is not good enough
    here: ``prepare_hard_delete`` *reassigns* surviving orgs and projects to
    another user, so an ownership-based cleanup silently misses them. A leaked
    org or project then makes the next run of the retention tests — which assert
    over whatever the policy finds globally — fail for unrelated reasons.
    """

    def __init__(self, sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID) -> None:
        self._sm = sm
        self.owner_id = owner_id
        self._projects: list[uuid.UUID] = []
        self._orgs: list[uuid.UUID] = []
        self._users: list[uuid.UUID] = [owner_id]

    async def user(self) -> uuid.UUID:
        uid = uuid.uuid4()
        async with self._sm() as s:
            await s.execute(
                users_t.insert().values(id=uid, email=f"barrier-{uid}@test.invalid", password_hash="x")
            )
            await s.commit()
        self._users.append(uid)
        return uid

    async def org(self, *, deleted_at: datetime | None) -> uuid.UUID:
        oid = uuid.uuid4()
        async with self._sm() as s:
            await s.execute(
                orgs_t.insert().values(
                    id=oid, name="barrier-org", creator_user_id=self.owner_id, deleted_at=deleted_at
                )
            )
            await s.commit()
        self._orgs.append(oid)
        return oid

    async def project(self, *, deleted_at: datetime | None, org_id: uuid.UUID | None = None) -> uuid.UUID:
        pid = uuid.uuid4()
        async with self._sm() as s:
            await s.execute(
                projects_t.insert().values(
                    id=pid,
                    name="barrier",
                    owner_user_id=None if org_id else self.owner_id,
                    owner_org_id=org_id,
                    created_by_user_id=self.owner_id,
                    deleted_at=deleted_at,
                )
            )
            await s.commit()
        self._projects.append(pid)
        return pid

    async def exists(self, pid: uuid.UUID) -> bool:
        async with self._sm() as s:
            row = (await s.execute(sa.select(projects_t.c.id).where(projects_t.c.id == pid))).first()
        return row is not None

    async def restore_project(self, pid: uuid.UUID) -> int:
        async with self._sm() as s, s.begin():
            result = await s.execute(
                projects_t.update()
                .where(projects_t.c.id == pid)
                .where(projects_t.c.deleted_at.isnot(None))
                .values(deleted_at=None)
            )
        return result.rowcount

    async def restore_org(self, oid: uuid.UUID) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(
                orgs_t.update()
                .where(orgs_t.c.id == oid)
                .where(orgs_t.c.deleted_at.isnot(None))
                .values(deleted_at=None)
            )

    async def cleanup(self) -> None:
        # Order matters: both user FKs are RESTRICT.
        async with self._sm() as s:
            await s.execute(projects_t.delete().where(projects_t.c.id.in_(self._projects)))
            await s.execute(orgs_t.delete().where(orgs_t.c.id.in_(self._orgs)))
            await s.execute(users_t.delete().where(users_t.c.id.in_(self._users)))
            await s.commit()


@pytest.fixture
async def tenancy(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[_Tenancy]:
    uid = uuid.uuid4()
    async with sessionmaker() as s:
        await s.execute(
            users_t.insert().values(id=uid, email=f"barrier-{uid}@test.invalid", password_hash="x")
        )
        await s.commit()
    fixtures = _Tenancy(sessionmaker, uid)
    try:
        yield fixtures
    finally:
        await fixtures.cleanup()


async def _run_retention(sm: async_sessionmaker[AsyncSession]) -> set[uuid.UUID]:
    """Run the destructive tenancy policy; return the ids handed to teardown."""
    from app.workers.tasks import retention as ret

    torn_down: set[uuid.UUID] = set()

    # `source` is optional so this probe also binds against the pre-fix call
    # shape. Without that, reverting the fix makes the probe raise TypeError,
    # the old code's blanket `except Exception` swallows it, and these tests go
    # green against the very bug they exist to catch.
    async def _capture(self, project_ids, *, source=None, audit_action="rag.source_infra_purged"):
        torn_down.update(project_ids)
        return len(set(project_ids))

    with (
        patch.object(ret, "get_sessionmaker", return_value=sm),
        patch(
            "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
            new=_capture,
        ),
    ):
        async with sm() as policy_session, policy_session.begin():
            await ret._purge_soft_deleted_tenancy(policy_session)
    return torn_down


# ===========================================================================
# §8.2 — the Postgres property the whole fix rests on
# ===========================================================================


async def test_restore_committing_first_makes_the_delete_match_nothing(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    """The barrier, in the direction that used to destroy data.

    The delete blocks on the restore's uncommitted row lock, then re-evaluates
    `deleted_at IS NOT NULL` under READ COMMITTED and finds the row live. It
    must return no rows — which is exactly what stops teardown from running.
    """
    pid = await tenancy.project(deleted_at=_LONG_AGO)
    cutoff = datetime.now(UTC) - timedelta(days=60)

    restore_session = sessionmaker()
    delete_session = sessionmaker()
    try:
        await restore_session.begin()
        await restore_session.execute(
            projects_t.update()
            .where(projects_t.c.id == pid)
            .where(projects_t.c.deleted_at.isnot(None))
            .values(deleted_at=None)
        )  # holds the row lock, uncommitted

        async def _delete() -> list[uuid.UUID]:
            async with delete_session.begin():
                res = await delete_session.execute(
                    projects_t.delete()
                    .where(projects_t.c.id == pid)
                    .where(projects_t.c.deleted_at.isnot(None))
                    .where(projects_t.c.deleted_at < cutoff)
                    .returning(projects_t.c.id)
                )
                return list(res.scalars().all())

        task = asyncio.create_task(_delete())
        # Give the delete time to reach Postgres and block on the lock.
        await asyncio.sleep(0.5)
        assert not task.done(), "the delete must block on the restore's row lock"

        await restore_session.commit()
        returned = await asyncio.wait_for(task, timeout=10)
    finally:
        await restore_session.close()
        await delete_session.close()

    assert returned == [], "restore won, so the delete must claim nothing"
    assert await tenancy.exists(pid), "the restored project must survive"


async def test_delete_committing_first_makes_the_restore_match_nothing(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    """The inverse: once the delete commits, restore cannot reactivate the row,
    so tearing its sources down afterwards is safe."""
    pid = await tenancy.project(deleted_at=_LONG_AGO)
    cutoff = datetime.now(UTC) - timedelta(days=60)

    async with sessionmaker() as del_s, del_s.begin():
        returned = list(
            (
                await del_s.execute(
                    projects_t.delete()
                    .where(projects_t.c.id == pid)
                    .where(projects_t.c.deleted_at.isnot(None))
                    .where(projects_t.c.deleted_at < cutoff)
                    .returning(projects_t.c.id)
                )
            )
            .scalars()
            .all()
        )
    assert returned == [pid]

    assert await tenancy.restore_project(pid) == 0, "a committed hard delete cannot be undone"
    assert not await tenancy.exists(pid)


# ===========================================================================
# §8.2/§8.3 — the same invariant through the actual retention policy
# ===========================================================================


async def test_policy_tears_down_only_the_project_it_committed(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    doomed = await tenancy.project(deleted_at=_LONG_AGO)
    live = await tenancy.project(deleted_at=None)

    torn_down = await _run_retention(sessionmaker)

    assert doomed in torn_down
    assert live not in torn_down, "a live project's sources must never be erased"
    assert not await tenancy.exists(doomed)
    assert await tenancy.exists(live)


async def test_policy_skips_a_project_restored_before_the_pass(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    """The F-7 regression at policy level: eligible at setup, restored before the
    destructive phase runs, so nothing may be torn down."""
    pid = await tenancy.project(deleted_at=_LONG_AGO)
    await tenancy.restore_project(pid)

    torn_down = await _run_retention(sessionmaker)

    assert pid not in torn_down
    assert await tenancy.exists(pid)


async def test_restore_landing_mid_pass_cancels_the_teardown(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    """The actual F-7 race, end to end — the one the old ordering lost.

    The restore commits *while the policy is running*, which is the window the
    sibling test (restore before the pass) cannot reach. Against the old code
    this fails: the candidate SELECT ran before the delete and, under READ
    COMMITTED, still saw the row soft-deleted, so teardown erased the sources of
    a project the subsequent delete then failed to claim. Now the delete is the
    decision, so blocking on the restore's lock is enough to cancel teardown.
    """
    pid = await tenancy.project(deleted_at=_LONG_AGO)

    restore_session = sessionmaker()
    try:
        await restore_session.begin()
        await restore_session.execute(
            projects_t.update()
            .where(projects_t.c.id == pid)
            .where(projects_t.c.deleted_at.isnot(None))
            .values(deleted_at=None)
        )  # holds the row lock, uncommitted

        task = asyncio.create_task(_run_retention(sessionmaker))
        await asyncio.sleep(0.5)
        assert not task.done(), "the policy's project delete must block on the restore"

        await restore_session.commit()
        torn_down = await asyncio.wait_for(task, timeout=10)
    finally:
        await restore_session.close()

    assert pid not in torn_down, "restore won the race — its sources must be untouched"
    assert await tenancy.exists(pid)


async def test_org_cascade_tears_down_children_of_committed_orgs_only(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    """§8.3: the returned parent is the authoritative cascade decision.

    The doomed org's project is itself live (`deleted_at IS NULL`) — it dies only
    by FK cascade, which is precisely why it cannot be discovered after the fact
    and must be captured before the delete.
    """
    doomed_org = await tenancy.org(deleted_at=_LONG_AGO)
    live_org = await tenancy.org(deleted_at=None)
    via_doomed = await tenancy.project(deleted_at=None, org_id=doomed_org)
    via_live = await tenancy.project(deleted_at=None, org_id=live_org)

    torn_down = await _run_retention(sessionmaker)

    assert via_doomed in torn_down, "a cascade-deleted project's sources must be erased"
    assert via_live not in torn_down
    assert not await tenancy.exists(via_doomed), "FK cascade must have removed it"
    assert await tenancy.exists(via_live)


async def test_org_restored_before_the_pass_keeps_its_projects_and_sources(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    oid = await tenancy.org(deleted_at=_LONG_AGO)
    pid = await tenancy.project(deleted_at=None, org_id=oid)
    await tenancy.restore_org(oid)

    torn_down = await _run_retention(sessionmaker)

    assert pid not in torn_down
    assert await tenancy.exists(pid)


# ===========================================================================
# §8.4/§8.5 — failure after commit is healed by row absence
# ===========================================================================


async def test_teardown_crash_leaves_the_project_discoverable_by_the_orphan_sweep(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    """The delete commits, the purge dies, and the row is gone — which is exactly
    the signal `_purge_rag_source_orphans` keys on, so the next pass reclaims it."""
    from app.workers.tasks import retention as ret

    pid = await tenancy.project(deleted_at=_LONG_AGO)

    with (
        patch.object(ret, "get_sessionmaker", return_value=sessionmaker),
        patch(
            "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
            new_callable=AsyncMock,
            side_effect=RuntimeError("qdrant unreachable — process would die here"),
        ),
    ):
        async with sessionmaker() as policy_session, policy_session.begin():
            await ret._purge_soft_deleted_tenancy(policy_session)

    assert not await tenancy.exists(pid), "the hard delete must have committed anyway"

    with patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.sweep_rag_source_orphans",
        new_callable=AsyncMock,
        return_value=0,
    ) as sweep:
        async with sessionmaker() as s, s.begin():
            await ret._purge_rag_source_orphans(s)

    live_ids = sweep.await_args.args[0]
    assert pid not in live_ids, "row absence is what makes the orphan recoverable"


async def test_partial_teardown_failure_does_not_affect_other_projects(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    """§8.5: one project's external failure must not hold back the others, and
    the rows are gone either way so the sweep can retry the loser."""
    from app.workers.tasks import retention as ret
    from contexts.knowledge.application.config_service import RagConfigService

    bad = await tenancy.project(deleted_at=_LONG_AGO)
    good = await tenancy.project(deleted_at=_LONG_AGO)
    purged: list[uuid.UUID] = []

    async def _purge_one(*, project_id: uuid.UUID, qdrant_store: object) -> dict:
        if project_id == bad:
            raise RuntimeError("minio down for this project only")
        purged.append(project_id)
        return {"blobs_removed": 1, "buckets_failed": 0, "collection_dropped": True}

    with (
        patch.object(ret, "get_sessionmaker", return_value=sessionmaker),
        patch("app.config.settings.get_settings"),
        patch("qdrant_client.AsyncQdrantClient", return_value=AsyncMock()),
        patch.object(RagConfigService, "purge_project_source_infra", new=staticmethod(_purge_one)),
        patch("contexts.knowledge.interfaces.facade.audit.emit", new_callable=AsyncMock),
    ):
        async with sessionmaker() as policy_session, policy_session.begin():
            await ret._purge_soft_deleted_tenancy(policy_session)

    assert purged == [good], "the healthy project is purged despite its neighbour failing"
    assert not await tenancy.exists(bad)
    assert not await tenancy.exists(good)


# ===========================================================================
# §8.3 — the admin GDPR path against real Postgres
# ===========================================================================


async def test_admin_gdpr_returns_only_projects_whose_delete_committed(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    from contexts.tenancy.application.account_deletion_service import AccountDeletionService

    admin_id = await tenancy.user()
    doomed_org = await tenancy.org(deleted_at=_LONG_AGO)
    live_org = await tenancy.org(deleted_at=None)
    via_org = await tenancy.project(deleted_at=None, org_id=doomed_org)
    direct = await tenancy.project(deleted_at=_LONG_AGO)
    # Org-owned rather than individually owned, to dodge FU-9: this method nulls
    # `owner_user_id` on every surviving project of the deleted user, which
    # violates `ck_projects_projects_owner_xor` for an org-less project. That is
    # a pre-existing defect on a path this task does not touch; an org-owned
    # project keeps the restore invariant under test either way.
    live = await tenancy.project(deleted_at=None, org_id=live_org)

    async with sessionmaker() as s, s.begin():
        committed = await AccountDeletionService(s).prepare_hard_delete(
            user_id=tenancy.owner_id, reassign_to_user_id=admin_id
        )

    assert direct in committed
    assert via_org in committed, "org-cascade children must be captured before the cascade"
    assert live not in committed, "a live project must never enter teardown"
    assert not await tenancy.exists(via_org)
    assert await tenancy.exists(live)


async def test_admin_gdpr_skips_a_project_restored_before_the_delete(
    sessionmaker: async_sessionmaker[AsyncSession], tenancy: _Tenancy
) -> None:
    from contexts.tenancy.application.account_deletion_service import AccountDeletionService

    admin_id = await tenancy.user()
    # Org-owned for the FU-9 reason documented in the sibling test; the project
    # is still a candidate for the direct delete via `created_by_user_id`.
    org_id = await tenancy.org(deleted_at=None)
    pid = await tenancy.project(deleted_at=_LONG_AGO, org_id=org_id)
    await tenancy.restore_project(pid)

    async with sessionmaker() as s, s.begin():
        committed = await AccountDeletionService(s).prepare_hard_delete(
            user_id=tenancy.owner_id, reassign_to_user_id=admin_id
        )

    assert pid not in committed
    assert await tenancy.exists(pid)
