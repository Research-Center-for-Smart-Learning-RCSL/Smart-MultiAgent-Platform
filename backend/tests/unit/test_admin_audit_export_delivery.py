"""`POST /api/admin/audit/export` returns a browser-reachable CSV link ([R19a.14])."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import admin_audit
from tests.unit.file_ticket_support import decode, ticket_signer

__all__ = ["ticket_signer"]


@pytest.mark.usefixtures("ticket_signer")
async def test_export_returns_typed_out_with_attachment_link() -> None:
    minio = SimpleNamespace(exports_bucket="exports", put_object=AsyncMock())
    facade = SimpleNamespace(export_csv=AsyncMock(return_value=b"a,b\n"))
    with (
        patch.object(admin_audit, "AuditFacade", return_value=facade),
        patch.object(admin_audit, "get_minio_client", return_value=minio),
        patch.object(admin_audit.audit, "emit", AsyncMock()),
    ):
        out = await admin_audit.export_audit(
            actor_user_id=None,
            resource_type=None,
            resource_id=None,
            action=None,
            from_ts=datetime(2026, 10, 1, tzinfo=UTC),
            to_ts=datetime(2026, 10, 11, tzinfo=UTC),
            ip_prefix=None,
            session_id=None,
            request_id=None,
            admin=SimpleNamespace(user_id=uuid.uuid4()),  # type: ignore[arg-type]
            ctx=SimpleNamespace(actor_ip="10.0.0.1", request_id=uuid.uuid4()),  # type: ignore[arg-type]
            db=SimpleNamespace(),  # type: ignore[arg-type]
        )
    assert isinstance(out, admin_audit.AuditExportOut)
    ticket = await decode(out.url)
    assert ticket.bucket == "exports"
    assert ticket.key == f"{out.job_id}/audit_export.csv"
    assert ticket.content_type == "text/csv; charset=utf-8"
    assert ticket.disposition.startswith('attachment; filename="audit-20261001-20261011.csv"')
