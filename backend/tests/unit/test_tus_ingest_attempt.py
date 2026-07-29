"""F-23 — tus re-upload retries must enqueue a fresh ingest/scan job.

The tus finalizers enqueued the ingest worker with a document-id-only,
deterministic Arq job id, so a same-SHA re-upload of a ``FAILED`` large-file
document within Arq's 3,600 s result-retention window was silently deduped — no
worker ran — while the endpoint reported success. The fix folds a per-document
``ingest_attempt`` counter into the ingest AND scan job ids.

The counter is produced by ``claim_for_reingest``: a single atomic UPDATE that
transitions a TERMINAL row (``FAILED``/``QUARANTINED``) to ``INGESTING`` and
bumps ``ingest_attempt`` under a ``WHERE status IN (...)`` guard, RETURNING the
new value — or ``None`` when the row is not terminal (a worker is in flight, or a
concurrent re-upload already claimed it). Folding the terminal-state check and the
bump into ONE statement is what defeats the concurrency bug: two concurrent
``FAILED`` re-uploads can no longer both bump to distinct attempts and spawn two
workers that collide on ``uq_rag_chunk_doc_idx`` — exactly one wins the claim.

Job-id distinctness and the finalizer's react-to-claim branching are asserted
independently of Arq (deterministic); the claim's atomic SQL guard is asserted at
statement level; the Arq dedup-on-retained-id behaviour itself is confirmed by the
wiring test (``tests/wiring/test_rag_ingestion.py``), skipped in this host-only
environment.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.knowledge.domain.errors import DocumentAllowlistConflict
from contexts.knowledge.domain.models import DocumentStatus
from contexts.knowledge.domain.reupload import ReuploadAction

_RAG_MOD = "contexts.knowledge.application.rag_tus_finalizer"
_KM_MOD = "contexts.knowledge.application.knowmap_tus_finalizer"


class _Captured:
    """Collects the ingest + scan job ids enqueued during one finalize()."""

    def __init__(self) -> None:
        self.ingest: list[str] = []
        self.scan: list[str] = []


def _patch_common(stack: ExitStack, mod: str, cap: _Captured) -> None:
    async def _ingest(*_a: object, **kw: object) -> None:
        cap.ingest.append(str(kw["_job_id"]))

    async def _scan(name: str, **kw: object) -> None:
        # shared_kernel.queue.enqueue is used by both ingest workers and scans;
        # only record the scan enqueues here (ingest is captured via {mod}.enqueue).
        if str(name).endswith("scan_document"):
            cap.scan.append(str(kw["_job_id"]))

    # Ingest enqueue is the name bound at import in each finalizer module.
    stack.enter_context(patch(f"{mod}.enqueue", new=AsyncMock(side_effect=_ingest)))
    # Scan enqueue resolves shared_kernel.queue.enqueue at call time (local import).
    stack.enter_context(patch("shared_kernel.queue.enqueue", new=AsyncMock(side_effect=_scan)))
    stack.enter_context(patch(f"{mod}._sha256_file", new=lambda _p: "deadbeef"))
    stack.enter_context(patch(f"{mod}.audit.emit", new=AsyncMock()))


# ---------------------------------------------------------------------------
# RAG finalizer
# ---------------------------------------------------------------------------


def _make_rag_finalizer(existing: Any, *, claim_returns: list[int | None]):
    from contexts.knowledge.application.rag_tus_finalizer import RagTusFinalizer

    fin = RagTusFinalizer.__new__(RagTusFinalizer)
    fin._db = AsyncMock()
    fin._configs = AsyncMock()
    fin._configs.get.return_value = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    fin._docs = AsyncMock()
    fin._docs.find_by_sha.return_value = existing
    fin._docs.claim_for_reingest = AsyncMock(side_effect=claim_returns)
    if existing is not None:
        fin._docs.set_agents.side_effect = lambda *, document_id, agent_ids: SimpleNamespace(
            id=document_id,
            status=existing.status,
            agent_ids=tuple(agent_ids),
        )
    fin._docs.create.return_value = SimpleNamespace(id=uuid.uuid4())
    fin._minio = MagicMock(rag_sources_bucket="rag-sources", put_file=AsyncMock())
    return fin


async def _run_rag(
    fin,
    cap: _Captured,
    *,
    agent_ids: list[uuid.UUID] | None = None,
    reupload_audit: AsyncMock | None = None,
    agents_set_audit: AsyncMock | None = None,
):
    reupload_audit = reupload_audit if reupload_audit is not None else AsyncMock()
    agents_set_audit = agents_set_audit if agents_set_audit is not None else AsyncMock()
    with ExitStack() as stack:
        _patch_common(stack, _RAG_MOD, cap)
        # RAG-only: the finalizer emits a register-phase pubsub event + reupload audit.
        stack.enter_context(
            patch(f"{_RAG_MOD}.Publisher", new=MagicMock(return_value=MagicMock(emit=AsyncMock())))
        )
        stack.enter_context(patch(f"{_RAG_MOD}.emit_reupload_audit", new=reupload_audit))
        stack.enter_context(patch(f"{_RAG_MOD}.emit_reupload_agents_set_audit", new=agents_set_audit))
        return await fin.finalize(
            rag_config_id=uuid.uuid4(),
            filename="doc.txt",
            mime="text/plain",
            staging_path="/tmp/x",
            size_bytes=100,
            uploaded_by=uuid.uuid4(),
            actor_ip=None,
            agent_ids=agent_ids,
        )


@pytest.mark.asyncio
async def test_rag_failed_reupload_bumps_and_enqueues_fresh_ids() -> None:
    doc_id = uuid.uuid4()
    existing = SimpleNamespace(id=doc_id, status=DocumentStatus.FAILED, agent_ids=())
    fin = _make_rag_finalizer(existing, claim_returns=[1, 2])

    cap1 = _Captured()
    await _run_rag(fin, cap1)
    cap2 = _Captured()
    await _run_rag(fin, cap2)

    # Two genuine retries -> two distinct ingest ids (…:1 then …:2), each with a
    # matching scan id — never the pre-fix document-only id.
    assert cap1.ingest == [f"rag-ingest:{doc_id}:1"]
    assert cap1.scan == [f"rag-scan:{doc_id}:1"]
    assert cap2.ingest == [f"rag-ingest:{doc_id}:2"]
    assert cap2.scan == [f"rag-scan:{doc_id}:2"]
    assert fin._docs.claim_for_reingest.await_count == 2


@pytest.mark.asyncio
async def test_rag_unclaimed_reupload_does_not_enqueue() -> None:
    # claim_for_reingest returns None: the row was not terminal (a worker is in
    # flight) or a concurrent re-upload already won the atomic claim. Either way the
    # finalizer must not re-enqueue — the running job is left to finish.
    existing = SimpleNamespace(id=uuid.uuid4(), status=DocumentStatus.INGESTING, agent_ids=())
    fin = _make_rag_finalizer(existing, claim_returns=[None])

    cap = _Captured()
    await _run_rag(fin, cap)

    fin._docs.claim_for_reingest.assert_awaited_once()
    assert cap.ingest == []
    assert cap.scan == []


@pytest.mark.asyncio
async def test_rag_first_upload_uses_attempt_zero() -> None:
    fin = _make_rag_finalizer(None, claim_returns=[])  # no existing row -> fresh doc
    new_id = fin._docs.create.return_value.id

    cap = _Captured()
    await _run_rag(fin, cap)

    assert cap.ingest == [f"rag-ingest:{new_id}:0"]
    assert cap.scan == [f"rag-scan:{new_id}:0"]
    fin._docs.claim_for_reingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_tus_reupload_applies_submitted_allowlist_when_unclaimed() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.INGESTING,
        agent_ids=(agent_a,),
    )
    fin = _make_rag_finalizer(existing, claim_returns=[None])

    returned = await _run_rag(fin, _Captured(), agent_ids=[agent_a, agent_b])

    fin._docs.set_agents.assert_awaited_once_with(
        document_id=existing.id,
        agent_ids=[agent_a, agent_b],
    )
    fin._db.commit.assert_awaited_once()
    assert returned.agent_ids == (agent_a, agent_b)


@pytest.mark.asyncio
async def test_rag_tus_reupload_applies_submitted_allowlist_when_claimed() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.FAILED,
        agent_ids=(agent_a,),
    )
    fin = _make_rag_finalizer(existing, claim_returns=[1])
    reupload_audit = AsyncMock()
    agents_set_audit = AsyncMock()

    returned = await _run_rag(
        fin,
        _Captured(),
        agent_ids=[agent_a, agent_b],
        reupload_audit=reupload_audit,
        agents_set_audit=agents_set_audit,
    )

    fin._docs.set_agents.assert_awaited_once_with(
        document_id=existing.id,
        agent_ids=[agent_a, agent_b],
    )
    assert reupload_audit.await_args.kwargs["outcome"] is ReuploadAction.REINDEX_WITH_OVERWRITE
    assert reupload_audit.await_args.kwargs["submitted_agent_ids"] == [agent_a, agent_b]
    assert agents_set_audit.await_args.kwargs["doc"] is returned
    assert returned.agent_ids == (agent_a, agent_b)


@pytest.mark.asyncio
async def test_rag_tus_ready_duplicate_with_different_allowlist_conflicts() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.READY,
        agent_ids=(agent_a,),
    )
    fin = _make_rag_finalizer(existing, claim_returns=[])

    with pytest.raises(DocumentAllowlistConflict, match="/api/rag-documents/.+/agents"):
        await _run_rag(fin, _Captured(), agent_ids=[agent_a, agent_b])

    fin._docs.set_agents.assert_not_awaited()
    fin._docs.claim_for_reingest.assert_not_awaited()
    fin._db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_rag_tus_ready_duplicate_with_identical_allowlist_is_noop() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.READY,
        agent_ids=(agent_a, agent_b),
    )
    fin = _make_rag_finalizer(existing, claim_returns=[])

    returned = await _run_rag(fin, _Captured(), agent_ids=[agent_b, agent_a])

    assert returned is existing
    fin._docs.set_agents.assert_not_awaited()
    fin._docs.claim_for_reingest.assert_not_awaited()
    fin._db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Knowledge Map finalizer (symmetric)
# ---------------------------------------------------------------------------


def _make_km_finalizer(existing: Any, *, claim_returns: list[int | None]):
    from contexts.knowledge.application.knowmap_tus_finalizer import KnowmapTusFinalizer

    fin = KnowmapTusFinalizer.__new__(KnowmapTusFinalizer)
    fin._db = AsyncMock()
    fin._configs = AsyncMock()
    fin._configs.get.return_value = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    fin._docs = AsyncMock()
    fin._docs.find_by_sha.return_value = existing
    fin._docs.claim_for_reingest = AsyncMock(side_effect=claim_returns)
    if existing is not None:
        fin._docs.set_agents.side_effect = lambda *, document_id, agent_ids: SimpleNamespace(
            id=document_id,
            status=existing.status,
            agent_ids=tuple(agent_ids),
        )
    fin._docs.create.return_value = SimpleNamespace(id=uuid.uuid4())
    fin._minio = MagicMock(knowmap_sources_bucket="knowmap-sources", put_file=AsyncMock())
    return fin


async def _run_km(
    fin,
    cap: _Captured,
    *,
    agent_ids: list[uuid.UUID] | None = None,
    reupload_audit: AsyncMock | None = None,
    agents_set_audit: AsyncMock | None = None,
):
    reupload_audit = reupload_audit if reupload_audit is not None else AsyncMock()
    agents_set_audit = agents_set_audit if agents_set_audit is not None else AsyncMock()
    with ExitStack() as stack:
        _patch_common(stack, _KM_MOD, cap)
        stack.enter_context(patch(f"{_KM_MOD}.emit_knowmap_reupload_audit", new=reupload_audit))
        stack.enter_context(patch(f"{_KM_MOD}.emit_knowmap_reupload_agents_set_audit", new=agents_set_audit))
        return await fin.finalize(
            knowmap_config_id=uuid.uuid4(),
            filename="doc.txt",
            mime="text/plain",
            staging_path="/tmp/x",
            size_bytes=100,
            uploaded_by=uuid.uuid4(),
            actor_ip=None,
            agent_ids=agent_ids,
        )


@pytest.mark.asyncio
async def test_knowmap_failed_reupload_bumps_and_enqueues_fresh_ids() -> None:
    doc_id = uuid.uuid4()
    existing = SimpleNamespace(id=doc_id, status=DocumentStatus.QUARANTINED, agent_ids=())
    fin = _make_km_finalizer(existing, claim_returns=[1, 2])

    cap1 = _Captured()
    await _run_km(fin, cap1)
    cap2 = _Captured()
    await _run_km(fin, cap2)

    assert cap1.ingest == [f"knowmap-ingest:{doc_id}:1"]
    assert cap1.scan == [f"knowmap-scan:{doc_id}:1"]
    assert cap2.ingest == [f"knowmap-ingest:{doc_id}:2"]
    assert cap2.scan == [f"knowmap-scan:{doc_id}:2"]


@pytest.mark.asyncio
async def test_knowmap_unclaimed_reupload_does_not_enqueue() -> None:
    existing = SimpleNamespace(id=uuid.uuid4(), status=DocumentStatus.INGESTING, agent_ids=())
    fin = _make_km_finalizer(existing, claim_returns=[None])

    cap = _Captured()
    await _run_km(fin, cap)

    fin._docs.claim_for_reingest.assert_awaited_once()
    assert cap.ingest == []
    assert cap.scan == []


@pytest.mark.asyncio
async def test_knowmap_first_upload_uses_attempt_zero() -> None:
    fin = _make_km_finalizer(None, claim_returns=[])
    new_id = fin._docs.create.return_value.id

    cap = _Captured()
    await _run_km(fin, cap)

    assert cap.ingest == [f"knowmap-ingest:{new_id}:0"]
    assert cap.scan == [f"knowmap-scan:{new_id}:0"]
    fin._docs.claim_for_reingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowmap_tus_reupload_applies_submitted_allowlist_when_unclaimed() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.INGESTING,
        agent_ids=(agent_a,),
    )
    fin = _make_km_finalizer(existing, claim_returns=[None])

    returned = await _run_km(fin, _Captured(), agent_ids=[agent_a, agent_b])

    fin._docs.set_agents.assert_awaited_once_with(
        document_id=existing.id,
        agent_ids=[agent_a, agent_b],
    )
    fin._db.commit.assert_awaited_once()
    assert returned.agent_ids == (agent_a, agent_b)


@pytest.mark.asyncio
async def test_knowmap_tus_reupload_applies_submitted_allowlist_when_claimed() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.FAILED,
        agent_ids=(agent_a,),
    )
    fin = _make_km_finalizer(existing, claim_returns=[1])
    reupload_audit = AsyncMock()
    agents_set_audit = AsyncMock()

    returned = await _run_km(
        fin,
        _Captured(),
        agent_ids=[agent_a, agent_b],
        reupload_audit=reupload_audit,
        agents_set_audit=agents_set_audit,
    )

    fin._docs.set_agents.assert_awaited_once_with(
        document_id=existing.id,
        agent_ids=[agent_a, agent_b],
    )
    assert reupload_audit.await_args.kwargs["outcome"] is ReuploadAction.REINDEX_WITH_OVERWRITE
    assert reupload_audit.await_args.kwargs["submitted_agent_ids"] == [agent_a, agent_b]
    assert agents_set_audit.await_args.kwargs["doc"] is returned
    assert returned.agent_ids == (agent_a, agent_b)


@pytest.mark.asyncio
async def test_knowmap_tus_ready_duplicate_with_different_allowlist_conflicts() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.READY,
        agent_ids=(agent_a,),
    )
    fin = _make_km_finalizer(existing, claim_returns=[])

    with pytest.raises(DocumentAllowlistConflict, match="/api/knowmap-documents/.+/agents"):
        await _run_km(fin, _Captured(), agent_ids=[agent_a, agent_b])

    fin._docs.set_agents.assert_not_awaited()
    fin._docs.claim_for_reingest.assert_not_awaited()
    fin._db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_knowmap_tus_ready_duplicate_with_identical_allowlist_is_noop() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status=DocumentStatus.READY,
        agent_ids=(agent_a, agent_b),
    )
    fin = _make_km_finalizer(existing, claim_returns=[])

    returned = await _run_km(fin, _Captured(), agent_ids=[agent_b, agent_a])

    assert returned is existing
    fin._docs.set_agents.assert_not_awaited()
    fin._docs.claim_for_reingest.assert_not_awaited()
    fin._db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Statement level — the atomic claim is guarded by terminal status (the race fix)
# ---------------------------------------------------------------------------


class _ClaimRow:
    ingest_attempt = 3


class _ClaimResult:
    def first(self) -> _ClaimRow:
        return _ClaimRow()


class _CapturingClaimDb:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> _ClaimResult:
        self.statements.append(stmt)
        return _ClaimResult()


def _sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(" ", "")


@pytest.mark.asyncio
async def test_rag_claim_for_reingest_is_status_guarded_transition() -> None:
    from contexts.knowledge.infrastructure.repositories import RagDocumentRepository

    db = _CapturingClaimDb()
    repo = RagDocumentRepository(db)  # type: ignore[arg-type]
    doc_id = uuid.uuid4()

    attempt = await repo.claim_for_reingest(doc_id)

    assert attempt == 3
    assert len(db.statements) == 1
    sql = _sql(db.statements[0]).lower()
    # Atomic transition: only a terminal row is claimed, and it flips to ingesting
    # while bumping the counter — all in one UPDATE so concurrent claims can't both win.
    assert "status='ingesting'" in sql
    assert "ingest_attempt=(rag_documents.ingest_attempt+1)" in sql
    assert "in('failed','quarantined')" in sql
    assert doc_id.hex in sql
    assert "returningrag_documents.ingest_attempt" in sql


@pytest.mark.asyncio
async def test_knowmap_claim_for_reingest_is_status_guarded_transition() -> None:
    from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapDocumentRepository

    db = _CapturingClaimDb()
    repo = KnowmapDocumentRepository(db)  # type: ignore[arg-type]
    doc_id = uuid.uuid4()

    attempt = await repo.claim_for_reingest(doc_id)

    assert attempt == 3
    sql = _sql(db.statements[0]).lower()
    assert "status='ingesting'" in sql
    assert "ingest_attempt=(knowmap_documents.ingest_attempt+1)" in sql
    assert "in('failed','quarantined')" in sql
    assert doc_id.hex in sql
