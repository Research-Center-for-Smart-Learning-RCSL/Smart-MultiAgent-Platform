"""`GET /api/skills/exports/{task_id}` returns a browser-reachable zip link ([R19a.14])."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import skills as skill_routes
from contexts.skills.application.bundle_jobs import BundleJobStatus
from tests.unit.file_ticket_support import decode, ticket_signer

__all__ = ["ticket_signer"]


@pytest.mark.usefixtures("ticket_signer")
async def test_ready_bundle_gets_attachment_link() -> None:
    user = uuid.uuid4()
    job = uuid.uuid4()
    state = SimpleNamespace(
        job_id=job,
        actor_user_id=user,
        status=BundleJobStatus.READY,
        bucket="exports",
        object_key=f"skills/{job}/skill.zip",
        error=None,
    )
    with patch.object(skill_routes.SkillsFacade, "get_export_job", AsyncMock(return_value=state)):
        out = await skill_routes.get_export_status(
            task_id=job,
            principal=SimpleNamespace(user_id=user, is_admin=False),  # type: ignore[arg-type]
        )
    ticket = await decode(out.url)
    assert (ticket.bucket, ticket.key) == ("exports", f"skills/{job}/skill.zip")
    assert ticket.content_type == "application/zip"
    assert ticket.disposition.startswith('attachment; filename="skill.zip"')
