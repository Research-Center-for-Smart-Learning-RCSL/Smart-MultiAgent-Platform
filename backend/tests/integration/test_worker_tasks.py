"""2.23: Integration coverage for Arq tasks.

These do NOT spin up a real Arq runtime — that would require a live Redis
+ Postgres stack which the Phase J unit/integration tier deliberately avoids.
Instead they exercise the *task callables* directly with stubbed clients,
covering the failure-handling paths the audit flagged:

  * `daily_org_advisory_snapshot` raises `RuntimeError` when every eligible
    org's snapshot fails (2.16)
  * `retention_sweep` records `-1` for a failing policy and surfaces it in
    the structured `failed_policies` payload (2.14)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# --------------------------------------------------------------------------- #
#  advisory.daily_org_advisory_snapshot — all-org failure raises               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_advisory_snapshot_raises_when_every_org_fails() -> None:
    from app.workers.tasks import advisory as adv

    org_ids = [uuid4(), uuid4(), uuid4()]
    call_n = {"n": 0}

    def _make_svc(_session):
        call_n["n"] += 1
        svc = AsyncMock()
        if call_n["n"] == 1:
            svc.list_active_org_ids.return_value = org_ids
        else:
            svc.compute_org_snapshot.side_effect = RuntimeError("postgres down")
        return svc

    class _SessionCM:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *_a):
            return None

    with (
        patch.object(adv, "get_sessionmaker", return_value=lambda: _SessionCM()),
        patch(
            "contexts.tenancy.application.advisory_service.AdvisoryService",
            side_effect=_make_svc,
        ),
        pytest.raises(RuntimeError, match="all 3 orgs failed"),
    ):
        await adv.daily_org_advisory_snapshot({})


@pytest.mark.asyncio
async def test_advisory_snapshot_partial_failure_does_not_raise() -> None:
    from app.workers.tasks import advisory as adv

    ok_id, bad_id = uuid4(), uuid4()
    call_n = {"n": 0}

    def _make_svc(_session):
        call_n["n"] += 1
        svc = AsyncMock()
        if call_n["n"] == 1:
            svc.list_active_org_ids.return_value = [ok_id, bad_id]
        elif call_n["n"] == 2:
            svc.compute_org_snapshot.return_value = {
                "users": 1,
                "chatrooms": 0,
                "agents": 0,
                "workflows": 0,
                "projects": 0,
            }
            svc.cache_snapshot.return_value = None
        else:
            svc.compute_org_snapshot.side_effect = RuntimeError("compute failed")
        return svc

    class _SessionCM:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *_a):
            return None

    with (
        patch.object(adv, "get_sessionmaker", return_value=lambda: _SessionCM()),
        patch(
            "contexts.tenancy.application.advisory_service.AdvisoryService",
            side_effect=_make_svc,
        ),
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

    async def _ok(session):
        return 7

    async def _boom(session):
        raise RuntimeError("simulated DB blip")

    fake_session = AsyncMock()

    class _BeginCM:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_a):
            return None

    # `session.begin()` must be a *sync* call returning an async CM —
    # AsyncMock would yield a coroutine which `async with` cannot consume.
    fake_session.begin = MagicMock(return_value=_BeginCM())

    class _SessionCM:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *_a):
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
