"""AC-3 — a turn that started and never finished gets resolved.

The turn's own cleanup covers both failure paths now, including cancellation,
so the only way a turn still strands is a SIGKILL: nothing in-process runs, and
`wakeup_agent` is `max_tries=1` so nothing re-runs the job either. That leaves
the room "thinking" forever with an `agent.turn_started` audit row that never
got its partner.

See docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C2).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.workers.tasks import turn_reaper
from contexts.agents.infrastructure.stranded_turns import (
    TURN_FAILED,
    TURN_FINISHED,
    TURN_STARTED,
    TurnEvent,
    stranded_from_events,
)

_T0 = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _ev(agent: uuid.UUID, room: uuid.UUID, action: str, offset_s: int) -> TurnEvent:
    return TurnEvent(
        agent_id=agent, chatroom_id=room, action=action, created_at=_T0 + timedelta(seconds=offset_s)
    )


def _reaped(agent: uuid.UUID, room: uuid.UUID, *, at_s: int, resolved_offset_s: int) -> TurnEvent:
    """A finish row this sweep wrote, naming the start it resolved."""
    return TurnEvent(
        agent_id=agent,
        chatroom_id=room,
        action=TURN_FAILED,
        created_at=_T0 + timedelta(seconds=at_s),
        reaped_started_at=_T0 + timedelta(seconds=resolved_offset_s),
    )


class TestPairingStartsWithFinishes:
    def test_a_start_with_no_finish_is_stranded(self) -> None:
        a, r = uuid.uuid4(), uuid.uuid4()

        out = stranded_from_events([_ev(a, r, TURN_STARTED, 0)], deadline=_T0 + timedelta(seconds=10))

        assert [(s.agent_id, s.chatroom_id) for s in out] == [(a, r)]

    def test_a_finished_turn_is_not_stranded(self) -> None:
        a, r = uuid.uuid4(), uuid.uuid4()
        events = [_ev(a, r, TURN_STARTED, 0), _ev(a, r, TURN_FINISHED, 5)]

        assert stranded_from_events(events, deadline=_T0 + timedelta(seconds=10)) == []

    def test_a_failed_turn_is_not_stranded(self) -> None:
        a, r = uuid.uuid4(), uuid.uuid4()
        events = [_ev(a, r, TURN_STARTED, 0), _ev(a, r, TURN_FAILED, 5)]

        assert stranded_from_events(events, deadline=_T0 + timedelta(seconds=10)) == []

    def test_a_start_still_inside_its_budget_is_left_alone(self) -> None:
        """The reaper must never resolve a turn that may still be running."""
        a, r = uuid.uuid4(), uuid.uuid4()

        out = stranded_from_events([_ev(a, r, TURN_STARTED, 100)], deadline=_T0 + timedelta(seconds=10))

        assert out == []

    def test_a_stranded_turn_followed_by_a_healthy_one_is_still_found(self) -> None:
        """The case a naive 'is there any later finish' check gets wrong: the
        second turn's finish row also sits after the first turn's start."""
        a, r = uuid.uuid4(), uuid.uuid4()
        events = [
            _ev(a, r, TURN_STARTED, 0),  # stranded
            _ev(a, r, TURN_STARTED, 20),
            _ev(a, r, TURN_FINISHED, 25),
        ]

        out = stranded_from_events(events, deadline=_T0 + timedelta(seconds=30))

        assert [s.started_at for s in out] == [_T0]

    def test_an_already_reaped_turn_is_not_reaped_again(self) -> None:
        """The sweep is idempotent only if it recognises its own work.

        Its finish rows are stamped `now`, so they land after every start in the
        window and can never sit between the two starts the pairing rule reads
        as evidence. Replays sweep 2 over sweep 1's output: two stranded starts,
        then the two `agent.turn_failed` rows the reaper wrote for them.
        """
        a, r = uuid.uuid4(), uuid.uuid4()
        events = [
            _ev(a, r, TURN_STARTED, 0),
            _ev(a, r, TURN_STARTED, 60),
            _reaped(a, r, at_s=600, resolved_offset_s=0),
            _reaped(a, r, at_s=600, resolved_offset_s=60),
        ]

        out = stranded_from_events(events, deadline=_T0 + timedelta(seconds=300))

        assert out == []

    def test_a_reap_row_resolves_only_the_start_it_names(self) -> None:
        """The older start stays stranded when only the newer one was reaped —
        the reap row must not be readable as a blanket 'this key is clean'."""
        a, r = uuid.uuid4(), uuid.uuid4()
        events = [
            _ev(a, r, TURN_STARTED, 0),
            _ev(a, r, TURN_STARTED, 60),
            _reaped(a, r, at_s=600, resolved_offset_s=60),
        ]

        out = stranded_from_events(events, deadline=_T0 + timedelta(seconds=300))

        assert [s.started_at for s in out] == [_T0]

    def test_pairs_are_independent_across_agents_and_rooms(self) -> None:
        a1, a2, r1, r2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        events = [
            _ev(a1, r1, TURN_STARTED, 0),
            _ev(a2, r1, TURN_STARTED, 1),
            _ev(a1, r2, TURN_STARTED, 2),
            _ev(a2, r1, TURN_FINISHED, 3),  # only this pair is resolved
        ]

        out = stranded_from_events(events, deadline=_T0 + timedelta(seconds=30))

        assert {(s.agent_id, s.chatroom_id) for s in out} == {(a1, r1), (a1, r2)}


def test_the_sweep_query_is_bounded_and_room_scoped() -> None:
    """Compiles the real statement, so a column or JSON-path typo fails here
    rather than in a cron log a minute after deploy.

    The room filter is what keeps the headless A2A path out of the sweep — it
    audits its start with no `chatroom_id`, has no room channel to notify and
    no coalesced trigger to drain.
    """
    from sqlalchemy.dialects import postgresql

    from contexts.agents.infrastructure.stranded_turns import turn_events_query

    stmt = turn_events_query(horizon=_T0, cap=500)
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "audit_logs" in sql
    assert "'chatroom_id'" in sql
    assert "IS NOT NULL" in sql
    assert "resource_type" in sql
    for action in (TURN_STARTED, TURN_FINISHED, TURN_FAILED):
        assert action in sql
    # cap + 1, so the caller can tell a full read from a truncated one.
    assert "LIMIT 501" in sql


class _FakeDB:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> _FakeDB:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _wire(
    monkeypatch: pytest.MonkeyPatch, events: list[TurnEvent], *, truncated: bool = False
) -> dict[str, Any]:
    seen: dict[str, Any] = {"audits": [], "emits": [], "drains": [], "db": _FakeDB()}

    monkeypatch.setattr("shared_kernel.db.session.async_session", lambda: seen["db"])

    class _Repo:
        def __init__(self, db: object) -> None:
            pass

        async def list_turn_events(self, *, horizon: object, cap: int) -> tuple[list[TurnEvent], bool]:
            return events, truncated

    monkeypatch.setattr("contexts.agents.infrastructure.stranded_turns.StrandedTurnRepository", _Repo)

    async def _emit(db: object, event: Any) -> bool:
        seen["audits"].append((event.action, event.resource_id, event.metadata))
        return True

    monkeypatch.setattr("shared_kernel.audit.emit", _emit)

    async def _finished(room: uuid.UUID, agent: uuid.UUID, reason: str) -> None:
        seen["emits"].append((room, agent, reason))

    monkeypatch.setattr("contexts.conversation.interfaces.emit_agent_finished_error", _finished)

    async def _drain(agent: uuid.UUID, room: uuid.UUID) -> None:
        seen["drains"].append((agent, room))

    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.drain_queued_trigger", _drain)
    return seen


class TestTheReaperResolvesAStrandedTurn:
    async def test_it_audits_notifies_and_drains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = uuid.uuid4(), uuid.uuid4()
        old = datetime.now(UTC) - timedelta(seconds=turn_reaper.STRANDED_TURN_BUDGET_S + 60)
        seen = _wire(
            monkeypatch,
            [TurnEvent(agent_id=agent, chatroom_id=room, action=TURN_STARTED, created_at=old)],
        )

        result = await turn_reaper.agent_turn_reaper({})

        assert result == "reaped=1"
        action, resource_id, meta = seen["audits"][0]
        assert (action, resource_id) == (TURN_FAILED, agent)
        assert meta["error"] == "stranded"
        assert meta["reaped"] is True
        assert meta["chatroom_id"] == str(room)
        assert seen["db"].commits == 1
        assert seen["emits"] == [(room, agent, "stranded")]
        assert seen["drains"] == [(agent, room)]

    async def test_a_turn_inside_its_budget_is_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = uuid.uuid4(), uuid.uuid4()
        recent = datetime.now(UTC) - timedelta(seconds=5)
        seen = _wire(
            monkeypatch,
            [TurnEvent(agent_id=agent, chatroom_id=room, action=TURN_STARTED, created_at=recent)],
        )

        assert await turn_reaper.agent_turn_reaper({}) == "reaped=0"
        assert seen["audits"] == []
        assert seen["emits"] == []

    async def test_one_unrecordable_turn_does_not_cost_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first, second, room = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        old = datetime.now(UTC) - timedelta(seconds=turn_reaper.STRANDED_TURN_BUDGET_S + 60)
        seen = _wire(
            monkeypatch,
            [
                TurnEvent(agent_id=first, chatroom_id=room, action=TURN_STARTED, created_at=old),
                TurnEvent(
                    agent_id=second,
                    chatroom_id=room,
                    action=TURN_STARTED,
                    created_at=old + timedelta(seconds=1),
                ),
            ],
        )

        async def _emit(db: object, event: Any) -> bool:
            if event.resource_id == first:
                raise RuntimeError("insert failed")
            seen["audits"].append((event.action, event.resource_id, event.metadata))
            return True

        monkeypatch.setattr("shared_kernel.audit.emit", _emit)

        assert await turn_reaper.agent_turn_reaper({}) == "reaped=1"
        assert [a[1] for a in seen["audits"]] == [second]
        assert seen["db"].rollbacks == 1
        assert seen["emits"] == [(room, second, "stranded")]


class TestTheReaperIsWired:
    def test_registered_as_a_per_minute_cron(self) -> None:
        from app.workers.main import WorkerSettings

        assert turn_reaper.agent_turn_reaper in WorkerSettings.functions
        jobs = [c for c in WorkerSettings.cron_jobs if c.coroutine is turn_reaper.agent_turn_reaper]
        assert len(jobs) == 1
        assert jobs[0].minute == set(range(60))

    def test_the_budget_outlasts_the_scoped_job_timeout(self) -> None:
        """A turn must be impossible to still be running before it is reaped."""
        from app.workers.tasks.orchestration import WAKEUP_TURN_TIMEOUT_S

        assert turn_reaper.STRANDED_TURN_BUDGET_S > WAKEUP_TURN_TIMEOUT_S
        assert turn_reaper.STRANDED_TURN_HORIZON_S > turn_reaper.STRANDED_TURN_BUDGET_S
