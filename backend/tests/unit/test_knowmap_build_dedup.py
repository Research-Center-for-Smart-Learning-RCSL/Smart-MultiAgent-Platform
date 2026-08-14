"""F-12 — Knowledge Map build dedup by corpus revision.

The pre-fix build dedup keyed the Arq job id on the config's
``(last_build_state, last_build_at@1s)`` snapshot read before the slow work and
used to enqueue after commit, so an upload committing while a build ran computed
the same id and was silently dropped. These tests cover the revision-based job id
(distinct corpus states never collide, the same revision still dedups), the
enqueue's target-revision plumbing + suppressed-enqueue observability, the
completion re-check that self-heals a corpus that advanced during a build, and
the transactional corpus-revision bump on a document mutation.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.knowmap_triggers import (
    EnqueueOutcome,
    enqueue_knowmap_build,
    knowmap_build_job_id,
)

# ---------------------------------------------------------------------------
# Revision-based job id (§8.1, §8.2, §8.4 / AC-4)
# ---------------------------------------------------------------------------


def test_job_id_format_is_revision_based() -> None:
    cid = uuid.uuid4()
    assert knowmap_build_job_id(cid, target_revision=5) == f"knowmap:build:{cid}:5"


def test_concurrent_upload_gets_distinct_job_id() -> None:
    # §8.1 primary: build A targets corpus revision 1; upload B commits (revision
    # -> 2) while A runs. B must not collide with A's retained job id — the pre-fix
    # bug computed knowmap:build:{C}:idle:0 for both.
    cid = uuid.uuid4()
    assert knowmap_build_job_id(cid, target_revision=1) != knowmap_build_job_id(cid, target_revision=2)


def test_same_revision_dedups_to_one_job_id() -> None:
    # §8.2: two enqueues for the same corpus revision collapse to one job.
    cid = uuid.uuid4()
    assert knowmap_build_job_id(cid, target_revision=5) == knowmap_build_job_id(cid, target_revision=5)


def test_sub_second_distinct_revisions_do_not_collide() -> None:
    # §8.4: two mutations within one wall-clock second get distinct revisions ->
    # distinct ids, where the old int-second epoch nonce would have collided.
    cid = uuid.uuid4()
    assert knowmap_build_job_id(cid, target_revision=7) != knowmap_build_job_id(cid, target_revision=8)


def test_distinct_configs_never_collide() -> None:
    # Two configs at the same revision must not share a job id — the config id is
    # part of the discriminator (carried over from the pre-F-12 contract).
    a, b = uuid.uuid4(), uuid.uuid4()
    assert knowmap_build_job_id(a, target_revision=1) != knowmap_build_job_id(b, target_revision=1)


# ---------------------------------------------------------------------------
# enqueue_knowmap_build — target-revision plumbing + dedup observability (§7.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_passes_target_revision_and_job_id() -> None:
    cid = uuid.uuid4()
    fake = AsyncMock(return_value=SimpleNamespace())  # arq Job handle
    with patch("shared_kernel.queue.enqueue", fake):
        await enqueue_knowmap_build(config_id=cid, target_revision=3)
    fake.assert_awaited_once()
    call = fake.await_args
    assert call.args[0] == "knowmap_build"
    assert call.kwargs["config_id"] == str(cid)
    assert call.kwargs["target_revision"] == 3
    assert call.kwargs["_job_id"] == f"knowmap:build:{cid}:3"


@pytest.mark.asyncio
async def test_enqueue_tolerates_dedup_none_return() -> None:
    # A None return is a legitimate same-revision dedup — must not raise.
    with patch("shared_kernel.queue.enqueue", AsyncMock(return_value=None)):
        await enqueue_knowmap_build(config_id=uuid.uuid4(), target_revision=1)


@pytest.mark.asyncio
async def test_enqueue_is_best_effort_on_error() -> None:
    with patch("shared_kernel.queue.enqueue", AsyncMock(side_effect=RuntimeError("redis down"))):
        await enqueue_knowmap_build(config_id=uuid.uuid4(), target_revision=1)  # no raise


# ---------------------------------------------------------------------------
# Completion re-check (§8.3 / AC-2)
# ---------------------------------------------------------------------------


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def begin(self) -> Any:
        class _Begin:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_a: Any) -> None:
                return None

        return _Begin()


def _sm() -> Any:
    return _FakeSession


async def _run_finalize(*, succeeded: bool, target: int | None, current_rev: int) -> tuple[Any, Any, Any]:
    from app.workers.tasks import knowmap as kmod

    cfg_id = uuid.uuid4()
    repo = AsyncMock()
    repo.get.return_value = SimpleNamespace(corpus_revision=current_rev)
    enq = AsyncMock()
    with (
        patch.object(kmod, "KnowmapConfigRepository", return_value=repo),
        patch.object(kmod, "enqueue_knowmap_build", enq),
    ):
        out = await kmod._finalize_build_revision(_sm(), cfg_id, target, succeeded=succeeded)
    return out, repo, enq


@pytest.mark.asyncio
async def test_finalize_enqueues_follow_up_when_corpus_advanced() -> None:
    # Build for revision 1 finished while corpus_revision is already 2 -> a
    # follow-up build for revision 2 is enqueued (self-heals the mid-build change).
    out, repo, enq = await _run_finalize(succeeded=True, target=1, current_rev=2)
    assert out == 2
    repo.set_built_corpus_revision.assert_awaited_once()
    assert repo.set_built_corpus_revision.await_args.args[1] == 1
    enq.assert_awaited_once()
    assert enq.await_args.kwargs["target_revision"] == 2


@pytest.mark.asyncio
async def test_finalize_no_follow_up_when_revision_caught_up() -> None:
    out, _repo, enq = await _run_finalize(succeeded=True, target=2, current_rev=2)
    assert out is None
    enq.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_noop_on_failed_build() -> None:
    out, repo, enq = await _run_finalize(succeeded=False, target=1, current_rev=5)
    assert out is None
    repo.set_built_corpus_revision.assert_not_awaited()
    enq.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_noop_without_target_revision() -> None:
    out, repo, enq = await _run_finalize(succeeded=True, target=None, current_rev=5)
    assert out is None
    repo.set_built_corpus_revision.assert_not_awaited()
    enq.assert_not_awaited()


# ---------------------------------------------------------------------------
# F-4 §8.1 / AC-1, AC-2 — the sweep recovers what a failed finalizer dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_enqueues_the_revision_a_failed_finalizer_dropped() -> None:
    # AC-1/AC-2: build A committed for revision 1 while a mutation advanced the
    # corpus to 2, then the follow-up enqueue was lost, so nothing was ever queued
    # for revision 2. With no further mutation and no manual rebuild, the sweep
    # must still deliver it, targeting 2 rather than the built 1.
    #
    # Scope: this pins what the sweep itself decides -- which config, which
    # revision. Whether arq then runs the job is arq's dedup, exercised by the
    # job-id tests above; revision 2 has never been used as a job id in this
    # scenario, which is precisely why the sweep recovers it promptly.
    from app.workers.tasks import knowmap as kmod

    cfg = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), corpus_revision=2, built_corpus_revision=1
    )

    class _Repo:
        def __init__(self, _db: Any) -> None:
            pass

        async def list_revision_divergent(
            self, *, limit: int, after_id: uuid.UUID | None = None
        ) -> list[Any]:
            return [cfg] if after_id is None else []

        async def list_stale_running(self, *, started_before: Any, limit: int) -> list[Any]:
            return []

    class _Tenancy:
        def __init__(self, _db: Any) -> None:
            pass

        async def get_projects(self, project_ids: Any) -> dict[uuid.UUID, Any]:
            return dict.fromkeys(project_ids, object())

    enq = AsyncMock(return_value=EnqueueOutcome.QUEUED)
    with (
        patch.object(kmod, "get_sessionmaker", _sm),
        patch.object(kmod, "KnowmapConfigRepository", _Repo),
        patch.object(kmod, "enqueue_knowmap_build", enq),
        patch("contexts.tenancy.interfaces.facade.TenancyFacade", _Tenancy),
    ):
        result = await kmod.knowmap_revision_sweep({})

    enq.assert_awaited_once_with(config_id=cfg.id, target_revision=2, pool=None)
    assert result == "enqueued=1 deduped=0 failed=0 abandoned=0 stale_running=0"


# ---------------------------------------------------------------------------
# Transactional corpus-revision bump (§8.5 / AC-3)
# ---------------------------------------------------------------------------


class _BumpResult:
    def __init__(self, rev: int | None) -> None:
        self._rev = rev

    def first(self) -> Any:
        return SimpleNamespace(corpus_revision=self._rev) if self._rev is not None else None


class _BumpSession:
    def __init__(self, rev: int | None) -> None:
        self._rev = rev
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _BumpResult:
        self.statements.append(stmt)
        return _BumpResult(self._rev)


@pytest.mark.asyncio
async def test_bump_corpus_revision_returns_incremented_value() -> None:
    from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository

    repo = KnowmapConfigRepository(_BumpSession(rev=5))  # type: ignore[arg-type]
    assert await repo.bump_corpus_revision(uuid.uuid4()) == 5


@pytest.mark.asyncio
async def test_bump_corpus_revision_missing_config_returns_zero() -> None:
    from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository

    repo = KnowmapConfigRepository(_BumpSession(rev=None))  # type: ignore[arg-type]
    assert await repo.bump_corpus_revision(uuid.uuid4()) == 0


@pytest.mark.asyncio
async def test_index_document_bumps_corpus_revision_once() -> None:
    # The mutation path must bump the revision exactly once per committed index.
    from contexts.knowledge.application import knowmap_ingest_service as kis
    from contexts.knowledge.application.knowmap_ingest_service import KnowmapIngestService
    from contexts.knowledge.domain.models import ChunkStrategy

    chunks = AsyncMock()
    documents = AsyncMock()
    configs = AsyncMock()
    svc = KnowmapIngestService(
        AsyncMock(),
        blob=AsyncMock(),
        embedder=AsyncMock(),
        configs=configs,
        documents=documents,
        chunks=chunks,
        chunker=AsyncMock(),
        scan_required=False,
    )
    config_id = uuid.uuid4()
    doc = SimpleNamespace(id=uuid.uuid4(), mime="text/plain", knowmap_config_id=config_id)
    svc._docs.get.return_value = doc
    cfg = SimpleNamespace(chunk_strategy=ChunkStrategy.FIXED, chunk_params={})

    async def _fake_chunk(*_a: Any, **_k: Any) -> list[str]:
        return ["chunk-0"]

    svc._chunker = _fake_chunk
    with (
        patch.dict(kis.MIME_TO_PARSER, {"text/plain": lambda _data: "text"}, clear=False),
        patch.object(kis.audit, "emit", new=AsyncMock()),
    ):
        await svc._index_document(
            doc=doc, cfg=cfg, data=b"x", actor_user_id=None, actor_ip=None, request_id=None
        )
    svc._configs.bump_corpus_revision.assert_awaited_once_with(config_id)


# ---------------------------------------------------------------------------
# W1 — scan-verdict events advance the revision so a sibling upload is not dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_verdict_rebuild_advances_and_targets_new_revision() -> None:
    # Two documents entering the ready-and-clean corpus via a scan verdict must get
    # distinct, incrementing target revisions. The pre-W1 code targeted the same
    # static corpus_revision for both, so arq dropped the second build as a
    # duplicate and that document was silently left unbuilt.
    from app.workers.tasks import knowmap as kmod

    counter = {"rev": 7}

    class _Repo:
        def __init__(self, db: Any) -> None:
            pass

        async def bump_corpus_revision(self, cfg_id: uuid.UUID) -> int:
            counter["rev"] += 1
            return counter["rev"]

    enq = AsyncMock()
    cfg_id = uuid.uuid4()
    with (
        patch.object(kmod, "KnowmapConfigRepository", _Repo),
        patch.object(kmod, "enqueue_knowmap_build", enq),
    ):
        await kmod._bump_and_enqueue_build(_sm(), cfg_id)
        await kmod._bump_and_enqueue_build(_sm(), cfg_id)

    assert [c.kwargs["target_revision"] for c in enq.await_args_list] == [8, 9]
    assert all(c.kwargs["config_id"] == cfg_id for c in enq.await_args_list)


@pytest.mark.asyncio
async def test_scan_verdict_rebuild_skips_concurrently_deleted_config() -> None:
    # bump_corpus_revision returns 0 only when no row matched (the config was
    # concurrently deleted) — there is nothing to build, so no enqueue.
    from app.workers.tasks import knowmap as kmod

    class _Repo:
        def __init__(self, db: Any) -> None:
            pass

        async def bump_corpus_revision(self, cfg_id: uuid.UUID) -> int:
            return 0

    enq = AsyncMock()
    with (
        patch.object(kmod, "KnowmapConfigRepository", _Repo),
        patch.object(kmod, "enqueue_knowmap_build", enq),
    ):
        await kmod._bump_and_enqueue_build(_sm(), uuid.uuid4())

    enq.assert_not_awaited()


# ---------------------------------------------------------------------------
# W2 — an explicit rebuild advances the revision so a retained result cannot drop it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_endpoint_bumps_and_targets_new_revision() -> None:
    # An explicit rebuild after a build (success OR terminal FAILED) must enqueue a
    # build that the retained prior-build result cannot suppress. It therefore bumps
    # corpus_revision and targets the bumped value (5), not the stale cfg value (4)
    # the pre-W2 code used, which collided with the retained result for keep_result
    # seconds while the reconciler does not heal a terminal FAILED.
    from app.api.v1 import knowmap as api

    config_id = uuid.uuid4()
    cfg = SimpleNamespace(id=config_id, project_id=uuid.uuid4(), corpus_revision=4)

    svc = AsyncMock()
    svc.get.return_value = cfg
    repo = AsyncMock()
    repo.bump_corpus_revision.return_value = 5
    enq = AsyncMock()
    db = AsyncMock()
    ctx = SimpleNamespace(actor_ip=None, request_id=None)
    principal = SimpleNamespace(user_id=uuid.uuid4())

    with (
        patch.object(api, "KnowmapConfigService", return_value=svc),
        patch.object(api, "KnowmapConfigRepository", return_value=repo),
        patch.object(api, "_assert_edit", AsyncMock()),
        patch.object(api, "enqueue_knowmap_build", enq),
        patch("shared_kernel.audit.emit", new=AsyncMock()),
    ):
        ack = await api.rebuild_knowmap_config(config_id=config_id, ctx=ctx, principal=principal, db=db)

    repo.bump_corpus_revision.assert_awaited_once_with(config_id)
    enq.assert_awaited_once_with(config_id=config_id, target_revision=5)
    assert ack.status == "enqueued"
