"""Unit tests for Knowledge Map API AuthZ guards (Phase 3, WS3 / AC-8).

Two surfaces:
- ``validate_knowmap_agent_allowlist`` — a document's allowlist may only name agents
  bound to *this* config (``agent.knowmap_config_id == config_id``); an unbound id is a
  422, never a silent no-op.
- ``_require_owner`` — only a Project Owner (or platform admin) may upload documents.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.knowmap import (
    KnowmapDocumentAgentsPatchIn,
    _require_owner,
    set_knowmap_document_agents,
    validate_knowmap_agent_allowlist,
)
from contexts.knowledge.domain.models import DocumentStatus, ScanStatus
from shared_kernel.auth.permissions import Decision

_CONFIG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _agent(agent_id: uuid.UUID, config_id: uuid.UUID | None) -> SimpleNamespace:
    return SimpleNamespace(id=agent_id, knowmap_config_id=config_id)


def _patch_agents(agents: list[SimpleNamespace]):
    facade = MagicMock()
    facade.return_value.list_agents_for_project = AsyncMock(return_value=agents)
    return patch("contexts.agents.interfaces.facade.AgentsFacade", facade)


class TestValidateAllowlist:
    async def test_empty_is_allowed(self) -> None:
        out = await validate_knowmap_agent_allowlist(
            db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[]
        )
        assert out == []

    async def test_bound_agents_pass_and_dedup(self) -> None:
        a = uuid.uuid4()
        with _patch_agents([_agent(a, _CONFIG_ID)]):
            out = await validate_knowmap_agent_allowlist(
                db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[a, a]
            )
        assert out == [a]

    async def test_agent_bound_to_other_config_is_rejected(self) -> None:
        # Naming an agent bound to a *different* knowmap config leaks nothing at
        # retrieval but is an AuthZ smell -> reject at the boundary.
        a = uuid.uuid4()
        with _patch_agents([_agent(a, uuid.uuid4())]), pytest.raises(HTTPException) as exc:
            await validate_knowmap_agent_allowlist(
                db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[a]
            )
        assert exc.value.status_code == 422

    async def test_unknown_agent_is_rejected(self) -> None:
        with _patch_agents([]), pytest.raises(HTTPException) as exc:
            await validate_knowmap_agent_allowlist(
                db=AsyncMock(), config_id=_CONFIG_ID, project_id=_PROJECT_ID, agent_ids=[uuid.uuid4()]
            )
        assert exc.value.status_code == 422


class TestRequireOwner:
    async def test_admin_short_circuits(self) -> None:
        principal = SimpleNamespace(is_admin=True, user_id=uuid.uuid4())
        # No TenancyFacade lookup for an admin.
        await _require_owner(db=AsyncMock(), project_id=_PROJECT_ID, principal=principal)

    async def test_owner_passes(self) -> None:
        principal = SimpleNamespace(is_admin=False, user_id=uuid.uuid4())
        facade = MagicMock()
        facade.return_value.is_project_owner = AsyncMock(return_value=True)
        with patch("app.api.v1.knowmap.TenancyFacade", facade):
            await _require_owner(db=AsyncMock(), project_id=_PROJECT_ID, principal=principal)

    async def test_non_owner_is_forbidden(self) -> None:
        principal = SimpleNamespace(is_admin=False, user_id=uuid.uuid4())
        facade = MagicMock()
        facade.return_value.is_project_owner = AsyncMock(return_value=False)
        with patch("app.api.v1.knowmap.TenancyFacade", facade), pytest.raises(HTTPException) as exc:
            await _require_owner(db=AsyncMock(), project_id=_PROJECT_ID, principal=principal)
        assert exc.value.status_code == 403


class TestSetDocumentAgentsOwnerGate:
    """set_knowmap_document_agents's own docstring promises DOM-7: missing
    and forbidden both 404. A capability-holding non-owner must get the same
    masked 404 as a missing document -- not a distinguishing 403 (which
    would let them tell "exists, no access" from "does not exist")."""

    def _setup(self, *, is_admin: bool, is_owner: bool):
        from datetime import datetime

        doc = SimpleNamespace(
            id=uuid.uuid4(),
            knowmap_config_id=_CONFIG_ID,
            filename="a.pdf",
            mime="application/pdf",
            size_bytes=1,
            sha256="x",
            status=DocumentStatus.READY,
            scan_status=ScanStatus.CLEAN,
            uploaded_at=datetime(2026, 1, 1),
            agent_ids=[],
        )
        cfg = SimpleNamespace(project_id=_PROJECT_ID)
        principal = SimpleNamespace(is_admin=is_admin, user_id=uuid.uuid4())

        docs_repo = MagicMock()
        docs_repo.return_value.require = AsyncMock(return_value=doc)
        docs_repo.return_value.set_agents = AsyncMock(return_value=doc)

        config_service = MagicMock()
        config_service.return_value.get = AsyncMock(return_value=cfg)

        tenancy = MagicMock()
        tenancy.return_value.is_project_owner = AsyncMock(return_value=is_owner)

        return doc, principal, docs_repo, config_service, tenancy

    async def test_capability_holder_but_not_owner_gets_masked_404(self) -> None:
        _doc, principal, docs_repo, config_service, tenancy = self._setup(is_admin=False, is_owner=False)

        with (
            patch("app.api.v1.knowmap.KnowmapDocumentRepository", docs_repo),
            patch("app.api.v1.knowmap.KnowmapConfigService", config_service),
            patch("app.api.v1.knowmap.TenancyFacade", tenancy),
            patch(
                "shared_kernel.auth.dependencies.get_role_resolver",
                AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "shared_kernel.auth.permissions.decide",
                AsyncMock(return_value=Decision.allow("ok")),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await set_knowmap_document_agents(
                body=KnowmapDocumentAgentsPatchIn(agent_ids=[]),
                document_id=uuid.uuid4(),
                ctx=SimpleNamespace(actor_ip=None, request_id=uuid.uuid4()),
                principal=principal,
                db=AsyncMock(),
            )

        assert exc.value.status_code == 404
        assert exc.value.detail == "knowmap document not found"
        docs_repo.return_value.set_agents.assert_not_awaited()

    async def test_owner_passes_through(self) -> None:
        doc, principal, docs_repo, config_service, tenancy = self._setup(is_admin=False, is_owner=True)

        with (
            patch("app.api.v1.knowmap.KnowmapDocumentRepository", docs_repo),
            patch("app.api.v1.knowmap.KnowmapConfigService", config_service),
            patch("app.api.v1.knowmap.TenancyFacade", tenancy),
            patch(
                "shared_kernel.auth.dependencies.get_role_resolver",
                AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "shared_kernel.auth.permissions.decide",
                AsyncMock(return_value=Decision.allow("ok")),
            ),
            patch(
                "app.api.v1.knowmap.validate_knowmap_agent_allowlist",
                AsyncMock(return_value=[]),
            ),
            patch("shared_kernel.audit.emit", AsyncMock()),
        ):
            out = await set_knowmap_document_agents(
                body=KnowmapDocumentAgentsPatchIn(agent_ids=[]),
                document_id=uuid.uuid4(),
                ctx=SimpleNamespace(actor_ip=None, request_id=uuid.uuid4()),
                principal=principal,
                db=AsyncMock(),
            )

        assert out.id == doc.id
        docs_repo.return_value.set_agents.assert_awaited_once()
