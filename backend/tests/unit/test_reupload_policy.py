from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from contexts.knowledge.application.ingest_service import (
    emit_reupload_agents_set_audit,
    emit_reupload_audit,
)
from contexts.knowledge.application.knowmap_ingest_service import (
    emit_knowmap_reupload_agents_set_audit,
    emit_knowmap_reupload_audit,
)
from contexts.knowledge.domain.models import DocumentStatus
from contexts.knowledge.domain.reupload import ReuploadAction, resolve_existing_document


def test_non_ready_document_reindexes_with_submitted_allowlist() -> None:
    assert (
        resolve_existing_document(
            status=DocumentStatus.FAILED,
            stored_agent_ids=[uuid.uuid4()],
            submitted_agent_ids=[uuid.uuid4()],
        )
        is ReuploadAction.REINDEX_WITH_OVERWRITE
    )


def test_ready_document_with_same_allowlist_is_dedup_noop_regardless_of_order() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    assert (
        resolve_existing_document(
            status=DocumentStatus.READY,
            stored_agent_ids=[agent_a, agent_b],
            submitted_agent_ids=[agent_b, agent_a],
        )
        is ReuploadAction.DEDUP_NOOP
    )


def test_ready_document_with_different_allowlist_conflicts() -> None:
    assert (
        resolve_existing_document(
            status=DocumentStatus.READY,
            stored_agent_ids=[uuid.uuid4()],
            submitted_agent_ids=[],
        )
        is ReuploadAction.CONFLICT
    )


async def test_rag_reupload_audits_capture_submitted_allowlist_and_outcome() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    project_id = uuid.uuid4()
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        rag_config_id=uuid.uuid4(),
        filename="doc.txt",
        mime="text/plain",
        size_bytes=100,
        sha256="abc123",
        agent_ids=(agent_a, agent_b),
    )

    with patch(
        "contexts.knowledge.application.ingest_service.audit.emit",
        AsyncMock(),
    ) as emit:
        await emit_reupload_audit(
            AsyncMock(),
            doc=doc,
            submitted_agent_ids=(agent_a, agent_b),
            outcome=ReuploadAction.REINDEX_WITH_OVERWRITE,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            request_id=uuid.uuid4(),
        )
        await emit_reupload_agents_set_audit(
            AsyncMock(),
            doc=doc,
            project_id=project_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            request_id=uuid.uuid4(),
        )

    uploaded, agents_set = (call.args[1] for call in emit.await_args_list)
    assert uploaded.action == "rag.document_uploaded"
    assert uploaded.metadata["agent_ids"] == [str(agent_a), str(agent_b)]
    assert uploaded.metadata["reupload"] is True
    assert uploaded.metadata["outcome"] == "reindex_with_overwrite"
    assert agents_set.action == "rag.document_agents_set"
    assert agents_set.metadata["agent_ids"] == [str(agent_a), str(agent_b)]
    assert agents_set.metadata["source"] == "reupload"


async def test_knowmap_reupload_audits_capture_submitted_allowlist_and_outcome() -> None:
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    project_id = uuid.uuid4()
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        knowmap_config_id=uuid.uuid4(),
        filename="doc.txt",
        mime="text/plain",
        size_bytes=100,
        sha256="abc123",
        agent_ids=(agent_a, agent_b),
    )

    with patch(
        "contexts.knowledge.application.knowmap_ingest_service.audit.emit",
        AsyncMock(),
    ) as emit:
        await emit_knowmap_reupload_audit(
            AsyncMock(),
            doc=doc,
            submitted_agent_ids=(agent_a, agent_b),
            outcome=ReuploadAction.REINDEX_WITH_OVERWRITE,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            request_id=uuid.uuid4(),
        )
        await emit_knowmap_reupload_agents_set_audit(
            AsyncMock(),
            doc=doc,
            project_id=project_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            request_id=uuid.uuid4(),
        )

    uploaded, agents_set = (call.args[1] for call in emit.await_args_list)
    assert uploaded.action == "knowmap.document_uploaded"
    assert uploaded.metadata["agent_ids"] == [str(agent_a), str(agent_b)]
    assert uploaded.metadata["reupload"] is True
    assert uploaded.metadata["outcome"] == "reindex_with_overwrite"
    assert agents_set.action == "knowmap.document_agents_set"
    assert agents_set.metadata["agent_ids"] == [str(agent_a), str(agent_b)]
    assert agents_set.metadata["source"] == "reupload"
