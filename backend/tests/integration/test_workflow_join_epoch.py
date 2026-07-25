"""Integration coverage for the join executor's Lua arrival script.

Q-5: no fake can characterize the atomicity this script relies on (SET NX,
SCARD, the claim/drain sequence all happening inside one EVAL), so these
tests run the real script against real Redis rather than mocking it.

The fix under test splits arrival tracking into two independent tracks --
"fan" (fan-in edges, keyed by the join's configured mode) and "pass"
(back-edges, always fire_threshold=1 per Q-8) -- each with its own epoch,
arrival SET, and one-shot latch. See
docs/tasks/2026-07-22-join-epoch-loop-reentry/spec.md section 7.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

pytestmark = pytest.mark.db


@pytest.fixture
async def join_redis() -> AsyncIterator[tuple[Redis, str]]:
    """A real Redis client plus a fresh run_id, scoped and cleaned up per test."""
    from app.config.settings import get_settings

    run_id = str(uuid.uuid4())
    client: Redis = Redis.from_url(get_settings().redis.dsn, decode_responses=True)
    try:
        yield client, run_id
    finally:
        keys = await client.keys(f"wf:join:{run_id}:*")
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def _arrive(
    client: Redis,
    run_id: str,
    node_id: str,
    track: str,
    branch_id: str,
    fire_threshold: int,
    total_branches: int,
    ttl_seconds: int = 86_400,
) -> tuple[int, bool]:
    from contexts.workflow.application.executors.join import _JOIN_ARRIVE_LUA

    raw = await client.eval(
        _JOIN_ARRIVE_LUA,
        0,
        run_id,
        node_id,
        track,
        branch_id,
        str(fire_threshold),
        str(total_branches),
        str(ttl_seconds),
    )
    return int(raw[0]), bool(int(raw[1]))


async def test_any_join_fires_on_every_loop_pass(join_redis: tuple[Redis, str]) -> None:
    client, run_id = join_redis
    node_id = "join1"

    _, is_finalizer = await _arrive(client, run_id, node_id, "fan", "e_entry", 1, 1)
    assert is_finalizer is True

    _, is_finalizer = await _arrive(client, run_id, node_id, "pass", "e_back", 1, 1)
    assert is_finalizer is True
    pass_epoch = await client.get(f"wf:join:{run_id}:{node_id}:pass:epoch")
    assert pass_epoch == "1"
    fan_epoch = await client.get(f"wf:join:{run_id}:{node_id}:fan:epoch")
    assert fan_epoch == "1"  # only 1 fan-in edge, so it drained on its own arrival

    # A third pass (second back-edge arrival) must fire again, not be
    # swallowed by a stale latch from the first pass.
    _, is_finalizer = await _arrive(client, run_id, node_id, "pass", "e_back", 1, 1)
    assert is_finalizer is True
    pass_epoch = await client.get(f"wf:join:{run_id}:{node_id}:pass:epoch")
    assert pass_epoch == "2"


async def test_any_fan_in_fires_once_and_drains(join_redis: tuple[Redis, str]) -> None:
    client, run_id = join_redis
    node_id = "join1"

    results = []
    for branch_id in ("e1", "e2", "e3"):
        _, is_finalizer = await _arrive(client, run_id, node_id, "fan", branch_id, 1, 3)
        results.append(is_finalizer)

    assert results == [True, False, False]
    assert await client.exists(f"wf:join:{run_id}:{node_id}:fan:0") == 0
    assert await client.exists(f"wf:join:{run_id}:{node_id}:fan:0:fired") == 0
    assert await client.get(f"wf:join:{run_id}:{node_id}:fan:epoch") == "1"


async def test_retried_branch_does_not_inflate_arrivals(join_redis: tuple[Redis, str]) -> None:
    client, run_id = join_redis
    node_id = "join1"

    arrivals, _ = await _arrive(client, run_id, node_id, "fan", "e1", 2, 3)
    assert arrivals == 1

    arrivals, _ = await _arrive(client, run_id, node_id, "fan", "e1", 2, 3)
    assert arrivals == 1


async def test_straggler_fan_in_suppressed_after_early_loop_pass(
    join_redis: tuple[Redis, str],
) -> None:
    """Direct regression test for Q-6: a fan-in straggler must not re-fire
    an `any` join, no matter how many loop passes ran in between."""
    client, run_id = join_redis
    node_id = "join1"

    # Branch A of a two-branch fan-in arrives and fires; the fan-in isn't
    # drained yet (only 1 of 2 fan-in branches seen).
    _, is_finalizer = await _arrive(client, run_id, node_id, "fan", "A", 1, 2)
    assert is_finalizer is True
    assert await client.get(f"wf:join:{run_id}:{node_id}:fan:epoch") is None

    # The loop body finishes fast and loops back before straggler B arrives.
    _, is_finalizer = await _arrive(client, run_id, node_id, "pass", "back", 1, 1)
    assert is_finalizer is True

    # The pass track's own drain must not touch the still-open fan track.
    assert await client.get(f"wf:join:{run_id}:{node_id}:fan:epoch") is None
    assert await client.get(f"wf:join:{run_id}:{node_id}:fan:0:fired") == "1"

    # Straggler B finally arrives -- it must be suppressed, not re-fire.
    _, is_finalizer = await _arrive(client, run_id, node_id, "fan", "B", 1, 2)
    assert is_finalizer is False
    assert await client.get(f"wf:join:{run_id}:{node_id}:fan:epoch") == "1"


async def test_multi_back_edge_each_fires_independently(join_redis: tuple[Redis, str]) -> None:
    """Contract test for Q-8's corrected design: called with the fixed
    total_branches=1 the executor now always passes for the pass track,
    every back-edge arrival fires and drains on its own -- distinct
    back-edges are distinct loop-completion signals, not duplicates of each
    other. The historical bug (total_branches computed as the count of
    distinct back-edges) lived entirely in the executor's Python argument
    computation, not in this script, so it is caught by
    test_multiple_back_edges_pass_total_is_fixed_at_one in
    tests/unit/test_workflow_executors.py, not here -- this test instead
    pins the script's behavior for the arguments the fixed executor sends."""
    client, run_id = join_redis
    node_id = "join1"

    _, is_finalizer = await _arrive(client, run_id, node_id, "pass", "loopA", 1, 1)
    assert is_finalizer is True
    assert await client.get(f"wf:join:{run_id}:{node_id}:pass:epoch") == "1"

    _, is_finalizer = await _arrive(client, run_id, node_id, "pass", "loopB", 1, 1)
    assert is_finalizer is True
    assert await client.get(f"wf:join:{run_id}:{node_id}:pass:epoch") == "2"


async def test_same_back_edge_retaken_does_not_stall_waiting_for_sibling(
    join_redis: tuple[Redis, str],
) -> None:
    """Contract test for the sequential-alternation case: a join fed by two
    back-edges (e.g. an if/else in the loop body) must not wait for the
    sibling that a given run may never take. Same caveat as the test above:
    the bug this guards against was in the executor's Python argument
    computation (see test_multiple_back_edges_pass_total_is_fixed_at_one),
    not in this script -- this test pins the script's behavior at
    total_branches=1 across repeated arrivals of the same edge."""
    client, run_id = join_redis
    node_id = "join1"

    for expected_epoch in ("1", "2", "3"):
        _, is_finalizer = await _arrive(client, run_id, node_id, "pass", "loopA", 1, 1)
        assert is_finalizer is True
        assert await client.get(f"wf:join:{run_id}:{node_id}:pass:epoch") == expected_epoch
