"""Creativity scores reach the teacher and the research export only (AC-17, Q-11).

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5
(Visibility). A score shown to a participant would change their behaviour, and
one in an agent's context would let a judge model steer facilitation.

The scores live in ``activity_submissions.judge_scores``. These tests pin that
the column is named only by the code that writes it, the export that carries
it and the dashboard read, and that the room-facing shapes have no field for it.
"""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.api.v1.activities as activities_api
from app.api.v1.activities import ActivitySubmissionOut
from contexts.activities.domain.models import ActivitySubmission, RecentActivityRow

_BACKEND = Path(__file__).resolve().parents[2]
_COLUMN = "judge_scores"
_ALLOWED = {
    "app/workers/tasks/activities.py",
    "app/workers/tasks/conversation.py",
    "contexts/activities/domain/creativity_judge.py",
    "contexts/activities/domain/models.py",
    "contexts/activities/infrastructure/tables.py",
    "contexts/activities/infrastructure/repositories/submission_repo.py",
    "contexts/conversation/application/research_export_builder.py",
}


def _naming(root: str) -> set[str]:
    files = list((_BACKEND / root).rglob("*.py"))
    assert files, f"the scan found nothing under {root}"
    return {p.relative_to(_BACKEND).as_posix() for p in files if _COLUMN in p.read_text(encoding="utf-8")}


def test_only_the_writers_the_export_and_the_dashboard_read_name_the_scores() -> None:
    named = _naming("app") | _naming("contexts") | _naming("shared_kernel")
    assert named <= _ALLOWED, named - _ALLOWED


def test_the_scan_would_see_a_reference() -> None:
    assert "contexts/activities/infrastructure/repositories/submission_repo.py" in _naming("contexts")


@pytest.mark.parametrize("shape", [ActivitySubmission, RecentActivityRow])
def test_the_room_facing_domain_shapes_carry_no_score(shape: type) -> None:
    """The agent digest, the activity context and the echo are built from these."""
    names = {f.name for f in dataclasses.fields(shape)}
    assert not {n for n in names if "score" in n and n != "sub_scores"}, names


def test_the_submission_response_has_no_score_field() -> None:
    names = set(ActivitySubmissionOut.model_fields)
    assert not {n for n in names if "judge" in n or "creativ" in n}, names


async def test_the_room_event_for_a_scored_submission_names_no_judge_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[tuple[str, dict[str, Any]]] = []

    class _Publisher:
        def __init__(self, _channel: object) -> None:
            pass

        async def emit(self, event: str, payload: dict[str, Any]) -> None:
            emitted.append((event, payload))

    monkeypatch.setattr(activities_api, "Publisher", _Publisher)
    monkeypatch.setattr(activities_api, "room_channel", lambda _r: "room")
    monkeypatch.setattr(activities_api, "enqueue", AsyncMock())
    monkeypatch.setattr(activities_api, "dispatch_room_activation_progress", AsyncMock())
    monkeypatch.setattr(activities_api, "dispatch_all_submitted", AsyncMock())
    monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: AsyncMock())
    submission = SimpleNamespace(
        id=uuid.uuid4(),
        activity_type_id=uuid.uuid4(),
        validation_status=SimpleNamespace(value="validated"),
        is_valid=True,
        judge_status="pending",
    )

    await activities_api._dispatch_submission(uuid.uuid4(), submission, {}, db=object())  # type: ignore[arg-type]

    assert emitted, "the dispatch emitted nothing, so this test proves nothing"
    for _event, payload in emitted:
        assert not {k for k in payload if "judge" in k or "score" in k}, payload
