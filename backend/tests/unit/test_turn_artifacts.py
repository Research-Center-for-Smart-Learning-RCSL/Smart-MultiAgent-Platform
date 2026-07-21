"""Unit test for TurnEngine._persist_artifacts (Code-Interpreter artifacts).

Drives the method with a fake DB + a stubbed AttachmentService so no MinIO or
Postgres is required — asserts dedupe, retrieval of artifacts the kernel could
not inline, that an unretrievable one is reported rather than dropped, and that
binding targets the reply message id.
"""

from __future__ import annotations

import base64
import logging
import uuid
from functools import partial
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from contexts.agents.application.runtime.turn_engine import TurnEngine


class _FakeDB:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeAttachmentService:
    instances: ClassVar[list[_FakeAttachmentService]] = []

    def __init__(self, _db: Any) -> None:
        self.uploaded: list[dict[str, Any]] = []
        self.persisted: dict[str, Any] | None = None
        _FakeAttachmentService.instances.append(self)

    async def upload_agent_artifact(self, **kwargs: Any) -> SimpleNamespace:
        self.uploaded.append(kwargs)
        return SimpleNamespace(attachment_id=uuid.uuid4(), filename=kwargs.get("filename"))

    async def persist_agent_artifacts(self, **kwargs: Any) -> int:
        self.persisted = kwargs
        return len(kwargs["uploads"])


def _engine() -> SimpleNamespace:
    """A `TurnEngine` stand-in with both infrastructure seams stubbed.

    `_artifact_bytes` is bound to the real implementation, so the acquisition
    strategy under test actually runs; only `_fetch_large_artifact` (which each
    test sets) and `_sandbox` stand in for infrastructure.

    Stubbing `_sandbox` is the point of having it. Before the seam existed these
    application-layer tests reached `docker_runsc_sandbox_from_settings()` for
    real, and passed only because it happens to construct without a Docker
    daemon -- a dependency they never meant to have and nothing declared.
    """
    engine = SimpleNamespace(_db=_FakeDB())
    engine._sandbox = lambda: SimpleNamespace()
    engine._artifact_bytes = partial(TurnEngine._artifact_bytes, engine)
    return engine


@pytest.mark.asyncio
async def test_persist_artifacts_dedupes_skips_and_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    msg_id = uuid.uuid4()
    room = uuid.uuid4()
    png = base64.b64encode(b"\x89PNG").decode("ascii")
    artifacts = [
        {"filename": "chart.png", "mime": "image/png", "rel_path": "/w/chart.png", "b64": png},
        {"filename": "chart.png", "mime": "image/png", "rel_path": "/w/chart.png", "b64": png},  # dup
        # Not inlined. Until 2026-07-19 this test asserted it was *skipped*, which
        # is how it stayed green over silent data loss; it is now fetched.
        {
            "filename": "big.csv",
            "mime": "text/csv",
            "rel_path": "/w/big.csv",
            "size_bytes": 9 * 1024 * 1024,
            "b64": None,
        },
    ]

    async def _fetch(_runner, _agent, _room, art):
        return b"col-a,col-b\n" if art["filename"] == "big.csv" else None

    engine._fetch_large_artifact = _fetch

    count = await TurnEngine._persist_artifacts(engine, agent, room, msg_id, artifacts)  # type: ignore[arg-type]

    assert count == 2  # dedup, plus the large one now retrieved rather than dropped
    svc = _FakeAttachmentService.instances[-1]
    assert len(svc.uploaded) == 2  # uploads run concurrently, one per unique artifact
    assert svc.uploaded[0]["filename"] == "chart.png"
    assert svc.uploaded[0]["project_id"] == agent.project_id
    assert svc.uploaded[1]["filename"] == "big.csv"
    assert svc.uploaded[1]["data"] == b"col-a,col-b\n"
    assert svc.persisted is not None
    assert svc.persisted["message_id"] == msg_id
    assert svc.persisted["chatroom_id"] == room
    assert len(svc.persisted["uploads"]) == 2
    assert engine._db.committed is True


@pytest.mark.asyncio
async def test_persist_artifacts_empty_is_noop() -> None:
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    count = await TurnEngine._persist_artifacts(engine, agent, uuid.uuid4(), uuid.uuid4(), [])  # type: ignore[arg-type]
    assert count == 0
    assert engine._db.committed is False


# --- 2026-07-19-large-artifacts-silently-dropped ----------------------------
#
# An artifact the kernel could not inline used to be dropped by a bare
# `continue`, with no log, no audit event and no signal to model or user. These
# pin the two halves of the fix: it is fetched when it can be, and its loss is
# reported when it cannot.


def _big(name: str = "big.bin", size: int = 9 * 1024 * 1024) -> dict:
    """A descriptor as the kernel emits it above _ARTIFACT_B64_CAP."""
    return {
        "filename": name,
        "mime": "application/octet-stream",
        "rel_path": f"/session/outputs/{name}",
        "size_bytes": size,
        "b64": None,
    }


@pytest.mark.asyncio
async def test_an_unfetchable_artifact_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """T-3/T-4/AC-3. The kernel can be evicted or reaped between the exec reply
    and the fetch, so retrieval genuinely can fail. What must not happen is what
    used to: the file vanishing with nothing written down anywhere."""
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())

    async def _fetch(_runner, _agent, _room, _art):
        return None  # kernel gone

    engine._fetch_large_artifact = _fetch

    with caplog.at_level(logging.WARNING):
        count = await TurnEngine._persist_artifacts(  # type: ignore[arg-type]
            engine, agent, uuid.uuid4(), uuid.uuid4(), [_big()]
        )

    assert count == 0
    assert "big.bin" in caplog.text
    assert "could not be delivered" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_fetch_does_not_cost_the_other_artifacts(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """T-4. One unretrievable file must not take the whole batch with it. The
    turn's text response is already committed by this point; artifacts are a
    best-effort addition to it."""
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    png = base64.b64encode(b"\x89PNG").decode("ascii")

    async def _fetch(_runner, _agent, _room, _art):
        return None

    engine._fetch_large_artifact = _fetch
    artifacts = [
        _big(),
        {"filename": "ok.png", "mime": "image/png", "rel_path": "/session/outputs/ok.png", "b64": png},
    ]

    with caplog.at_level(logging.WARNING):
        count = await TurnEngine._persist_artifacts(  # type: ignore[arg-type]
            engine, agent, uuid.uuid4(), uuid.uuid4(), artifacts
        )

    assert count == 1
    svc = _FakeAttachmentService.instances[-1]
    assert [u["filename"] for u in svc.uploaded] == ["ok.png"]
    assert "big.bin" in caplog.text


@pytest.mark.asyncio
async def test_an_artifact_above_the_ceiling_is_never_fetched(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """T-3/AC-3. Past the ceiling the platform declines rather than pulling tens
    of MB through a tool round. The refusal is explicit, unlike the old drop."""
    from contexts.agents.application.runtime import turn_engine as te

    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    called: list[str] = []

    class _Runner:
        async def fetch_kernel_artifact(self, **_kwargs):
            called.append("fetched")
            raise AssertionError("must not reach the sandbox for an oversized artifact")

    huge = _big("huge.bin", size=te.MAX_ARTIFACT_BYTES + 1)

    with caplog.at_level(logging.WARNING):
        data = await TurnEngine._fetch_large_artifact(  # type: ignore[arg-type]
            engine, _Runner(), agent, uuid.uuid4(), huge
        )

    assert data is None
    assert called == []
    assert "huge.bin" in caplog.text


@pytest.mark.asyncio
async def test_a_raising_fetch_costs_one_artifact_not_the_batch(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """A malformed descriptor must not discard artifacts that decoded fine.

    Found by this fix's own test stubs: a TypeError inside the loop reached the
    method-level handler, which logged "artifact persistence failed" and returned
    0 -- losing every artifact in the turn, including the good ones.
    """
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    png = base64.b64encode(b"\x89PNG").decode("ascii")

    async def _fetch(*_args, **_kwargs):
        raise RuntimeError("docker daemon exploded")

    engine._fetch_large_artifact = _fetch
    artifacts = [
        _big(),
        {"filename": "ok.png", "mime": "image/png", "rel_path": "/session/outputs/ok.png", "b64": png},
    ]

    with caplog.at_level(logging.WARNING):
        count = await TurnEngine._persist_artifacts(  # type: ignore[arg-type]
            engine, agent, uuid.uuid4(), uuid.uuid4(), artifacts
        )

    assert count == 1
    assert [u["filename"] for u in _FakeAttachmentService.instances[-1].uploaded] == ["ok.png"]
    assert "big.bin" in caplog.text


@pytest.mark.asyncio
async def test_the_shortfall_count_excludes_deduped_duplicates(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """The denominator is candidates after dedup, not raw descriptors.

    `len(artifacts)` counts duplicates the dedup pass already discarded, so an
    operator reconciling "N of M" against what the user received would be given
    a total that never existed -- in the one log line added so a human could
    reconstruct what happened.
    """
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())

    async def _fetch(*_args, **_kwargs):
        return None

    engine._fetch_large_artifact = _fetch
    # Three descriptors, but two are the same file: two real candidates.
    artifacts = [_big(), _big(), _big("other.bin")]

    with caplog.at_level(logging.WARNING):
        await TurnEngine._persist_artifacts(  # type: ignore[arg-type]
            engine, agent, uuid.uuid4(), uuid.uuid4(), artifacts
        )

    assert "2 of 2 artifacts" in caplog.text


@pytest.mark.asyncio
async def test_a_hostile_size_costs_one_artifact_not_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`size_bytes` is agent-controlled, like every field of the descriptor.

    A bare `int()` on it raised past the per-artifact guards into the
    method-level handler, which returns 0 -- discarding artifacts that had
    decoded perfectly well. That is a milder rerun of the silent batch loss this
    task exists to end, so it is pinned here rather than left to inspection.
    """
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    png = base64.b64encode(b"\x89PNG").decode("ascii")
    artifacts = [
        {"filename": "hostile.bin", "rel_path": "/session/outputs/h.bin", "size_bytes": "enormous"},
        {"filename": "ok.png", "mime": "image/png", "rel_path": "/session/outputs/ok.png", "b64": png},
    ]

    async def _fetch(*_args, **_kwargs):
        return None

    engine._fetch_large_artifact = _fetch
    count = await TurnEngine._persist_artifacts(  # type: ignore[arg-type]
        engine, agent, uuid.uuid4(), uuid.uuid4(), artifacts
    )

    assert count == 1
    assert [u["filename"] for u in _FakeAttachmentService.instances[-1].uploaded] == ["ok.png"]


@pytest.mark.asyncio
async def test_hydrated_bytes_are_used_without_a_second_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifacts now arrive already fetched, off the kernel at exec time. The
    late fetch stays only as a fallback for descriptors that never passed
    through the tool, and must not re-fetch what is already in hand."""
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    calls: list[Any] = []

    async def _fetch(*args, **kwargs):
        calls.append(args)

    engine._fetch_large_artifact = _fetch
    art = _big()
    art["data"] = b"already here"

    count = await TurnEngine._persist_artifacts(  # type: ignore[arg-type]
        engine, agent, uuid.uuid4(), uuid.uuid4(), [art]
    )

    assert count == 1
    assert calls == []
    assert _FakeAttachmentService.instances[-1].uploaded[0]["data"] == b"already here"


@pytest.mark.asyncio
async def test_undecodable_b64_is_now_reported_too(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """The sibling loss path: it logged at debug, so a corrupt payload was
    nearly as quiet as the dropped one. Both now report at warning."""
    _FakeAttachmentService.instances.clear()
    monkeypatch.setattr(
        "contexts.conversation.application.attachment_service.AttachmentService",
        _FakeAttachmentService,
    )
    engine = _engine()
    agent = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    bad = {"filename": "corrupt.png", "mime": "image/png", "rel_path": "/x", "b64": "!!!not base64!!!"}

    with caplog.at_level(logging.WARNING):
        count = await TurnEngine._persist_artifacts(  # type: ignore[arg-type]
            engine, agent, uuid.uuid4(), uuid.uuid4(), [bad]
        )

    assert count == 0
    assert "corrupt.png" in caplog.text
