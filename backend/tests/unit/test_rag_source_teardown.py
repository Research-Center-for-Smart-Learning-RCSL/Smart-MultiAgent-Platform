"""F-24: tenant-deletion teardown of File RAG + Knowledge Map source infra.

Covers the row-independent teardown primitive
(``RagConfigService.purge_project_source_infra``), the backstop orphan
enumeration (``list_rag_source_orphans``), per-store isolation, and the admin
GDPR hard-delete wiring (``AccountDeletionService.prepare_hard_delete``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.infrastructure.qdrant_store import collection_name


class _FakeObj:
    def __init__(self, name: str) -> None:
        self.object_name = name


class _FakeMinio:
    def __init__(self) -> None:
        self.rag_sources_bucket = "rag-sources"
        self.knowmap_sources_bucket = "knowmap-sources"
        self._objects: dict[str, list[_FakeObj]] = {}
        self.removed: list[tuple[str, str]] = []
        self.raise_list_for: set[str] = set()

    def add(self, bucket: str, key: str) -> None:
        self._objects.setdefault(bucket, []).append(_FakeObj(key))

    def list_objects_sync(
        self, bucket: str, *, prefix: str | None = None, recursive: bool = True
    ) -> list[_FakeObj]:
        if bucket in self.raise_list_for:
            raise RuntimeError("minio list down")
        objs = self._objects.get(bucket, [])
        if prefix is not None:
            objs = [o for o in objs if o.object_name.startswith(prefix)]
        if recursive:
            return list(objs)
        # Emulate MinIO's delimited listing: one "{seg}/" entry per top-level prefix.
        out: list[_FakeObj] = []
        seen: set[str] = set()
        for o in objs:
            seg = o.object_name.split("/", 1)[0]
            if seg not in seen:
                seen.add(seg)
                out.append(_FakeObj(f"{seg}/"))
        return out

    def remove_object_sync(self, bucket: str, key: str) -> None:
        self.removed.append((bucket, key))


class _FakeQdrant:
    def __init__(
        self,
        collections: tuple[str, ...] = (),
        *,
        raise_on_delete: bool = False,
        raise_on_list: bool = False,
    ) -> None:
        self._collections: set[str] = set(collections)
        self.dropped: list[str] = []
        self.closed = False
        self.raise_on_delete = raise_on_delete
        self.raise_on_list = raise_on_list

    async def collection_exists(self, name: str) -> bool:
        return name in self._collections

    async def delete_collection(self, collection_name: str) -> None:
        if self.raise_on_delete:
            raise RuntimeError("qdrant delete down")
        self.dropped.append(collection_name)
        self._collections.discard(collection_name)

    async def get_collections(self) -> Any:
        if self.raise_on_list:
            raise RuntimeError("qdrant list down")
        cols = [type("C", (), {"name": n})() for n in self._collections]
        return type("Resp", (), {"collections": cols})()

    async def close(self) -> None:
        self.closed = True


def _fake_settings() -> Any:
    return type("S", (), {"qdrant": type("Q", (), {"url": "x", "api_key": None})()})()


def _patches(minio: _FakeMinio, qdrant: _FakeQdrant) -> Any:
    return (
        patch("shared_kernel.storage.get_minio_client", return_value=minio),
        patch("app.config.settings.get_settings", return_value=_fake_settings()),
        patch("qdrant_client.AsyncQdrantClient", return_value=qdrant),
    )


# ===========================================================================
# Teardown primitive (AC-3)
# ===========================================================================


@pytest.mark.asyncio
async def test_purge_primitive_removes_both_buckets_and_drops_collection() -> None:
    pid = uuid.uuid4()
    other = uuid.uuid4()
    minio = _FakeMinio()
    minio.add("rag-sources", f"{pid}/cfg/a.pdf")
    minio.add("rag-sources", f"{pid}/cfg/b.pdf")
    minio.add("rag-sources", f"{other}/cfg/x.pdf")  # different tenant — must survive
    minio.add("knowmap-sources", f"{pid}/cfg/c.pdf")
    qdrant = _FakeQdrant(collections=(collection_name(pid),))

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        summary = await RagConfigService.purge_project_source_infra(project_id=pid)

    removed = set(minio.removed)
    assert ("rag-sources", f"{pid}/cfg/a.pdf") in removed
    assert ("rag-sources", f"{pid}/cfg/b.pdf") in removed
    assert ("knowmap-sources", f"{pid}/cfg/c.pdf") in removed
    assert ("rag-sources", f"{other}/cfg/x.pdf") not in removed
    assert summary["blobs_removed"] == 3
    assert summary["buckets_failed"] == 0
    assert summary["collection_dropped"] is True
    assert qdrant.dropped == [collection_name(pid)]
    assert qdrant.closed is True


@pytest.mark.asyncio
async def test_purge_primitive_idempotent_when_absent() -> None:
    pid = uuid.uuid4()
    minio = _FakeMinio()
    qdrant = _FakeQdrant(collections=())  # collection does not exist

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        summary = await RagConfigService.purge_project_source_infra(project_id=pid)

    assert minio.removed == []
    assert summary["blobs_removed"] == 0
    assert summary["buckets_failed"] == 0
    # collection_dropped reflects that nothing existed (F-24 #4: not an overstated
    # "dropped" for an absent collection).
    assert summary["collection_dropped"] is False
    assert qdrant.dropped == []


# ===========================================================================
# Per-store isolation (AC-5)
# ===========================================================================


@pytest.mark.asyncio
async def test_purge_primitive_isolates_bucket_failure() -> None:
    pid = uuid.uuid4()
    minio = _FakeMinio()
    minio.add("knowmap-sources", f"{pid}/c.pdf")
    minio.raise_list_for = {"rag-sources"}  # first bucket enumeration raises
    qdrant = _FakeQdrant(collections=(collection_name(pid),))

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        summary = await RagConfigService.purge_project_source_infra(project_id=pid)

    assert summary["buckets_failed"] == 1
    assert summary["blobs_removed"] == 1  # knowmap bucket still swept
    assert ("knowmap-sources", f"{pid}/c.pdf") in minio.removed
    assert summary["collection_dropped"] is True  # qdrant still dropped


@pytest.mark.asyncio
async def test_purge_primitive_isolates_qdrant_failure() -> None:
    pid = uuid.uuid4()
    minio = _FakeMinio()
    minio.add("rag-sources", f"{pid}/a.pdf")
    qdrant = _FakeQdrant(collections=(collection_name(pid),), raise_on_delete=True)

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        summary = await RagConfigService.purge_project_source_infra(project_id=pid)

    assert summary["blobs_removed"] == 1  # blobs still removed
    assert summary["collection_dropped"] is False  # qdrant failure isolated


# ===========================================================================
# Backstop orphan enumeration (AC-4)
# ===========================================================================


@pytest.mark.asyncio
async def test_list_orphans_returns_only_projects_with_no_row() -> None:
    live = uuid.uuid4()
    orphan_blob = uuid.uuid4()
    orphan_knowmap = uuid.uuid4()
    orphan_collection = uuid.uuid4()

    minio = _FakeMinio()
    minio.add("rag-sources", f"{live}/a.pdf")  # live — not an orphan
    minio.add("rag-sources", f"{orphan_blob}/b.pdf")
    minio.add("knowmap-sources", f"{orphan_knowmap}/c.pdf")
    minio.add("rag-sources", "not-a-uuid/d.pdf")  # unparseable — ignored
    qdrant = _FakeQdrant(
        collections=(
            collection_name(live),
            collection_name(orphan_collection),
            f"graphrag_{str(uuid.uuid4()).replace('-', '_')}",  # not rag_ — ignored
            f"knowmap_{str(uuid.uuid4()).replace('-', '_')}",  # not rag_ — ignored
        )
    )

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        orphans = await RagConfigService.list_rag_source_orphans(live_project_ids={live})

    assert orphans == {orphan_blob, orphan_knowmap, orphan_collection}
    assert live not in orphans


@pytest.mark.asyncio
async def test_list_orphans_skips_failed_store() -> None:
    live = uuid.uuid4()
    orphan_collection = uuid.uuid4()
    minio = _FakeMinio()
    minio.raise_list_for = {"rag-sources", "knowmap-sources"}  # both bucket lists raise
    qdrant = _FakeQdrant(collections=(collection_name(orphan_collection),))

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        orphans = await RagConfigService.list_rag_source_orphans(live_project_ids={live})

    # MinIO enumeration failed and was skipped; the Qdrant orphan is still found.
    assert orphans == {orphan_collection}


@pytest.mark.asyncio
async def test_list_orphans_survives_qdrant_enumeration_failure() -> None:
    live = uuid.uuid4()
    orphan_blob = uuid.uuid4()
    minio = _FakeMinio()
    minio.add("rag-sources", f"{orphan_blob}/a.pdf")
    qdrant = _FakeQdrant(collections=(), raise_on_list=True)

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        orphans = await RagConfigService.list_rag_source_orphans(live_project_ids={live})

    assert orphans == {orphan_blob}


@pytest.mark.asyncio
async def test_list_orphans_ignores_non_canonical_segment() -> None:
    # F-24 #1: a non-canonical (dash-less) UUID key segment parses to a valid UUID,
    # but the teardown only ever purges the canonical `{pid}/` prefix, so flagging it
    # would re-sweep it forever. The round-trip guard must reject it.
    live = uuid.uuid4()
    stray = uuid.uuid4()
    minio = _FakeMinio()
    minio.add("rag-sources", f"{stray.hex}/a.pdf")  # 32 hex, no dashes — non-canonical
    qdrant = _FakeQdrant(collections=())

    p1, p2, p3 = _patches(minio, qdrant)
    with p1, p2, p3:
        orphans = await RagConfigService.list_rag_source_orphans(live_project_ids={live})

    assert orphans == set()


# ===========================================================================
# Facade batch / sweep — shared client + per-project isolation (AC-5, #2/#6)
# ===========================================================================


@pytest.mark.asyncio
async def test_batch_shares_one_client_isolates_and_audits() -> None:
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    good = uuid.uuid4()
    bad = uuid.uuid4()
    qdrant = _FakeQdrant(collections=())
    db = AsyncMock()

    async def _fake_purge(*, project_id, qdrant_store):
        # The shared store is threaded through to every project.
        assert qdrant_store is not None
        if project_id == bad:
            raise RuntimeError("qdrant down")
        return {
            "project_id": str(project_id),
            "blobs_removed": 1,
            "buckets_failed": 0,
            "collection_dropped": True,
        }

    with (
        patch("app.config.settings.get_settings", return_value=_fake_settings()),
        patch("qdrant_client.AsyncQdrantClient", return_value=qdrant),
        patch(
            "contexts.knowledge.application.config_service.RagConfigService.purge_project_source_infra",
            new=staticmethod(_fake_purge),
        ),
        patch("contexts.knowledge.interfaces.facade.audit.emit", new_callable=AsyncMock) as emit,
    ):
        swept = await KnowledgeFacade(db).purge_project_source_infra_batch([good, bad], source="test")

    assert swept == 1  # bad isolated, good swept
    assert qdrant.closed is True  # single shared client closed once
    # Exactly one audit (for the good project), carrying only project_id + counts.
    emit.assert_awaited_once()
    meta = emit.await_args.args[1].metadata
    assert meta["project_id"] == str(good)
    assert set(meta) == {"project_id", "blobs_removed", "buckets_failed", "collection_dropped"}


@pytest.mark.asyncio
async def test_sweep_reuses_client_for_enumeration_and_teardown() -> None:
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    live = uuid.uuid4()
    orphan = uuid.uuid4()
    minio = _FakeMinio()
    minio.add("rag-sources", f"{orphan}/a.pdf")
    qdrant = _FakeQdrant(collections=(collection_name(orphan),))
    db = AsyncMock()

    with (
        patch("shared_kernel.storage.get_minio_client", return_value=minio),
        patch("app.config.settings.get_settings", return_value=_fake_settings()),
        patch("qdrant_client.AsyncQdrantClient", return_value=qdrant),
        patch("contexts.knowledge.interfaces.facade.audit.emit", new_callable=AsyncMock) as emit,
    ):
        swept = await KnowledgeFacade(db).sweep_rag_source_orphans({live})

    assert swept == 1
    assert qdrant.dropped == [collection_name(orphan)]
    assert qdrant.closed is True
    emit.assert_awaited_once()
    assert emit.await_args.args[1].action == "rag.source_orphan_swept"
    assert emit.await_args.args[1].metadata["project_id"] == str(orphan)


# ===========================================================================
# Admin GDPR hard-delete wiring (AC-9)
# ===========================================================================


class _GdprResult:
    """Result stub answering both the row-tuple and the RETURNING-scalar shape."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> MagicMock:
        m = MagicMock()
        m.all.return_value = [r[0] if isinstance(r, tuple) else r for r in self._rows]
        return m

    def all(self) -> list:
        return list(self._rows)


def _gdpr_db(*, org_rows: list, deleted_projects: list, deleted_orgs: list) -> AsyncMock:
    """DB stub dispatching on statement type and target table, not call order."""
    import sqlalchemy as sa

    from contexts.tenancy.infrastructure import tables as t

    db = AsyncMock()

    async def _execute(stmt: Any, *_a: Any, **_kw: Any) -> Any:
        if isinstance(stmt, sa.Delete):
            if stmt.table is t.projects:
                return _GdprResult(deleted_projects)
            if stmt.table is t.orgs:
                return _GdprResult(deleted_orgs)
            return _GdprResult([])
        if isinstance(stmt, sa.Update):
            return _GdprResult([])
        # The org->project mapping read, captured before the cascade.
        return _GdprResult(org_rows)

    db.execute.side_effect = _execute
    return db


@pytest.mark.asyncio
async def test_admin_gdpr_hard_delete_returns_committed_projects() -> None:
    """The erased set covers both direct and org-cascade projects (AC-2/AC-4).

    `prepare_hard_delete` itself must do no external work — the teardown is the
    caller's job, after the commit (F-7).
    """
    from contexts.tenancy.application.account_deletion_service import AccountDeletionService

    doomed_direct = uuid.uuid4()
    doomed_via_org = uuid.uuid4()
    doomed_org = uuid.uuid4()

    db = _gdpr_db(
        org_rows=[(doomed_via_org, doomed_org)],
        deleted_projects=[doomed_direct],
        deleted_orgs=[doomed_org],
    )

    with patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
    ) as purge_batch:
        committed = await AccountDeletionService(db).prepare_hard_delete(
            user_id=uuid.uuid4(), reassign_to_user_id=uuid.uuid4()
        )

    assert committed == {doomed_direct, doomed_via_org}
    purge_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_gdpr_hard_delete_skips_restored_project() -> None:
    """A restore winning the race must leave the project's sources intact (AC-3).

    The deletes are guarded by `deleted_at IS NOT NULL`, so a restore landing
    first makes them return no rows — and nothing may then be torn down.
    """
    from contexts.tenancy.application.account_deletion_service import AccountDeletionService

    restored = uuid.uuid4()
    db = _gdpr_db(org_rows=[(restored, uuid.uuid4())], deleted_projects=[], deleted_orgs=[])

    svc = AccountDeletionService(db)
    committed = await svc.prepare_hard_delete(user_id=uuid.uuid4(), reassign_to_user_id=uuid.uuid4())

    assert committed == set()

    with patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
    ) as purge_batch:
        assert await svc.purge_hard_deleted_project_sources(committed) == 0

    purge_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_gdpr_teardown_purges_the_committed_projects() -> None:
    """The happy path: the committed ids reach the facade and the count comes back.

    Without this, a change that dropped the ids or returned a constant would
    leave every other test in this file green while admin GDPR purges silently
    stopped erasing source infra.
    """
    from contexts.tenancy.application.account_deletion_service import AccountDeletionService

    db = AsyncMock()
    doomed = {uuid.uuid4(), uuid.uuid4()}

    with patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
        return_value=2,
    ) as purge_batch:
        purged = await AccountDeletionService(db).purge_hard_deleted_project_sources(doomed)

    assert purged == 2
    purge_batch.assert_awaited_once()
    assert set(purge_batch.await_args.args[0]) == doomed
    assert purge_batch.await_args.kwargs["source"] == "admin_gdpr"
    # The audit rows the batch wrote have to be durable before the request ends.
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_gdpr_teardown_failure_leaves_session_usable() -> None:
    """A catastrophic teardown must not fail an already-committed GDPR purge.

    The partial audit writes have to be rolled back: leaving a failed
    transaction on the request session would make `db_session`'s trailing
    commit raise and turn a successful delete into a 500.
    """
    from contexts.tenancy.application.account_deletion_service import AccountDeletionService

    db = AsyncMock()
    svc = AccountDeletionService(db)

    with patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
        side_effect=RuntimeError("qdrant client construction failed"),
    ):
        purged = await svc.purge_hard_deleted_project_sources({uuid.uuid4()})

    assert purged == 0
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_hard_delete_commits_before_teardown() -> None:
    """AC-6: no transaction spans the MinIO/Qdrant calls on the admin path."""
    from contexts.identity.application.admin_service import AdminService

    target = uuid.uuid4()
    doomed = uuid.uuid4()
    order: list[str] = []

    db = AsyncMock()
    db.commit.side_effect = lambda: order.append("commit")

    svc = AdminService(db)
    user = MagicMock(deleted_at=datetime(2020, 1, 1, tzinfo=UTC))
    tenancy = AsyncMock()
    tenancy.orgs_blocking_self_delete.return_value = []
    tenancy.prepare_hard_delete.return_value = {doomed}

    async def _purge(ids: set[uuid.UUID]) -> int:
        order.append("teardown")
        return len(ids)

    tenancy.purge_hard_deleted_project_sources.side_effect = _purge

    with (
        patch.object(svc._users, "get_by_id", new=AsyncMock(return_value=user)),
        patch("contexts.tenancy.interfaces.facade.TenancyFacade", return_value=tenancy),
        patch("contexts.identity.application.admin_service.audit.emit", new_callable=AsyncMock),
    ):
        await svc.hard_delete_user(target_user_id=target, admin_user_id=uuid.uuid4(), actor_ip=None)

    assert order == ["commit", "teardown"], "teardown must follow the durable commit"
