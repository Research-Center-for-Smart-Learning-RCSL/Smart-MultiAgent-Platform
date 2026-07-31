"""A turn must clean up after itself when its job is killed (F-8, F-18).

`job_timeout` cancels the task, and `CancelledError` inherits `BaseException`,
so neither `_run_locked`'s `except Exception` nor `run_turn`'s bare post-loop
drain runs today: the room stays "thinking", no `agent.turn_failed` is audited,
drained notifications are lost and the coalesced trigger is stranded for its
full hour of TTL.

See docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C1, C3).
"""

from __future__ import annotations

from arq.worker import Function


def _entry(name: str) -> object:
    from app.workers.main import WorkerSettings

    for fn in WorkerSettings.functions:
        if (getattr(fn, "name", None) or fn.__name__) == name:
            return fn
    raise AssertionError(f"{name} is not registered on WorkerSettings")


def test_wakeup_agent_is_registered_without_retries() -> None:
    """AC-4: a turn is not retry-safe, so its job must never be re-run."""
    entry = _entry("wakeup_agent")
    assert isinstance(entry, Function), "wakeup_agent must be registered via arq's func(...)"
    assert entry.max_tries == 1


def test_wakeup_agent_has_a_scoped_timeout_with_lock_headroom() -> None:
    """AC-4: the timeout is scoped to this lane and sized against the lock TTL.

    The relation is headroom, not tightness (Q-9): the heartbeat-refreshed,
    fail-closed turn lock is the single-writer authority, and one provider
    stream read alone may take as long as the TTL.
    """
    from app.workers.tasks.orchestration import WAKEUP_TURN_TIMEOUT_S
    from contexts.agents.infrastructure.turn_lock import DEFAULT_TURN_TTL_S

    entry = _entry("wakeup_agent")
    assert isinstance(entry, Function)
    assert entry.timeout_s == WAKEUP_TURN_TIMEOUT_S
    assert WAKEUP_TURN_TIMEOUT_S > DEFAULT_TURN_TTL_S
