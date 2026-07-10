"""Regression: external-store purge flags must reflect what actually ran.

Both `graphrag_config_service.purge_config_external_stores` and
`KnowmapConfigService.cascade_external_stores` used to initialize their
`neo4j_purged`/`qdrant_purged` result flags to `True` before checking whether
the neo4j/vectors client was even available, so a missing client (e.g.
`settings.neo4j` unset) reported a purge that never ran — an audit row
claiming success while silently orphaning the config's Neo4j subgraph or
Qdrant collection (found in code review, 2026-07-10).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.graphrag_config_service import (
    purge_config_external_stores,
)
from contexts.knowledge.application.knowmap_config_service import KnowmapConfigService


class _FakeNeo4j:
    def __init__(self, *, raise_on_delete: bool = False) -> None:
        self.raise_on_delete = raise_on_delete
        self.deleted: list[uuid.UUID] = []

    async def delete_all(self, *, config_id: uuid.UUID) -> None:
        if self.raise_on_delete:
            raise RuntimeError("neo4j down")
        self.deleted.append(config_id)


class _FakeVectors:
    def __init__(self, *, raise_on_delete: bool = False) -> None:
        self.raise_on_delete = raise_on_delete
        self.deleted: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def delete_by_config(self, *, project_id: uuid.UUID, config_id: uuid.UUID) -> None:
        if self.raise_on_delete:
            raise RuntimeError("qdrant down")
        self.deleted.append((project_id, config_id))


@pytest.mark.asyncio
async def test_purge_reports_neo4j_not_purged_when_client_absent() -> None:
    config_id, project_id = uuid.uuid4(), uuid.uuid4()
    vectors = _FakeVectors()

    outcome = await purge_config_external_stores(
        config_id=config_id, project_id=project_id, neo4j=None, vectors=vectors
    )

    assert outcome["neo4j_purged"] is False
    assert outcome["qdrant_purged"] is True
    assert vectors.deleted == [(project_id, config_id)]


@pytest.mark.asyncio
async def test_purge_reports_qdrant_not_purged_when_vectors_absent() -> None:
    config_id, project_id = uuid.uuid4(), uuid.uuid4()
    neo4j = _FakeNeo4j()

    outcome = await purge_config_external_stores(
        config_id=config_id, project_id=project_id, neo4j=neo4j, vectors=None
    )

    assert outcome["neo4j_purged"] is True
    assert outcome["qdrant_purged"] is False
    assert neo4j.deleted == [config_id]


@pytest.mark.asyncio
async def test_purge_reports_qdrant_not_purged_when_project_id_absent() -> None:
    config_id = uuid.uuid4()
    neo4j, vectors = _FakeNeo4j(), _FakeVectors()

    outcome = await purge_config_external_stores(
        config_id=config_id, project_id=None, neo4j=neo4j, vectors=vectors
    )

    assert outcome["neo4j_purged"] is True
    assert outcome["qdrant_purged"] is False
    assert vectors.deleted == []


@pytest.mark.asyncio
async def test_purge_reports_failure_not_success_on_exception() -> None:
    config_id, project_id = uuid.uuid4(), uuid.uuid4()
    neo4j = _FakeNeo4j(raise_on_delete=True)
    vectors = _FakeVectors(raise_on_delete=True)

    outcome = await purge_config_external_stores(
        config_id=config_id, project_id=project_id, neo4j=neo4j, vectors=vectors
    )

    assert outcome == {"neo4j_purged": False, "qdrant_purged": False}


@pytest.mark.asyncio
async def test_purge_reports_both_purged_on_success() -> None:
    config_id, project_id = uuid.uuid4(), uuid.uuid4()
    neo4j, vectors = _FakeNeo4j(), _FakeVectors()

    outcome = await purge_config_external_stores(
        config_id=config_id, project_id=project_id, neo4j=neo4j, vectors=vectors
    )

    assert outcome == {"neo4j_purged": True, "qdrant_purged": True}


@pytest.mark.asyncio
async def test_knowmap_cascade_reports_neo4j_not_purged_when_neo4j_unconfigured() -> None:
    config_id, project_id = uuid.uuid4(), uuid.uuid4()

    fake_settings = type("S", (), {"neo4j": None, "qdrant": type("Q", (), {"url": "x", "api_key": None})()})()

    with (
        patch("app.config.settings.get_settings", return_value=fake_settings),
        patch("qdrant_client.AsyncQdrantClient") as qclient_cls,
        patch("contexts.knowledge.infrastructure.graphrag_vector_store.GraphRagVectorStore") as vectors_cls,
    ):
        qclient = AsyncMock()
        qclient_cls.return_value = qclient
        vectors = AsyncMock()
        vectors_cls.return_value = vectors

        outcome = await KnowmapConfigService.cascade_external_stores(
            config_id=config_id, project_id=project_id
        )

    assert outcome["neo4j_purged"] is False
    assert outcome["qdrant_purged"] is True
    vectors.delete_by_config.assert_awaited_once_with(project_id=project_id, config_id=config_id)
