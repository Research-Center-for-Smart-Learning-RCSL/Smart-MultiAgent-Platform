"""The scan job must not be enqueued before the row it names is committed.

`skill_scan_file` looks the file up by id on its **own connection** (`get_sessionmaker`
inside the worker). If the enqueue happens inside the request's still-open transaction,
the worker can win the race, find nothing, and return "not_found" — which is terminal, not
a retry. The file then sits `pending` forever, and under AC-34's fail-closed gate that
means the owning skill is permanently unreadable.

`db_session`'s docstring names this exact case: "An endpoint that must run work *after* a
durable commit — e.g. enqueueing Arq jobs that reference a just-written row — may call
``await session.commit()`` itself". The RAG path does it at `ingest_service.py:165` and
`:234`. These tests pin that Skills does too.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.skills.domain.models import (
    Skill,
    SkillFile,
    SkillFileKind,
    SkillScanStatus,
    SkillScope,
    SkillSource,
)
from contexts.skills.interfaces import facade as facade_mod


def _skill() -> Skill:
    return Skill(
        id=uuid.uuid4(),
        scope=SkillScope.PROJECT,
        agent_id=None,
        project_id=uuid.uuid4(),
        org_id=None,
        name="pdf-fill",
        description="Fills PDFs.",
        body="# Body",
        body_sha256="0" * 64,
        source=SkillSource.AUTHORED,
        bundle_sha256=None,
        requires=(),
        allowed_tools=(),
        extra_frontmatter={},
        created_by=uuid.uuid4(),
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _file(skill_id: uuid.UUID) -> SkillFile:
    return SkillFile(
        id=uuid.uuid4(),
        skill_id=skill_id,
        path="references/guide.md",
        kind=SkillFileKind.REFERENCE,
        mime="text/markdown",
        size_bytes=6,
        sha256="b" * 64,
        minio_key="k",
        scan_status=SkillScanStatus.PENDING,
        extracted_chars=6,
        created_at=datetime.now(UTC),
    )


class _Recorder:
    """Records the order of the two operations whose order is the bug."""

    def __init__(self) -> None:
        self.events: list[str] = []


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    skill = _skill()
    created = _file(skill.id)

    class _Db:
        async def commit(self) -> None:
            r.events.append("commit")

    class _SkillService:
        def __init__(self, _db: object) -> None:
            pass

        async def get_owned(self, *a: Any, **k: Any) -> Skill:
            return skill

    class _FileService:
        def __init__(self, _db: object) -> None:
            pass

        async def add(self, **k: Any) -> SkillFile:
            r.events.append("insert")
            return created

        async def get_owned(self, *a: Any, **k: Any) -> SkillFile:
            return created

        async def update_content(self, **k: Any) -> SkillFile:
            r.events.append("insert")
            return created

    async def _enqueue(*, file_id: uuid.UUID, sha256: str) -> None:
        r.events.append("enqueue")

    monkeypatch.setattr(facade_mod, "SkillService", _SkillService)
    monkeypatch.setattr(facade_mod, "SkillFileService", _FileService)
    monkeypatch.setattr(facade_mod, "enqueue_skill_scan", _enqueue)
    monkeypatch.setattr(
        facade_mod,
        "get_settings",
        lambda: type("S", (), {"security": type("X", (), {"file_scan_enabled": True})()})(),
    )

    r.facade = facade_mod.SkillsFacade(_Db())  # type: ignore[attr-defined]
    r.skill = skill  # type: ignore[attr-defined]
    r.created = created  # type: ignore[attr-defined]
    return r


async def test_add_file_commits_before_enqueueing_the_scan(rec: _Recorder) -> None:
    await rec.facade.add_file(  # type: ignore[attr-defined]
        rec.skill.id,  # type: ignore[attr-defined]
        SkillScope.PROJECT,
        owner_id=rec.skill.project_id,  # type: ignore[attr-defined]
        path="references/guide.md",
        data=b"# Guide",
        mime="text/markdown",
        actor_user_id=uuid.uuid4(),
    )
    # The worker reads on its own connection, so an enqueue before the commit is a race
    # it can win — and losing it is terminal, not a retry.
    assert rec.events == ["insert", "commit", "enqueue"]


async def test_update_file_commits_before_enqueueing_the_scan(rec: _Recorder) -> None:
    # The edit path resets scan_status to pending, so it has exactly the same exposure:
    # a lost scan leaves an edited file unreadable rather than merely unscanned.
    await rec.facade.update_file(  # type: ignore[attr-defined]
        rec.skill.id,  # type: ignore[attr-defined]
        SkillScope.PROJECT,
        rec.created.id,  # type: ignore[attr-defined]
        owner_id=rec.skill.project_id,  # type: ignore[attr-defined]
        data=b"new",
        actor_user_id=uuid.uuid4(),
    )
    assert rec.events == ["insert", "commit", "enqueue"]
