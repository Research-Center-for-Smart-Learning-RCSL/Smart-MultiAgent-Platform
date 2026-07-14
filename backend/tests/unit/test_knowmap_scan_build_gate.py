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
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.models import DocumentStatus

_CFG_ID = uuid.uuid4()


class _Session:
    def __init__(self, db: object) -> None:
        self._db = db

    async def __aenter__(self) -> object:
        return self._db

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _sm(db: object) -> Any:
    return lambda: _Session(db)


def _install_repos(monkeypatch, *, doc: object, cfg: object) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _DocRepo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, doc_id: uuid.UUID) -> object:
            return doc

    class _CfgRepo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, cfg_id: uuid.UUID) -> object:
            return cfg

    async def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(km, "KnowmapDocumentRepository", _DocRepo)
    monkeypatch.setattr(km, "KnowmapConfigRepository", _CfgRepo)
    monkeypatch.setattr(km, "enqueue_knowmap_build", _capture)
    return calls


@pytest.mark.asyncio
async def test_enqueue_build_on_clean_enqueues_when_ready(monkeypatch) -> None:
    doc = SimpleNamespace(status=DocumentStatus.READY, knowmap_config_id=_CFG_ID)
    cfg = SimpleNamespace(id=_CFG_ID, last_build_state=BuildState.IDLE, last_build_at=None)
    calls = _install_repos(monkeypatch, doc=doc, cfg=cfg)

    await km._enqueue_build_on_clean(_sm(object()), uuid.uuid4())

    assert calls == [{"config_id": _CFG_ID, "last_build_state": BuildState.IDLE, "last_build_at": None}]


@pytest.mark.asyncio
async def test_enqueue_build_on_clean_skips_when_not_ready(monkeypatch) -> None:
    # Async tus path: clean verdict arrives before indexing sets READY -> defer to
    # the index worker (which enqueues when it sees the clean verdict).
    doc = SimpleNamespace(status=DocumentStatus.INGESTING, knowmap_config_id=_CFG_ID)
    cfg = SimpleNamespace(id=_CFG_ID, last_build_state=BuildState.IDLE, last_build_at=None)
    calls = _install_repos(monkeypatch, doc=doc, cfg=cfg)

    await km._enqueue_build_on_clean(_sm(object()), uuid.uuid4())

    assert calls == []


@pytest.mark.asyncio
async def test_enqueue_build_on_clean_skips_when_document_missing(monkeypatch) -> None:
    calls = _install_repos(monkeypatch, doc=None, cfg=None)
    await km._enqueue_build_on_clean(_sm(object()), uuid.uuid4())
    assert calls == []
