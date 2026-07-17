"""Phase 4 transport (D-58) — the import/export job state, slots, and worker tasks.

The bundle *service* is covered by `test_skill_bundle.py`; these tests cover the transport
that wraps it: the Redis-backed job state a caller polls, the per-org concurrency slots that
bound a tenant's claim on the worker, and the two Arq tasks that run the service off-request
and record the outcome. The endpoints themselves are thin adapters over `_import`/`_export`
and are exercised at the wiring level (registration) plus the pieces they compose.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.workers.tasks import skills as worker
from contexts.skills.application import bundle_jobs
from contexts.skills.domain.errors import BundleInvalid, BundleQuarantined, SkillUnreadable
from contexts.skills.domain.models import SkillScope
from contexts.skills.interfaces.facade import SkillsFacade


class _FakeRedis:
    """In-memory subset of the async Redis surface `bundle_jobs` uses."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.expires: dict[str, int] = {}

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value.encode() if isinstance(value, str) else value
        if ex is not None:
            self.expires[key] = ex

    async def incr(self, key: str) -> int:
        current = int(self.values.get(key, b"0")) + 1
        self.values[key] = str(current).encode()
        return current

    async def decr(self, key: str) -> int:
        current = int(self.values.get(key, b"0")) - 1
        self.values[key] = str(current).encode()
        return current

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    r = _FakeRedis()
    monkeypatch.setattr(bundle_jobs, "get_redis", lambda: r)
    return r


class TestImportJobState:
    async def test_create_get_round_trip(self, redis: _FakeRedis) -> None:
        owner = uuid.uuid4()
        actor = uuid.uuid4()
        job = await bundle_jobs.create_import(scope="org", owner_id=owner, actor_user_id=actor)
        got = await bundle_jobs.get_import(job.job_id)
        assert got is not None
        assert got.status is bundle_jobs.BundleJobStatus.QUEUED
        assert got.owner_id == owner
        assert got.actor_user_id == actor
        assert got.skill_id is None
        assert got.warnings == ()
        assert got.error is None

    async def test_ready_carries_skill_and_warnings(self, redis: _FakeRedis) -> None:
        job = await bundle_jobs.create_import(scope="platform", owner_id=None, actor_user_id=uuid.uuid4())
        skill_id = uuid.uuid4()
        await bundle_jobs.mark_import_running(job.job_id)
        await bundle_jobs.mark_import_ready(
            job_id=job.job_id, skill_id=skill_id, warnings=("scripts use the network",)
        )
        got = await bundle_jobs.get_import(job.job_id)
        assert got is not None
        assert got.status is bundle_jobs.BundleJobStatus.READY
        assert got.skill_id == skill_id
        assert got.warnings == ("scripts use the network",)

    async def test_failed_carries_reason(self, redis: _FakeRedis) -> None:
        job = await bundle_jobs.create_import(scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4())
        await bundle_jobs.mark_import_failed(job_id=job.job_id, error="bundle is invalid")
        got = await bundle_jobs.get_import(job.job_id)
        assert got is not None
        assert got.status is bundle_jobs.BundleJobStatus.FAILED
        assert got.error == "bundle is invalid"

    async def test_unknown_job_is_none(self, redis: _FakeRedis) -> None:
        assert await bundle_jobs.get_import(uuid.uuid4()) is None

    async def test_marking_a_vanished_job_is_a_noop(self, redis: _FakeRedis) -> None:
        # The TTL can expire between create and mark; marking must not resurrect it.
        await bundle_jobs.mark_import_ready(job_id=uuid.uuid4(), skill_id=uuid.uuid4(), warnings=())
        assert not redis.values


class TestExportJobState:
    async def test_ready_carries_object_location(self, redis: _FakeRedis) -> None:
        job = await bundle_jobs.create_export(
            skill_id=uuid.uuid4(), scope="agent", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )
        await bundle_jobs.mark_export_running(job.job_id)
        await bundle_jobs.mark_export_ready(
            job_id=job.job_id, bucket="exports", object_key="skills/x/skill.zip"
        )
        got = await bundle_jobs.get_export(job.job_id)
        assert got is not None
        assert got.status is bundle_jobs.BundleJobStatus.READY
        assert got.bucket == "exports"
        assert got.object_key == "skills/x/skill.zip"

    async def test_failed_carries_reason(self, redis: _FakeRedis) -> None:
        job = await bundle_jobs.create_export(
            skill_id=uuid.uuid4(), scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )
        await bundle_jobs.mark_export_failed(job_id=job.job_id, error="skill unreadable")
        got = await bundle_jobs.get_export(job.job_id)
        assert got is not None
        assert got.status is bundle_jobs.BundleJobStatus.FAILED
        assert got.error == "skill unreadable"


class TestConcurrencySlots:
    async def test_acquire_up_to_the_cap_then_refuse(self, redis: _FakeRedis) -> None:
        key = "org:abc"
        for _ in range(bundle_jobs.MAX_CONCURRENT_IMPORTS_PER_ORG):
            assert await bundle_jobs.acquire_import_slot(key) is True
        # One past the cap is refused, and the refusal does not consume a slot — the count
        # is decremented back so a later release cannot over-grant.
        assert await bundle_jobs.acquire_import_slot(key) is False
        assert int(redis.values[bundle_jobs._slot_key(key)]) == bundle_jobs.MAX_CONCURRENT_IMPORTS_PER_ORG

    async def test_release_frees_a_slot(self, redis: _FakeRedis) -> None:
        key = "org:abc"
        for _ in range(bundle_jobs.MAX_CONCURRENT_IMPORTS_PER_ORG):
            await bundle_jobs.acquire_import_slot(key)
        assert await bundle_jobs.acquire_import_slot(key) is False
        await bundle_jobs.release_import_slot(key)
        # A slot came free, so the next acquire succeeds.
        assert await bundle_jobs.acquire_import_slot(key) is True

    async def test_release_never_goes_negative(self, redis: _FakeRedis) -> None:
        # A leaked/expired slot released twice must clamp at zero, not leave a negative that
        # would over-grant the next acquires.
        key = "org:abc"
        await bundle_jobs.release_import_slot(key)
        await bundle_jobs.release_import_slot(key)
        assert int(redis.values[bundle_jobs._slot_key(key)]) == 0

    async def test_slots_are_per_org(self, redis: _FakeRedis) -> None:
        for _ in range(bundle_jobs.MAX_CONCURRENT_IMPORTS_PER_ORG):
            await bundle_jobs.acquire_import_slot("org:a")
        assert await bundle_jobs.acquire_import_slot("org:a") is False
        # A different org's budget is untouched.
        assert await bundle_jobs.acquire_import_slot("org:b") is True


class TestFacadeJobDelegators:
    """The routers reach job state through the facade (SoC), not `bundle_jobs` directly.

    These delegators are sessionless staticmethods; the test drives them with no `db` — the
    same way the status GET endpoints do — and checks they round-trip through the module.
    """

    async def test_import_job_delegators_round_trip(self, redis: _FakeRedis) -> None:
        owner = uuid.uuid4()
        job = await SkillsFacade.create_import_job(
            scope=SkillScope.ORG, owner_id=owner, actor_user_id=uuid.uuid4()
        )
        got = await SkillsFacade.get_import_job(job.job_id)
        assert got is not None
        assert got.scope == "org"
        assert got.owner_id == owner

    async def test_export_job_delegators_round_trip(self, redis: _FakeRedis) -> None:
        job = await SkillsFacade.create_export_job(
            skill_id=uuid.uuid4(), scope=SkillScope.PLATFORM, owner_id=None, actor_user_id=uuid.uuid4()
        )
        await SkillsFacade.mark_export_job_failed(job_id=job.job_id, error="failed to enqueue export")
        got = await SkillsFacade.get_export_job(job.job_id)
        assert got is not None
        assert got.status is bundle_jobs.BundleJobStatus.FAILED
        assert got.error == "failed to enqueue export"

    async def test_slot_delegators_round_trip(self, redis: _FakeRedis) -> None:
        key = "org:facade"
        for _ in range(bundle_jobs.MAX_CONCURRENT_IMPORTS_PER_ORG):
            assert await SkillsFacade.acquire_import_slot(key) is True
        assert await SkillsFacade.acquire_import_slot(key) is False
        await SkillsFacade.release_import_slot(key)
        assert await SkillsFacade.acquire_import_slot(key) is True


# ---------------------------------------------------------------------------
# worker tasks
# ---------------------------------------------------------------------------


class _FakeMinio:
    skill_bundles_bucket = "skill-bundles"
    exports_bucket = "exports"

    def __init__(self, data: bytes = b"zip-bytes") -> None:
        self.data = data
        self.removed: list[tuple[str, str]] = []
        self.put: list[tuple[str, str, bytes]] = []

    async def get_object(self, *, bucket: str, key: str) -> bytes:
        return self.data

    async def put_object(self, *, bucket: str, key: str, data: bytes, content_type: str) -> None:
        self.put.append((bucket, key, data))

    async def remove(self, *, bucket: str, key: str) -> None:
        self.removed.append((bucket, key))


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def commit(self) -> None:
        return None


def _fake_skill() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="deploy",
        scope=SimpleNamespace(value="org"),
        source=SimpleNamespace(value="imported"),
        bundle_sha256="b" * 64,
        body_sha256="c" * 64,
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> dict[str, Any]:
    """Replace everything the worker reaches for; return the spies the tests read."""
    from shared_kernel import audit

    minio = _FakeMinio()
    events: list[audit.AuditEvent] = []

    monkeypatch.setattr(worker, "get_sessionmaker", lambda: (lambda: _Session()))
    monkeypatch.setattr("shared_kernel.storage.get_minio_client", lambda: minio)

    async def _capture(_db: object, event: audit.AuditEvent) -> None:
        events.append(event)

    monkeypatch.setattr(audit, "emit", _capture)
    return {"minio": minio, "events": events, "redis": redis}


class _FakeFacade:
    """Stands in for `SkillsFacade` in the worker. Records the bytes it was handed."""

    result: ClassVar[Any] = None
    export_bytes: ClassVar[bytes] = b""
    export_error: ClassVar[Exception | None] = None
    seen_data: ClassVar[list[bytes]] = []

    def __init__(self, _db: object) -> None:
        pass

    async def import_bundle(self, *, data: bytes, **kwargs: Any) -> Any:
        _FakeFacade.seen_data.append(data)
        if isinstance(_FakeFacade.result, Exception):
            raise _FakeFacade.result
        return _FakeFacade.result

    async def export_bundle(self, skill_id: uuid.UUID, scope: Any, *, owner_id: uuid.UUID | None) -> bytes:
        if _FakeFacade.export_error is not None:
            raise _FakeFacade.export_error
        return _FakeFacade.export_bytes


@pytest.fixture(autouse=True)
def _reset_fake_facade() -> None:
    _FakeFacade.result = None
    _FakeFacade.export_bytes = b""
    _FakeFacade.export_error = None
    _FakeFacade.seen_data = []


class TestImportWorker:
    async def test_success_marks_ready_audits_releases_and_cleans_up(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        skill = _fake_skill()
        file = SimpleNamespace(id=uuid.uuid4(), sha256="d" * 64)
        _FakeFacade.result = SimpleNamespace(skill=skill, files=(file,), warnings=("net warning",))

        job = await bundle_jobs.create_import(scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4())
        org_key = "org:xyz"
        await bundle_jobs.acquire_import_slot(org_key)

        out = await worker.skill_import_bundle(
            {},
            job_id=str(job.job_id),
            object_key="imports/x.zip",
            scope="org",
            owner_id=str(uuid.uuid4()),
            actor_user_id=str(uuid.uuid4()),
            org_key=org_key,
        )
        assert out == "imported"

        state = await bundle_jobs.get_import(job.job_id)
        assert state is not None
        assert state.status is bundle_jobs.BundleJobStatus.READY
        assert state.skill_id == skill.id
        assert state.warnings == ("net warning",)

        # R31.25's distinct import event, keyed on the skill.
        events = wired["events"]
        assert [e.action for e in events] == ["skill.bundle_imported"]
        assert events[0].resource_id == skill.id
        assert events[0].metadata["file_count"] == 1

        # Slot released (back to 0) and the staging object deleted — both in `finally`.
        assert int(wired["redis"].values[bundle_jobs._slot_key(org_key)]) == 0
        assert wired["minio"].removed == [("skill-bundles", "imports/x.zip")]

    async def test_an_audit_write_failure_does_not_fail_a_committed_import(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The skill is created and committed before the audit runs; a fault in the audit
        # write must leave the job READY, not FAILED. A false 'failed' would send the user to
        # re-import a skill that already exists — where the name collision fails the retry
        # too, stranding a live skill the UI calls failed.
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        _FakeFacade.result = SimpleNamespace(skill=_fake_skill(), files=(), warnings=())

        from shared_kernel import audit

        async def _boom(_db: object, _event: Any) -> None:
            raise RuntimeError("audit table unavailable")

        monkeypatch.setattr(audit, "emit", _boom)

        job = await bundle_jobs.create_import(scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4())
        out = await worker.skill_import_bundle(
            {},
            job_id=str(job.job_id),
            object_key="imports/x.zip",
            scope="org",
            owner_id=None,
            actor_user_id=str(uuid.uuid4()),
            org_key="org:xyz",
        )
        assert out == "imported"
        state = await bundle_jobs.get_import(job.job_id)
        assert state is not None
        assert state.status is bundle_jobs.BundleJobStatus.READY
        assert state.skill_id is not None

    async def test_a_rejected_bundle_marks_failed_with_its_reason(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        _FakeFacade.result = BundleInvalid("bundle has no SKILL.md at its root")

        job = await bundle_jobs.create_import(scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4())
        org_key = "org:xyz"
        await bundle_jobs.acquire_import_slot(org_key)

        out = await worker.skill_import_bundle(
            {},
            job_id=str(job.job_id),
            object_key="imports/x.zip",
            scope="org",
            owner_id=None,
            actor_user_id=str(uuid.uuid4()),
            org_key=org_key,
        )
        assert out == "rejected"
        state = await bundle_jobs.get_import(job.job_id)
        assert state is not None
        assert state.status is bundle_jobs.BundleJobStatus.FAILED
        # The author-facing reason reaches the client verbatim (it is a BundleInvalid).
        assert "SKILL.md" in (state.error or "")
        # No audit event for a rejected import, and the slot + staging are still cleaned up.
        assert wired["events"] == []
        assert int(wired["redis"].values[bundle_jobs._slot_key(org_key)]) == 0
        assert wired["minio"].removed == [("skill-bundles", "imports/x.zip")]

    async def test_a_quarantined_bundle_is_rejected(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        _FakeFacade.result = BundleQuarantined("scripts/evil.sh")

        job = await bundle_jobs.create_import(scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4())
        out = await worker.skill_import_bundle(
            {},
            job_id=str(job.job_id),
            object_key="imports/x.zip",
            scope="org",
            owner_id=None,
            actor_user_id=str(uuid.uuid4()),
            org_key="org:xyz",
        )
        assert out == "rejected"
        state = await bundle_jobs.get_import(job.job_id)
        assert state is not None
        assert state.status is bundle_jobs.BundleJobStatus.FAILED

    async def test_an_unexpected_error_fails_with_a_generic_reason(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A worker-internal fault must not leak a stack trace to the client, and it must
        # still release the slot and delete the staging object.
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        _FakeFacade.result = RuntimeError("connection reset by peer")

        job = await bundle_jobs.create_import(scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4())
        org_key = "org:xyz"
        await bundle_jobs.acquire_import_slot(org_key)
        out = await worker.skill_import_bundle(
            {},
            job_id=str(job.job_id),
            object_key="imports/x.zip",
            scope="org",
            owner_id=None,
            actor_user_id=str(uuid.uuid4()),
            org_key=org_key,
        )
        assert out == "failed"
        state = await bundle_jobs.get_import(job.job_id)
        assert state is not None
        assert state.error == "import failed"
        assert "reset" not in (state.error or "")
        assert int(wired["redis"].values[bundle_jobs._slot_key(org_key)]) == 0
        assert wired["minio"].removed == [("skill-bundles", "imports/x.zip")]

    async def test_the_staged_bytes_reach_the_service(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        wired["minio"].data = b"the real zip"
        _FakeFacade.result = SimpleNamespace(skill=_fake_skill(), files=(), warnings=())

        job = await bundle_jobs.create_import(scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4())
        await worker.skill_import_bundle(
            {},
            job_id=str(job.job_id),
            object_key="imports/x.zip",
            scope="org",
            owner_id=None,
            actor_user_id=str(uuid.uuid4()),
            org_key="org:xyz",
        )
        assert _FakeFacade.seen_data == [b"the real zip"]


class TestExportWorker:
    async def test_success_writes_the_zip_and_marks_ready(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        _FakeFacade.export_bytes = b"PK\x03\x04 the bundle"
        skill_id = uuid.uuid4()
        job = await bundle_jobs.create_export(
            skill_id=skill_id, scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )
        out = await worker.skill_export_bundle(
            {}, job_id=str(job.job_id), skill_id=str(skill_id), scope="org", owner_id=str(uuid.uuid4())
        )
        assert out == "exported"

        put = wired["minio"].put
        assert len(put) == 1
        bucket, key, data = put[0]
        assert bucket == "exports"
        assert key == f"skills/{job.job_id}/skill.zip"
        assert data == b"PK\x03\x04 the bundle"

        state = await bundle_jobs.get_export(job.job_id)
        assert state is not None
        assert state.status is bundle_jobs.BundleJobStatus.READY
        assert state.bucket == "exports"
        assert state.object_key == key

    async def test_an_unreadable_skill_fails_the_export(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D-64's exfiltration direction: export must apply the same fail-closed gate as
        # read_skill. A SkillUnreadable is a domain error, so its message reaches the client.
        monkeypatch.setattr("contexts.skills.interfaces.facade.SkillsFacade", _FakeFacade)
        _FakeFacade.export_error = SkillUnreadable("deploy", path="assets/x.bin")
        skill_id = uuid.uuid4()
        job = await bundle_jobs.create_export(
            skill_id=skill_id, scope="org", owner_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )
        out = await worker.skill_export_bundle(
            {}, job_id=str(job.job_id), skill_id=str(skill_id), scope="org", owner_id=None
        )
        assert out == "failed"
        state = await bundle_jobs.get_export(job.job_id)
        assert state is not None
        assert state.status is bundle_jobs.BundleJobStatus.FAILED
        assert "deploy" in (state.error or "")
        assert wired["minio"].put == []


class TestWiring:
    def test_both_tasks_are_registered_with_arq(self) -> None:
        # A task nobody registers never runs — the 202 would resolve to a job that sits
        # QUEUED forever.
        from app.workers.main import WorkerSettings

        assert worker.skill_import_bundle in WorkerSettings.functions
        assert worker.skill_export_bundle in WorkerSettings.functions

    def test_the_bundle_router_is_mounted(self) -> None:
        # The status endpoints are useless unmounted; the 202 flow would have no poll target.
        from app.api.v1 import get_router_registry
        from app.api.v1 import skills as skills_routes

        routers = [e.router for e in get_router_registry()]
        assert skills_routes.bundle_router in routers
