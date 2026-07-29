from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import knowmap, rag
from contexts.knowledge.domain.errors import DocumentAllowlistConflict
from contexts.knowledge.interfaces.error_mapping import register
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session


@pytest.mark.parametrize(
    ("route", "patch_path"),
    [
        ("/rag-upload", "/api/rag-documents/doc-rag/agents"),
        ("/knowmap-upload", "/api/knowmap-documents/doc-km/agents"),
    ],
)
def test_duplicate_allowlist_conflict_is_actionable_problem_json(
    route: str,
    patch_path: str,
) -> None:
    app = FastAPI()
    register(app)

    @app.post(route)
    async def _upload() -> None:
        raise DocumentAllowlistConflict(
            f"document already exists with a different agent allowlist; use PATCH {patch_path}"
        )

    response = TestClient(app).post(route)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://smap.local/problems/knowledge/document-allowlist-conflict",
        "title": "Document allowlist differs",
        "status": 409,
        "detail": ("document already exists with a different agent allowlist; " f"use PATCH {patch_path}"),
        "instance": route,
    }


def test_rag_upload_route_surfaces_allowlist_conflict_as_problem_json() -> None:
    config_id = uuid.uuid4()
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    db = AsyncMock()
    principal = Principal(user_id=user_id, is_admin=False, email_verified=True)
    ctx = RequestContext(principal=principal)
    cfg = SimpleNamespace(
        id=config_id,
        project_id=uuid.uuid4(),
        embed_key_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
    )
    ingest = AsyncMock()
    ingest.ingest.side_effect = DocumentAllowlistConflict(
        f"document {doc_id} already exists with a different agent allowlist; "
        f"use PATCH /api/rag-documents/{doc_id}/agents"
    )
    qclient = MagicMock(close=AsyncMock())

    app = FastAPI()
    register(app)
    app.include_router(rag.config_router)
    app.dependency_overrides[rag.current_context] = lambda: ctx
    app.dependency_overrides[rag.current_principal] = lambda: principal
    app.dependency_overrides[db_session] = lambda: db

    with (
        patch("app.api.v1.rag.RagConfigService") as service_cls,
        patch("app.api.v1.rag._require_owner", AsyncMock()),
        patch("app.api.v1.rag.validate_agent_allowlist", AsyncMock(return_value=[])),
        patch("app.api.v1.rag.router_embedder_for", MagicMock()),
        patch("app.api.v1.rag.build_router", MagicMock()),
        patch(
            "shared_kernel.auth.dependencies.get_role_resolver",
            AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "shared_kernel.auth.permissions.decide",
            AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ),
    ):
        service_cls.return_value.get = AsyncMock(return_value=cfg)
        service_cls.build_ingest_service.return_value = (ingest, qclient)
        response = TestClient(app).post(
            f"/api/rag-configs/{config_id}/documents",
            files={"file": ("doc.txt", b"same document", "text/plain")},
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/knowledge/document-allowlist-conflict")
    assert response.json()["detail"].endswith(f"/api/rag-documents/{doc_id}/agents")
    qclient.close.assert_awaited_once()


def test_knowmap_upload_route_surfaces_allowlist_conflict_as_problem_json() -> None:
    config_id = uuid.uuid4()
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    db = AsyncMock()
    principal = Principal(user_id=user_id, is_admin=False, email_verified=True)
    ctx = RequestContext(principal=principal)
    cfg = SimpleNamespace(id=config_id, project_id=uuid.uuid4())
    ingest = AsyncMock()
    ingest.ingest.side_effect = DocumentAllowlistConflict(
        f"document {doc_id} already exists with a different agent allowlist; "
        f"use PATCH /api/knowmap-documents/{doc_id}/agents"
    )

    app = FastAPI()
    register(app)
    app.include_router(knowmap.config_router)
    app.dependency_overrides[knowmap.current_context] = lambda: ctx
    app.dependency_overrides[knowmap.current_principal] = lambda: principal
    app.dependency_overrides[db_session] = lambda: db

    with (
        patch("app.api.v1.knowmap.KnowmapConfigService") as service_cls,
        patch("app.api.v1.knowmap._assert_edit", AsyncMock()),
        patch("app.api.v1.knowmap._require_owner", AsyncMock()),
        patch(
            "app.api.v1.knowmap.validate_knowmap_agent_allowlist",
            AsyncMock(return_value=[]),
        ),
        patch("app.api.v1.knowmap.build_knowmap_embedder", AsyncMock()),
    ):
        service_cls.return_value.get = AsyncMock(return_value=cfg)
        service_cls.build_ingest_service.return_value = ingest
        response = TestClient(app).post(
            f"/api/knowmap-configs/{config_id}/documents",
            files={"file": ("doc.txt", b"same document", "text/plain")},
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/knowledge/document-allowlist-conflict")
    assert response.json()["detail"].endswith(f"/api/knowmap-documents/{doc_id}/agents")
