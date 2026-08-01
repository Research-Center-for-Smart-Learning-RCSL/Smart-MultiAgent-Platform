"""Wiring tier — F-5 Knowledge Map scan gating against real Postgres.

The build- and retrieval-eligibility selectors must admit a document only when its
scan verdict is ``clean``. A ``pending`` document (freshly committed, scan not yet
returned) must be excluded from both — closing the race where a build indexes a
never-scanned document. ``quarantined`` and ``skipped`` remain excluded as before.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.identity.infrastructure.repositories import UserRepository
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.knowledge.application.knowmap_ingest_service import (
    KnowmapIngestInput,
    KnowmapIngestService,
)
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure.chunkers import chunk_document
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapChunkRepository,
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel.auth.clients import now
from shared_kernel.db.session import async_session

pytestmark = pytest.mark.wiring

_TEXT = b"Knowledge Map retry body. " * 40


class _FakeBlob:
    async def put(self, *, bucket: str, key: str, data: bytes, content_type: str) -> str:
        return f"{bucket}/{key}"

    async def get(self, *, bucket: str, key: str) -> bytes:
        return _TEXT

    async def download_to_path(self, *, bucket: str, key: str, path: Path) -> None:
        path.write_bytes(_TEXT)


async def _seed(db) -> SimpleNamespace:
    u = uuid.uuid4().hex[:8]
    user = await UserRepository(db).insert(email=f"km-{u}@smap.test", password_hash="x" * 16)
    org = await OrgRepository(db).create(name=f"org-{u}", creator_user_id=user.id)
    await OrgMemberRepository(db).add(
        org_id=org.id, user_id=user.id, role=OrgMemberRole.OWNER, is_original_creator=True
    )
    project = await ProjectRepository(db).create(
        name=f"proj-{u}", owner_user_id=None, owner_org_id=org.id, created_by_user_id=user.id
    )
    await ProjectMemberRepository(db).add(
        project_id=project.id, user_id=user.id, role=ProjectMemberRole.OWNER
    )
    builder_kg = await KeyGroupRepository(db).create(project_id=project.id, name="builder")
    cfg = await KnowmapConfigRepository(db).create(
        project_id=project.id,
        name=f"km-{u}",
        builder_key_group_id=builder_kg.id,
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params={"chunk_size_tokens": 512, "chunk_overlap_tokens": 64},
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        embed_dim=1536,
    )
    return SimpleNamespace(user=user, project=project, config=cfg)


async def _doc(db, cfg_id, agent_id, *, status, scan):
    repo = KnowmapDocumentRepository(db)
    doc = await repo.create(
        knowmap_config_id=cfg_id,
        filename=f"{uuid.uuid4().hex[:6]}.txt",
        mime="text/plain",
        size_bytes=10,
        sha256=uuid.uuid4().hex,
        minio_path=f"knowmap-sources/{uuid.uuid4().hex}",
        uploaded_by=None,
        agent_ids=[agent_id],
    )
    await repo.set_status(document_id=doc.id, status=status)
    if scan is not None:
        await repo.mark_scan(document_id=doc.id, scan_status=scan, scan_at=now())
    return doc


async def test_selectors_require_clean_verdict() -> None:
    async with async_session() as db:
        env = await _seed(db)
        agent_id = uuid.uuid4()
        cfg_id = env.config.id

        # READY but scan still pending (the F-5 race window) -> excluded.
        pending = await _doc(db, cfg_id, agent_id, status=DocumentStatus.READY, scan=None)
        # READY + clean -> the only eligible document.
        clean = await _doc(db, cfg_id, agent_id, status=DocumentStatus.READY, scan=ScanStatus.CLEAN)
        # quarantined / skipped remain excluded.
        await _doc(db, cfg_id, agent_id, status=DocumentStatus.READY, scan=ScanStatus.QUARANTINED)
        await _doc(db, cfg_id, agent_id, status=DocumentStatus.READY, scan=ScanStatus.SKIPPED)
        await db.commit()

        repo = KnowmapDocumentRepository(db)
        ready_ids = await repo.ready_document_ids(config_id=cfg_id)
        assert ready_ids == [clean.id]
        assert pending.id not in ready_ids

        allowed = await repo.allowed_document_ids(config_id=cfg_id, agent_id=agent_id)
        assert allowed == [clean.id]


async def test_failed_reupload_applies_submitted_allowlist() -> None:
    async with async_session() as db:
        env = await _seed(db)
        agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
        sha = hashlib.sha256(_TEXT).hexdigest()
        repo = KnowmapDocumentRepository(db)
        existing = await repo.create(
            knowmap_config_id=env.config.id,
            filename="retry.txt",
            mime="text/plain",
            size_bytes=len(_TEXT),
            sha256=sha,
            minio_path=f"knowmap-sources/{env.project.id}/{env.config.id}/{sha}",
            uploaded_by=env.user.id,
            agent_ids=[agent_a],
        )
        await repo.set_status(document_id=existing.id, status=DocumentStatus.FAILED)
        await db.commit()

        service = KnowmapIngestService(
            db,
            blob=_FakeBlob(),
            embedder=MagicMock(vector_size=1536),
            configs=KnowmapConfigRepository(db),
            documents=KnowmapDocumentRepository(db),
            chunks=KnowmapChunkRepository(db),
            chunker=chunk_document,
            scan_required=False,
        )
        with patch(
            "contexts.knowledge.application.knowmap_ingest_service.enqueue_knowmap_scan",
            AsyncMock(),
        ):
            returned = await service.ingest(
                ipt=KnowmapIngestInput(
                    knowmap_config_id=env.config.id,
                    filename="retry.txt",
                    mime="text/plain",
                    data=_TEXT,
                    uploaded_by=env.user.id,
                    agent_ids=(agent_a, agent_b),
                ),
                actor_user_id=env.user.id,
                actor_ip=None,
            )
        await db.commit()

        persisted = await repo.require(existing.id)
        assert returned.agent_ids == (agent_a, agent_b)
        assert persisted.agent_ids == (agent_a, agent_b)
