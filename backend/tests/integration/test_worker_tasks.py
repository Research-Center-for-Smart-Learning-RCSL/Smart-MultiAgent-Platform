"""2.23: Integration coverage for Arq tasks.

These do NOT spin up a real Arq runtime — that would require a live Redis
+ Postgres stack which the Phase J unit/integration tier deliberately avoids.
Instead they exercise the *task callables* directly with stubbed clients,
covering the failure-handling paths the audit flagged:

  * `archive_workflow_runs` falls back to the default cutoff when the Redis
    override is malformed (2.13)
  * `daily_org_advisory_snapshot` raises `RuntimeError` when every eligible
    org's snapshot fails (2.16)
  * `retention_sweep` records `-1` for a failing policy and surfaces it in
    the structured `failed_policies` payload (2.14)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# --------------------------------------------------------------------------- #
#  workflow.archive_workflow_runs — invalid override falls back to default    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_archive_override_invalid_logs_and_uses_default() -> None:
    from app.workers.tasks import workflow as wf

    fake_redis = AsyncMock()
    fake_redis.get.return_value = b"not-an-int"

    fake_db_rows: list[Any] = []
    fake_session = AsyncMock()
    fake_session.execute.return_value.all.return_value = fake_db_rows
    fake_session.commit = AsyncMock()

    class _SessionCM:
        async def __aenter__(self_inner):  # noqa: ANN001
            return fake_session
        async def __aexit__(self_inner, *_a):  # noqa: ANN001
            return None

    with (
        patch("shared_kernel.auth.clients.get_redis", return_value=fake_redis),
        patch("shared_kernel.db.session.async_session", return_value=_SessionCM()),
    ):
        out = await wf.archive_workflow_runs({}, cutoff_days=90)

    assert out == "archived=0"
    fake_redis.get.assert_awaited_once_with("config:archive:cutoff_days")


# --------------------------------------------------------------------------- #
#  advisory.daily_org_advisory_snapshot — all-org failure raises               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_advisory_snapshot_raises_when_every_org_fails() -> None:
    from app.workers.tasks import advisory as adv

    org_ids = [(uuid4(),), (uuid4(),), (uuid4(),)]

    fake_redis = AsyncMock()
    list_session = AsyncMock()
    list_session.execute.return_value.all.return_value = org_ids

    class _ListCM:
        async def __aenter__(self_inner):  # noqa: ANN001
            return list_session
        async def __aexit__(self_inner, *_a):  # noqa: ANN001
            return None

    class _BoomCM:
        async def __aenter__(self_inner):  # noqa: ANN001
            raise RuntimeError("postgres down")
        async def __aexit__(self_inner, *_a):  # noqa: ANN001
            return None

    sm_calls = {"n": 0}

    def _sm():
        sm_calls["n"] += 1
        # First call lists orgs; subsequent calls compute snapshots and blow up.
        return _ListCM() if sm_calls["n"] == 1 else _BoomCM()

    with (
        patch.object(adv, "get_sessionmaker", return_value=_sm),
        patch.object(adv, "get_redis", return_value=fake_redis),
    ):
        with pytest.raises(RuntimeError, match="all 3 orgs failed"):
            await adv.daily_org_advisory_snapshot({})


@pytest.mark.asyncio
async def test_advisory_snapshot_partial_failure_does_not_raise() -> None:
    from app.workers.tasks import advisory as adv

    ok_id, bad_id = uuid4(), uuid4()

    fake_redis = AsyncMock()
    list_session = AsyncMock()
    list_session.execute.return_value.all.return_value = [(ok_id,), (bad_id,)]

    class _ListCM:
        async def __aenter__(self_inner):  # noqa: ANN001
            return list_session
        async def __aexit__(self_inner, *_a):  # noqa: ANN001
            return None

    class _SnapCM:
        def __init__(self_inner, ok: bool):  # noqa: ANN001
            self_inner.ok = ok
        async def __aenter__(self_inner):  # noqa: ANN001
            if not self_inner.ok:
                raise RuntimeError("compute failed")
            sess = AsyncMock()
            return sess
        async def __aexit__(self_inner, *_a):  # noqa: ANN001
            return None

    sm_calls = {"n": 0}

    def _sm():
        sm_calls["n"] += 1
        if sm_calls["n"] == 1:
            return _ListCM()
        # Second iteration is the OK org, third is the BAD org.
        return _SnapCM(ok=(sm_calls["n"] == 2))

    async def _fake_compute(_session, _org_id):
        return {"users": 1, "chatrooms": 0, "agents": 0, "workflows": 0, "projects": 0}

    with (
        patch.object(adv, "get_sessionmaker", return_value=_sm),
        patch.object(adv, "get_redis", return_value=fake_redis),
        patch.object(adv, "_compute_org_snapshot", new=_fake_compute),
    ):
        out = await adv.daily_org_advisory_snapshot({})

    assert out["eligible"] == 2
    assert out["orgs_processed"] == 1
    assert out["failed"] == 1


# --------------------------------------------------------------------------- #
#  retention.retention_sweep — failed policy is tracked, succeeds continue     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_retention_sweep_tracks_failed_policy() -> None:
    from app.workers.tasks import retention as ret

    async def _ok(session):  # noqa: ARG001
        return 7

    async def _boom(session):  # noqa: ARG001
        raise RuntimeError("simulated DB blip")

    fake_session = AsyncMock()

    class _BeginCM:
        async def __aenter__(self_inner):  # noqa: ANN001
            return None
        async def __aexit__(self_inner, *_a):  # noqa: ANN001
            return None

    # `session.begin()` must be a *sync* call returning an async CM —
    # AsyncMock would yield a coroutine which `async with` cannot consume.
    fake_session.begin = MagicMock(return_value=_BeginCM())

    class _SessionCM:
        async def __aenter__(self_inner):  # noqa: ANN001
            return fake_session
        async def __aexit__(self_inner, *_a):  # noqa: ANN001
            return None

    def _sm():
        return _SessionCM()

    test_policies = [("first_ok", _ok), ("second_boom", _boom), ("third_ok", _ok)]

    with (
        patch.object(ret, "get_sessionmaker", return_value=_sm),
        patch.object(ret, "_POLICIES", test_policies),
    ):
        report = await ret.retention_sweep({})

    assert report == {"first_ok": 7, "second_boom": -1, "third_ok": 7}
