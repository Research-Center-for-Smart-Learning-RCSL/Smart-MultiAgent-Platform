"""F-5 — the clean-verdict build gate in the Knowledge Map scan worker.

Both the scan-disabled fast-path and the CLEAN verdict route through
``_enqueue_build_on_clean``, the single choke point that enqueues the deferred
build only once a document is READY (last-writer-wins with the indexing side). A
document that is not yet READY (async tus path, still indexing) must NOT enqueue —
the index worker will, when it observes the clean verdict.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import app.workers.tasks.knowmap as km
from contexts.knowledge.domain.models import DocumentStatus

_CFG_ID = uuid.uuid4()


class _Begin:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Db:
    def begin(self) -> _Begin:
        return _Begin()


class _Session:
    async def __aenter__(self) -> _Db:
        return _Db()

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _sm() -> Any:
    return lambda: _Session()


def _install_repos(monkeypatch, *, doc: object, bumped_rev: int = 5) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _DocRepo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, doc_id: uuid.UUID) -> object:
            return doc

    class _CfgRepo:
        def __init__(self, db: object) -> None:
            pass

        async def bump_corpus_revision(self, cfg_id: uuid.UUID) -> int:
            return bumped_rev

    async def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(km, "KnowmapDocumentRepository", _DocRepo)
    monkeypatch.setattr(km, "KnowmapConfigRepository", _CfgRepo)
    monkeypatch.setattr(km, "enqueue_knowmap_build", _capture)
    return calls


@pytest.mark.asyncio
async def test_enqueue_build_on_clean_enqueues_when_ready(monkeypatch) -> None:
    doc = SimpleNamespace(status=DocumentStatus.READY, knowmap_config_id=_CFG_ID)
    calls = _install_repos(monkeypatch, doc=doc, bumped_rev=5)

    await km._enqueue_build_on_clean(_sm(), uuid.uuid4(), entered=True)

    # F-12 (W1): a document newly entering the clean set advances the corpus
    # revision and the build targets the freshly bumped value, so a sibling upload
    # cannot collide with it.
    assert calls == [{"config_id": _CFG_ID, "target_revision": 5}]


@pytest.mark.asyncio
async def test_enqueue_build_on_clean_skips_reconfirm(monkeypatch) -> None:
    # F-12 (W3): a CLEAN->CLEAN reconfirm (entered=False, e.g. a reindex rescan)
    # does not change membership — no bump, no enqueue here; the ingest side owns
    # any content-change build, so enqueuing would double it.
    doc = SimpleNamespace(status=DocumentStatus.READY, knowmap_config_id=_CFG_ID)
    calls = _install_repos(monkeypatch, doc=doc, bumped_rev=5)

    await km._enqueue_build_on_clean(_sm(), uuid.uuid4(), entered=False)

    assert calls == []


@pytest.mark.asyncio
async def test_enqueue_build_on_clean_skips_when_not_ready(monkeypatch) -> None:
    # Async tus path: clean verdict arrives before indexing sets READY -> defer to
    # the index worker (which enqueues when it sees the clean verdict). No bump.
    doc = SimpleNamespace(status=DocumentStatus.INGESTING, knowmap_config_id=_CFG_ID)
    calls = _install_repos(monkeypatch, doc=doc)

    await km._enqueue_build_on_clean(_sm(), uuid.uuid4(), entered=True)

    assert calls == []


@pytest.mark.asyncio
async def test_enqueue_build_on_clean_skips_when_document_missing(monkeypatch) -> None:
    calls = _install_repos(monkeypatch, doc=None)
    await km._enqueue_build_on_clean(_sm(), uuid.uuid4(), entered=True)
    assert calls == []
