"""A turn's outcome must come from its durable result, not its last failed step.

The reply commits, and then six things happen: artifacts persist, two events
publish, two dispatches fan out, and the approval ballots settle. All six were
inside the `try` whose `except` decides the turn's outcome, so any one of them
raising rewrote a committed, fully successful turn as `failed` — auditing
`agent.turn_failed` next to the `agent.turn_finished` it had already written,
requeueing notifications the agent had already consumed, and skipping the
downstream dispatches that were the point of the turn (F-6).

`redis.publish` is the reachable trigger: `shared_kernel/realtime/pubsub.py`
propagates whatever it raises and the client retries on timeout only, so a
`ConnectionError`, `MISCONF` or `OOM command not allowed` lands here.

Every committed *skip* owes the same guarantee, which is why S-1 (`empty_reply`)
and the `knowledge_starved` branch are pinned alongside the reply path.

See docs/tasks/2026-07-22-turn-outcome-reporting/spec.md (C1), R1, [R13.27].
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import redis.exceptions

import contexts.agents.application.runtime.turn_engine as te
from contexts.agents.application.runtime.tool_registry import ToolResult
from contexts.keys.application.provider_router import (
    ProviderCallResult,
    StreamComplete,
    TokenDelta,
)
from contexts.keys.domain.providers import ApiKeyProvider
from tests.unit.turn_engine_fakes import PublisherSpy, Trace, make_agent, run_locked, wire_engine

_NOTE = {"kind": "notify", "body": "unseen"}


async def _none(*a: Any, **k: Any) -> None:
    return None


class _ToolThenTextRouter:
    """One tool round that narrates before calling, then the real answer.

    Mirrors `test_agent_turn_loop.py`'s fake: the point is that "think" reaches
    the room and is then superseded by "done", which is the only text persisted.
    """

    def __init__(self) -> None:
        self.rounds = 0

    async def call_stream(self, *, group_id: Any, request: Any) -> Any:
        self.rounds += 1
        if self.rounds == 1:
            yield TokenDelta("think")
            yield StreamComplete(
                ProviderCallResult(
                    200,
                    {
                        "text": "",
                        "tool_calls": [{"id": "t1", "name": "noop", "arguments": {"a": 1}}],
                        "finish_reason": "tool_use",
                    },
                )
            )
        else:
            yield TokenDelta("done")
            yield StreamComplete(
                ProviderCallResult(200, {"text": "done", "tool_calls": [], "finish_reason": "end"})
            )


class _OneToolRegistry:
    def specs(self) -> list[dict[str, Any]]:
        return [{"name": "noop", "description": "d", "input_schema": {}}]

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(content='{"ok": true}')


def _replies(text: str = "the answer"):
    async def _stream(**kw: Any) -> te.ToolLoopOutcome:
        return te.ToolLoopOutcome(text=text, rounds=0)

    return _stream


def _publish_fails(event: str) -> None:
    """Make the room publish for one event type raise the way Redis does."""
    PublisherSpy.fail_on = event
    PublisherSpy.error = redis.exceptions.ConnectionError("redis is gone")


def _wire(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Trace, SimpleNamespace, uuid.UUID]:
    agent, room = make_agent(), uuid.uuid4()
    engine, trace = wire_engine(monkeypatch, agent, note=_NOTE)
    engine._stream_with_tools = _replies()
    return engine, trace, agent, room


def _starved(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Trace, SimpleNamespace, uuid.UUID]:
    """A turn whose knowledge budget floors at zero, with its emit failing.

    `compact` mode is what makes `context_token_cap` load-bearing: in `general`
    mode `_request_ceiling` returns the provider hard limit and ignores the cap
    entirely, so a low cap starves nothing. And this branch reports through
    `emit_agent_finished_error`, not `Publisher`, so that is what has to fail.
    """
    engine, trace, agent, room = _wire(monkeypatch)
    agent.context_mode = SimpleNamespace(value="compact")
    agent.context_token_cap = 1

    async def _has_source(*a: Any, **k: Any) -> bool:
        return True

    engine._has_knowledge_source = _has_source

    async def _emit_fails(*a: Any, **k: Any) -> None:
        raise redis.exceptions.ConnectionError("redis is gone")

    monkeypatch.setattr(te, "emit_agent_finished_error", _emit_fails)
    return engine, trace, agent, room


class TestACommittedReplyReportsCompleted:
    """AC-1, AC-2. Each of these returns `failed` before C1."""

    async def test_a_failed_message_created_publish_does_not_fail_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, trace, agent, room = _wire(monkeypatch)
        _publish_fails("message.created")

        result = await run_locked(engine, room, agent)

        assert result.status == "completed"
        assert result.message_id is not None
        assert trace.audited("agent.turn_failed") == []
        assert len(trace.audited("agent.turn_finished")) == 1

    async def test_a_failed_agent_finished_publish_does_not_fail_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, trace, agent, room = _wire(monkeypatch)
        _publish_fails("agent.finished")

        result = await run_locked(engine, room, agent)

        assert result.status == "completed"
        assert trace.audited("agent.turn_failed") == []

    async def test_a_failed_publish_still_runs_both_downstream_dispatches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dispatches are the turn's whole point for the rest of the system:
        R15.01 every_n counting, silence timers, R11.02 GraphRAG triggers and
        K.4 workflow `message` waits all hang off them. Losing them to a Redis
        blip is the durable half of F-6's damage."""
        engine, trace, agent, room = _wire(monkeypatch)
        _publish_fails("message.created")

        result = await run_locked(engine, room, agent)

        assert trace.message_signals == ["the answer"]
        assert trace.reply_wakeups == [result.message_id]

    async def test_a_failed_publish_does_not_requeue_consumed_notifications(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3. The agent saw these notes and answered on them — the reply is
        committed. Requeueing re-offers them to the next turn as if unread."""
        engine, trace, agent, room = _wire(monkeypatch)
        _publish_fails("message.created")

        await run_locked(engine, room, agent)

        assert trace.requeued == []
        assert trace.compact_restored == []

    async def test_a_failed_artifact_persist_does_not_fail_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, trace, agent, room = _wire(monkeypatch)

        async def _boom(*a: Any, **k: Any) -> int:
            raise RuntimeError("minio down")

        engine._persist_artifacts = _boom

        result = await run_locked(engine, room, agent)

        assert result.status == "completed"
        assert trace.audited("agent.turn_failed") == []

    async def test_a_failed_dispatch_does_not_change_the_outcome_or_stop_the_next(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each post-commit step fails independently: the signal raising must
        not take the reply wakeups down with it."""
        engine, trace, agent, room = _wire(monkeypatch)

        async def _boom(*a: Any, **k: Any) -> None:
            raise RuntimeError("workflow signal exploded")

        engine._dispatch_agent_message_signal = _boom

        result = await run_locked(engine, room, agent)

        assert result.status == "completed"
        assert trace.reply_wakeups == [result.message_id]
        assert trace.settled == 1
        assert trace.audited("agent.turn_failed") == []

    async def test_a_failed_settle_does_not_change_the_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine, trace, agent, room = _wire(monkeypatch)

        async def _boom(*a: Any, **k: Any) -> None:
            raise RuntimeError("orchestration unreachable")

        engine._settle_pending_approvals = _boom

        result = await run_locked(engine, room, agent)

        assert result.status == "completed"
        assert trace.requeued == []


class TestACommittedSkipReportsSkipped:
    """S-1 and the `knowledge_starved` sibling: the guarantee is the commit, not
    the reply. Both report `failed` before C1."""

    async def test_empty_reply_stays_skipped_when_its_publish_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, trace, agent, room = _wire(monkeypatch)
        engine._stream_with_tools = _replies("   ")
        _publish_fails("agent.finished")

        result = await run_locked(engine, room, agent)

        assert result.status == "skipped"
        assert result.reason == "empty_reply"
        assert trace.audited("agent.turn_failed") == []
        assert trace.requeued == []

    async def test_knowledge_starved_stays_skipped_when_its_publish_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This branch commits deliberately — `_assemble_history` may have spent
        a real summarisation call on the user's own key — so its emit is
        post-commit and carries F-6's shape too. Not in the spec's sibling list;
        found re-verifying it (D-5)."""
        engine, trace, agent, room = _starved(monkeypatch)

        result = await run_locked(engine, room, agent)

        assert result.status == "skipped"
        assert result.reason == "knowledge_starved"
        assert trace.audited("agent.turn_failed") == []

    async def test_knowledge_starved_still_restores_its_notifications(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unlike the reply path, this skip never reached the provider, so the
        requeue is the branch's own correct behaviour — not failure cleanup —
        and guarding the emit must not cost it."""
        engine, trace, agent, room = _starved(monkeypatch)

        await run_locked(engine, room, agent)

        assert trace.requeued == [([_NOTE], set())]


class TestTheTurnReportsItsLiveness:
    """C3/F-15. The window between `agent.thinking` and the first token is the
    engine's only long silence, and the client reads silence as a wedged turn."""

    async def test_the_assembly_window_emits_progress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine, _, agent, room = _wire(monkeypatch)

        await run_locked(engine, room, agent)

        phases = [p["phase"] for p in PublisherSpy.events("agent.progress")]
        assert phases == ["context", "skills", "workspace", "history"]

    async def test_progress_carries_ids_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The room channel admits a chatroom guest who is not a project member,
        so a phase beacon must not become a content side-channel."""
        engine, _, agent, room = _wire(monkeypatch)

        await run_locked(engine, room, agent)

        for payload in PublisherSpy.events("agent.progress"):
            assert set(payload) == {"agent_id", "phase"}
            assert payload["agent_id"] == str(agent.id)

    async def test_progress_precedes_the_first_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ordering is the whole point: a beacon that arrived after the stream
        started would re-arm a watchdog the tokens already re-armed."""
        engine, _, agent, room = _wire(monkeypatch)

        await run_locked(engine, room, agent)

        order = [e for _, e, _ in PublisherSpy.emitted]
        assert order[0] == "agent.thinking"
        assert order.index("agent.progress") < order.index("agent.finished")

    async def test_a_failed_progress_emit_never_fails_the_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine, trace, agent, room = _wire(monkeypatch)
        _publish_fails("agent.progress")

        result = await run_locked(engine, room, agent)

        assert result.status == "completed"
        assert trace.audited("agent.turn_failed") == []

    async def test_an_observer_turn_emits_no_progress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """R28.01: an observer turn has no room channel at all, and every emit on
        that path is guarded on it. A new one must fail closed like the rest."""
        from contexts.conversation.domain.models import ChatroomAgentRole

        agent, room = make_agent(), uuid.uuid4()
        engine, _ = wire_engine(monkeypatch, agent, note=_NOTE, role=ChatroomAgentRole.OBSERVER)
        engine._stream_with_tools = _replies()
        engine._observer_memory_block = _none
        engine._emit_observation_event = _none

        async def _record(**kw: Any) -> SimpleNamespace:
            return SimpleNamespace(id=uuid.uuid4(), created_at=None)

        monkeypatch.setattr(te, "ObservationService", lambda db: SimpleNamespace(record=_record))

        await run_locked(engine, room, agent)

        assert PublisherSpy.events("agent.progress") == []


class TestAToolRoundResetsTheStreamedDraft:
    """C4/F-40. Round-1's preamble is streamed, then discarded when only the
    final round's text is persisted — the client needs the boundary to reset on."""

    async def test_a_round_boundary_is_announced_between_the_rounds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, _, agent, room = _wire(monkeypatch)
        # `_wire` stubs the loop out; this test is about the real one.
        del engine._stream_with_tools

        events: list[tuple[str, dict]] = []

        class _Pub:
            def __init__(self, channel: str) -> None:
                pass

            async def emit(self, etype: str, data: dict) -> None:
                events.append((etype, data))

        monkeypatch.setattr(te, "Publisher", _Pub)
        engine._router = _ToolThenTextRouter()

        outcome = await engine._stream_with_tools(
            agent=agent,
            chatroom_id=room,
            parent_agent_id=None,
            system_text="sys",
            messages=[{"role": "user", "content": "go"}],
            provider=ApiKeyProvider.CLAUDE,
            model="m",
            registry=_OneToolRegistry(),
            room="room",
        )

        assert outcome.text == "done"
        kinds = [(e, d.get("text") or d.get("phase")) for e, d in events]
        assert kinds == [
            ("agent.token", "think"),
            ("agent.progress", "tool_round"),
            ("agent.token", "done"),
        ]

    async def test_a_headless_round_boundary_emits_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`room=None` is the A2A/observer path; the boundary must stay silent
        there like every other emit in this loop."""
        engine, _, agent, room = _wire(monkeypatch)
        del engine._stream_with_tools

        events: list[str] = []

        class _Pub:
            def __init__(self, channel: str) -> None:
                pass

            async def emit(self, etype: str, data: dict) -> None:
                events.append(etype)

        monkeypatch.setattr(te, "Publisher", _Pub)
        engine._router = _ToolThenTextRouter()

        await engine._stream_with_tools(
            agent=agent,
            chatroom_id=room,
            parent_agent_id=None,
            system_text="sys",
            messages=[{"role": "user", "content": "go"}],
            provider=ApiKeyProvider.CLAUDE,
            model="m",
            registry=_OneToolRegistry(),
            room=None,
        )

        assert events == []


class TestAnUncommittedFailureStillFails:
    """The correction ends the failure scope at the commit — it does not widen
    the success path. S-2: `no_input` emits *before* its commit, so nothing
    durable exists and routing to the failure path stays correct."""

    async def test_no_input_publish_failure_still_fails_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-2 exactly: the `no_input` branch emits at its start and commits
        four lines later, so a publish failure there predates anything durable.
        Reporting `failed` — and owing the drained notes back — is right."""
        engine, trace, agent, room = _wire(monkeypatch)

        async def _no_history(*a: Any, **k: Any) -> list[Any]:
            return []

        engine._assemble_history = _no_history
        _publish_fails("agent.finished")

        result = await run_locked(engine, room, agent)

        assert result.status == "failed"
        assert len(trace.audited("agent.turn_failed")) == 1
        assert trace.audited("agent.turn_finished") == []
        # The agent never acted on them — the failure path owes these back.
        assert trace.requeued == [([_NOTE], set())]

    async def test_a_publish_failure_before_the_drain_still_fails_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, trace, agent, room = _wire(monkeypatch)
        _publish_fails("agent.thinking")

        result = await run_locked(engine, room, agent)

        assert result.status == "failed"
        assert len(trace.audited("agent.turn_failed")) == 1
        assert trace.audited("agent.turn_finished") == []
        # Nothing had been drained this early, so there is nothing to give back.
        assert trace.requeued == [([], set())]

    async def test_a_provider_failure_still_fails_the_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine, trace, agent, room = _wire(monkeypatch)

        async def _boom(**kw: Any) -> te.ToolLoopOutcome:
            raise RuntimeError("provider exploded")

        engine._stream_with_tools = _boom

        result = await run_locked(engine, room, agent)

        assert result.status == "failed"
        assert len(trace.audited("agent.turn_failed")) == 1
        assert trace.compact_restored == [room]
