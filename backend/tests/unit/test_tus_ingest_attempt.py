"""F-23 — tus re-upload retries must enqueue a fresh ingest/scan job.

The tus finalizers enqueued the ingest worker with a document-id-only,
deterministic Arq job id, so a same-SHA re-upload of a ``FAILED`` large-file
document within Arq's 3,600 s result-retention window was silently deduped — no
worker ran — while the endpoint reported success. The fix folds a per-document
``ingest_attempt`` counter into the ingest AND scan job ids, bumped only on a
TERMINAL non-READY re-upload (``FAILED``/``QUARANTINED``); an ``INGESTING``
re-upload keeps the in-flight id so a running worker is not duplicated (which
would collide on ``uq_rag_chunk_doc_idx``).

Job-id distinctness is asserted independently of Arq (deterministic); the Arq
dedup-on-retained-id behaviour itself is confirmed by the wiring test
(``tests/wiring/test_rag_ingestion.py``), skipped in this host-only environment.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.knowledge.domain.models import DocumentStatus

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


def _make_rag_finalizer(existing: object, *, bump_returns: list[int]):
    from contexts.knowledge.application.rag_tus_finalizer import RagTusFinalizer

    fin = RagTusFinalizer.__new__(RagTusFinalizer)
    fin._db = AsyncMock()
    fin._configs = AsyncMock()
    fin._configs.get.return_value = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    fin._docs = AsyncMock()
    fin._docs.find_by_sha.return_value = existing
    fin._docs.bump_ingest_attempt = AsyncMock(side_effect=bump_returns)
    fin._docs.create.return_value = SimpleNamespace(id=uuid.uuid4())
    fin._minio = MagicMock(rag_sources_bucket="rag-sources", put_file=AsyncMock())
    return fin


async def _run_rag(fin, cap: _Captured) -> None:
    with ExitStack() as stack:
        _patch_common(stack, _RAG_MOD, cap)
        # RAG-only: the finalizer emits a register-phase pubsub event + reupload audit.
        stack.enter_context(
            patch(f"{_RAG_MOD}.Publisher", new=MagicMock(return_value=MagicMock(emit=AsyncMock())))
        )
        stack.enter_context(patch(f"{_RAG_MOD}.emit_reupload_audit", new=AsyncMock()))
        await fin.finalize(
            rag_config_id=uuid.uuid4(),
            filename="doc.txt",
            mime="text/plain",
            staging_path="/tmp/x",
            size_bytes=100,
            uploaded_by=uuid.uuid4(),
            actor_ip=None,
        )


@pytest.mark.asyncio
async def test_rag_failed_reupload_bumps_and_enqueues_fresh_ids() -> None:
    doc_id = uuid.uuid4()
    existing = SimpleNamespace(id=doc_id, status=DocumentStatus.FAILED)
    fin = _make_rag_finalizer(existing, bump_returns=[1, 2])

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
    assert fin._docs.bump_ingest_attempt.await_count == 2


@pytest.mark.asyncio
async def test_rag_ingesting_reupload_does_not_bump_or_enqueue() -> None:
    existing = SimpleNamespace(id=uuid.uuid4(), status=DocumentStatus.INGESTING)
    fin = _make_rag_finalizer(existing, bump_returns=[99])

    cap = _Captured()
    await _run_rag(fin, cap)

    # In-flight worker: no bump, no re-enqueue (the running job is left to finish).
    fin._docs.bump_ingest_attempt.assert_not_awaited()
    assert cap.ingest == []
    assert cap.scan == []


@pytest.mark.asyncio
async def test_rag_first_upload_uses_attempt_zero() -> None:
    fin = _make_rag_finalizer(None, bump_returns=[])  # no existing row -> fresh doc
    new_id = fin._docs.create.return_value.id

    cap = _Captured()
    await _run_rag(fin, cap)

    assert cap.ingest == [f"rag-ingest:{new_id}:0"]
    assert cap.scan == [f"rag-scan:{new_id}:0"]
    fin._docs.bump_ingest_attempt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Knowledge Map finalizer (symmetric)
# ---------------------------------------------------------------------------


def _make_km_finalizer(existing: object, *, bump_returns: list[int]):
    from contexts.knowledge.application.knowmap_tus_finalizer import KnowmapTusFinalizer

    fin = KnowmapTusFinalizer.__new__(KnowmapTusFinalizer)
    fin._db = AsyncMock()
    fin._configs = AsyncMock()
    fin._configs.get.return_value = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    fin._docs = AsyncMock()
    fin._docs.find_by_sha.return_value = existing
    fin._docs.bump_ingest_attempt = AsyncMock(side_effect=bump_returns)
    fin._docs.create.return_value = SimpleNamespace(id=uuid.uuid4())
    fin._minio = MagicMock(knowmap_sources_bucket="knowmap-sources", put_file=AsyncMock())
    return fin


async def _run_km(fin, cap: _Captured) -> None:
    with ExitStack() as stack:
        _patch_common(stack, _KM_MOD, cap)
        await fin.finalize(
            knowmap_config_id=uuid.uuid4(),
            filename="doc.txt",
            mime="text/plain",
            staging_path="/tmp/x",
            size_bytes=100,
            uploaded_by=uuid.uuid4(),
            actor_ip=None,
        )


@pytest.mark.asyncio
async def test_knowmap_failed_reupload_bumps_and_enqueues_fresh_ids() -> None:
    doc_id = uuid.uuid4()
    existing = SimpleNamespace(id=doc_id, status=DocumentStatus.QUARANTINED)
    fin = _make_km_finalizer(existing, bump_returns=[1, 2])

    cap1 = _Captured()
    await _run_km(fin, cap1)
    cap2 = _Captured()
    await _run_km(fin, cap2)

    assert cap1.ingest == [f"knowmap-ingest:{doc_id}:1"]
    assert cap1.scan == [f"knowmap-scan:{doc_id}:1"]
    assert cap2.ingest == [f"knowmap-ingest:{doc_id}:2"]
    assert cap2.scan == [f"knowmap-scan:{doc_id}:2"]


@pytest.mark.asyncio
async def test_knowmap_ingesting_reupload_does_not_bump_or_enqueue() -> None:
    existing = SimpleNamespace(id=uuid.uuid4(), status=DocumentStatus.INGESTING)
    fin = _make_km_finalizer(existing, bump_returns=[99])

    cap = _Captured()
    await _run_km(fin, cap)

    fin._docs.bump_ingest_attempt.assert_not_awaited()
    assert cap.ingest == []
    assert cap.scan == []


@pytest.mark.asyncio
async def test_knowmap_first_upload_uses_attempt_zero() -> None:
    fin = _make_km_finalizer(None, bump_returns=[])
    new_id = fin._docs.create.return_value.id

    cap = _Captured()
    await _run_km(fin, cap)

    assert cap.ingest == [f"knowmap-ingest:{new_id}:0"]
    assert cap.scan == [f"knowmap-scan:{new_id}:0"]
