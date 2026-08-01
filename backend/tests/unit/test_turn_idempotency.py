"""AC-8 — a replayed turn job produces no second reply, and spends nothing.

A turn commits its reply with post-commit work still to run, and its lock is
released by the cancellation unwind, so a re-run could re-assemble a history
that already contained the reply and post a second one. `max_tries=1` closes
the arq half; this is the half that also covers a turn which ran twice because
its lock lapsed under it, which no retry guard can see.

The durable backstop is a partial unique index (migration 0072), exercised
against real Postgres in `tests/integration/test_turn_job_idempotency.py`. What
is unit-testable is the wiring: the key reaches the reply row, and the
short-circuit fires before any provider call.

See docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C6), R1.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import contexts.agents.application.runtime.turn_engine as te
from contexts.conversation.domain.models import ChatroomAgentRole


class _FakeDB:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


class TestTheKeyReachesTheReplyRow:
    async def test_send_agent_stores_the_turn_job_id_in_metadata(self) -> None:
        from contexts.conversation.application.message_service import MessageService

        created: dict[str, Any] = {}

        class _Repo:
            async def create(self, **kw: Any) -> SimpleNamespace:
                created.update(kw)
                return SimpleNamespace(id=uuid.uuid4(), created_at=None)

        svc = MessageService.__new__(MessageService)
        svc._db = _FakeDB()  # type: ignore[attr-defined]
        svc._messages = _Repo()  # type: ignore[attr-defined]

        async def _emit(db: object, event: object) -> bool:
            return True

        import shared_kernel.audit as audit_mod

        original, audit_mod.emit = audit_mod.emit, _emit  # type: ignore[assignment]
        try:
            await svc.send_agent(
                chatroom_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                content_md="hi",
                turn_job_id="job-abc",
            )
        finally:
            audit_mod.emit = original  # type: ignore[assignment]

        assert created["metadata"]["turn_job_id"] == "job-abc"

    async def test_no_key_leaves_the_metadata_untouched(self) -> None:
        """Every pre-0072 row, and every reply from a caller with no job id, must
        stay outside the partial unique index's predicate."""
        from contexts.conversation.application.message_service import MessageService

        created: dict[str, Any] = {}

        class _Repo:
            async def create(self, **kw: Any) -> SimpleNamespace:
                created.update(kw)
                return SimpleNamespace(id=uuid.uuid4(), created_at=None)

        svc = MessageService.__new__(MessageService)
        svc._db = _FakeDB()  # type: ignore[attr-defined]
        svc._messages = _Repo()  # type: ignore[attr-defined]

        async def _emit(db: object, event: object) -> bool:
            return True

        import shared_kernel.audit as audit_mod

        original, audit_mod.emit = audit_mod.emit, _emit  # type: ignore[assignment]
        try:
            await svc.send_agent(chatroom_id=uuid.uuid4(), agent_id=uuid.uuid4(), content_md="hi")
        finally:
            audit_mod.emit = original  # type: ignore[assignment]

        assert "turn_job_id" not in created["metadata"]


def _wire_precheck(
    monkeypatch: pytest.MonkeyPatch, *, existing: uuid.UUID | None
) -> tuple[Any, SimpleNamespace, list[tuple[str, dict]]]:
    """`_run_locked` wired only as far as the replay short-circuit.

    Nothing past it is stubbed on purpose: if the short-circuit stops firing,
    the test fails on a missing fake rather than passing quietly.
    """
    agent = SimpleNamespace(id=uuid.uuid4(), key_group_id=uuid.uuid4(), project_id=uuid.uuid4())
    audits: list[tuple[str, dict]] = []

    class _AgentsFacade:
        def __init__(self, db: object) -> None:
            pass

        async def get_agent(self, aid: uuid.UUID) -> SimpleNamespace:
            return agent

    monkeypatch.setattr(te, "AgentsFacade", _AgentsFacade)

    class _BindingRepo:
        def __init__(self, db: object) -> None:
            pass

        async def role_of(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> ChatroomAgentRole:
            return ChatroomAgentRole.NORMAL

    monkeypatch.setattr(te, "ChatroomAgentRepository", _BindingRepo)

    class _MessageRepo:
        def __init__(self, db: object) -> None:
            pass

        async def id_for_turn_job(self, turn_job_id: str) -> uuid.UUID | None:
            return existing

    monkeypatch.setattr(te, "MessageRepository", _MessageRepo)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]

    async def _audit(agent_: object, room: object, action: str, extra: dict) -> None:
        audits.append((action, extra))

    engine._audit = _audit  # type: ignore[attr-defined]
    return engine, agent, audits


class TestTheReplayShortCircuit:
    async def test_a_replayed_job_does_not_run_the_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        already = uuid.uuid4()
        engine, agent, audits = _wire_precheck(monkeypatch, existing=already)

        result = await engine._run_locked(
            agent_id=agent.id,
            chatroom_id=uuid.uuid4(),
            trigger="mention",
            parent_agent_id=None,
            input_text=None,
            request_id=None,
            turn_job_id="job-abc",
        )

        assert result.status == "skipped"
        assert result.reason == "duplicate_job"
        # The caller learns which reply already exists, rather than being told
        # the turn produced nothing.
        assert result.message_id == already
        assert audits == [("agent.turn_skipped", {"reason": "duplicate_job"})]

    async def test_the_short_circuit_is_recorded_durably(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine, agent, _ = _wire_precheck(monkeypatch, existing=uuid.uuid4())

        await engine._run_locked(
            agent_id=agent.id,
            chatroom_id=uuid.uuid4(),
            trigger="mention",
            parent_agent_id=None,
            input_text=None,
            request_id=None,
            turn_job_id="job-abc",
        )

        assert engine._db.commits == 1

    async def test_a_turn_with_no_key_never_looks_it_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One extra indexed query per turn is cheap, but zero is cheaper — and
        a caller with no job id has nothing to look up."""
        engine, agent, _ = _wire_precheck(monkeypatch, existing=uuid.uuid4())
        looked_up = []

        class _Boom:
            def __init__(self, db: object) -> None:
                pass

            async def id_for_turn_job(self, turn_job_id: str) -> uuid.UUID | None:
                looked_up.append(turn_job_id)
                return None

        monkeypatch.setattr(te, "MessageRepository", _Boom)

        async def _out_of_scope(agent_: object) -> bool:
            raise AssertionError("reached: the turn continued past the pre-check")

        engine._key_group_out_of_scope = _out_of_scope  # type: ignore[attr-defined]

        with pytest.raises(AssertionError, match="reached"):
            await engine._run_locked(
                agent_id=agent.id,
                chatroom_id=uuid.uuid4(),
                trigger="mention",
                parent_agent_id=None,
                input_text=None,
                request_id=None,
                turn_job_id=None,
            )

        assert looked_up == []
