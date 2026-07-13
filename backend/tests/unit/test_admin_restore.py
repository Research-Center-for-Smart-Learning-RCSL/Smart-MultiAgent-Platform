"""Admin restore (R8.13) — the pieces without a context-local service test home:
identity user restore, workflow definition restore (incl. the name-conflict 409),
and the route dispatcher that fans each resource_type out to its owning facade.

Tenancy/agents/conversation admin_restore live in their own service test modules.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.v1 import admin_projects
from contexts.identity.application.admin_service import AdminService
from contexts.workflow.application.workflow_service import WorkflowService
from shared_kernel.db.restore import RestoreConflict, raise_restore_conflict

_ADMIN = uuid.uuid4()


def _integrity_error(message: str) -> IntegrityError:
    """An IntegrityError whose ``orig`` carries *message* — matches what asyncpg
    surfaces (the constraint name appears in the DB error text)."""
    return IntegrityError("UPDATE ...", {}, Exception(message))


# --------------------------------------------------------------------------- #
# shared discrimination helper — the collision-vs-unrelated decision
# --------------------------------------------------------------------------- #


class TestRaiseRestoreConflict:
    def test_matching_constraint_raises_restore_conflict(self) -> None:
        exc = _integrity_error('duplicate key value violates unique constraint "uq_orgs_name_active"')
        with pytest.raises(RestoreConflict) as info:
            raise_restore_conflict(exc, unique_constraint="uq_orgs_name_active", resource_type="org")
        assert info.value.resource_type == "org"

    def test_matching_one_of_several_constraints(self) -> None:
        exc = _integrity_error('violates unique constraint "uq_projects_org_name"')
        with pytest.raises(RestoreConflict):
            raise_restore_conflict(
                exc,
                unique_constraint=("uq_projects_user_name", "uq_projects_org_name"),
                resource_type="project",
            )

    def test_unrelated_integrity_error_is_reraised(self) -> None:
        exc = _integrity_error('violates foreign key constraint "fk_agents_project_id_projects"')
        # A non-name-collision fault must surface as its real cause, not a false 409.
        with pytest.raises(IntegrityError):
            raise_restore_conflict(
                exc, unique_constraint="uq_agents_project_name_active", resource_type="agent"
            )


# --------------------------------------------------------------------------- #
# identity — AdminService.restore_user
# --------------------------------------------------------------------------- #


class TestRestoreUser:
    @patch("contexts.identity.application.admin_service.audit.emit", new_callable=AsyncMock)
    async def test_success_clears_reactivates_and_audits(self, audit_emit) -> None:
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(rowcount=1)
        svc = AdminService(db)

        ok = await svc.restore_user(resource_id=uuid.uuid4(), admin_user_id=_ADMIN, actor_ip=None)

        assert ok is True
        # Two UPDATEs: the guarded deleted_at clear + the status/ban reset (R8.13).
        assert db.execute.await_count == 2
        audit_emit.assert_awaited_once()
        event = audit_emit.await_args.args[1]
        assert event.action == "admin.restore_resource"
        assert event.resource_type == "user"

    @patch("contexts.identity.application.admin_service.audit.emit", new_callable=AsyncMock)
    async def test_not_soft_deleted_returns_false_without_reset(self, audit_emit) -> None:
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(rowcount=0)
        svc = AdminService(db)

        ok = await svc.restore_user(resource_id=uuid.uuid4(), admin_user_id=_ADMIN, actor_ip=None)

        assert ok is False
        # Only the guarded clear ran; no reset UPDATE, no audit.
        assert db.execute.await_count == 1
        audit_emit.assert_not_awaited()

    @patch("contexts.identity.application.admin_service.audit.emit", new_callable=AsyncMock)
    async def test_email_reuse_raises_restore_conflict(self, audit_emit) -> None:
        db = AsyncMock()
        db.execute.side_effect = _integrity_error('violates unique constraint "uq_users_email_active"')
        svc = AdminService(db)

        with pytest.raises(RestoreConflict) as info:
            await svc.restore_user(resource_id=uuid.uuid4(), admin_user_id=_ADMIN, actor_ip=None)
        assert info.value.resource_type == "user"
        # The clear UPDATE raised before any reset; no audit emitted.
        audit_emit.assert_not_awaited()

    @patch("contexts.identity.application.admin_service.audit.emit", new_callable=AsyncMock)
    async def test_previously_banned_user_is_restored_to_banned(self, audit_emit) -> None:
        # Restore reverses a deletion; it must not silently lift a ban. A user
        # banned before soft-delete (banned_reason preserved through delete) is
        # routed straight back to BANNED, and its ban metadata is never wiped.
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(rowcount=1)
        svc = AdminService(db)

        await svc.restore_user(resource_id=uuid.uuid4(), admin_user_id=_ADMIN, actor_ip=None)

        # The status reset is the second UPDATE (after the guarded deleted_at clear).
        reset_stmt = db.execute.await_args_list[1].args[0]
        compiled = str(reset_stmt).lower()
        # A pre-existing ban (banned_reason set) routes back to BANNED, not ACTIVE.
        assert "banned_reason is not null" in compiled
        # Ban metadata is left intact — restore never nulls banned_at/banned_reason.
        assert "banned_at" not in compiled
        assert "set banned_reason" not in compiled


# --------------------------------------------------------------------------- #
# workflow — WorkflowService.admin_restore
# --------------------------------------------------------------------------- #


def _workflow_service(repo: AsyncMock) -> WorkflowService:
    svc = WorkflowService(AsyncMock())
    svc._repo = repo
    return svc


class TestWorkflowAdminRestore:
    @patch("contexts.workflow.application.workflow_service.audit.emit", new_callable=AsyncMock)
    async def test_success(self, audit_emit) -> None:
        repo = AsyncMock()
        repo.restore.return_value = True
        wid = uuid.uuid4()

        ok = await _workflow_service(repo).admin_restore(wid, admin_user_id=_ADMIN, actor_ip=None)

        assert ok is True
        repo.restore.assert_awaited_once_with(wid)
        audit_emit.assert_awaited_once()
        assert audit_emit.await_args.args[1].resource_type == "workflow"

    @patch("contexts.workflow.application.workflow_service.audit.emit", new_callable=AsyncMock)
    async def test_not_soft_deleted_returns_false(self, audit_emit) -> None:
        repo = AsyncMock()
        repo.restore.return_value = False

        ok = await _workflow_service(repo).admin_restore(uuid.uuid4(), admin_user_id=_ADMIN, actor_ip=None)

        assert ok is False
        audit_emit.assert_not_awaited()

    @patch("contexts.workflow.application.workflow_service.audit.emit", new_callable=AsyncMock)
    async def test_name_reuse_raises_restore_conflict(self, audit_emit) -> None:
        repo = AsyncMock()
        repo.restore.side_effect = _integrity_error(
            'violates unique constraint "uq_workflows_workspace_id_name"'
        )

        with pytest.raises(RestoreConflict) as info:
            await _workflow_service(repo).admin_restore(uuid.uuid4(), admin_user_id=_ADMIN, actor_ip=None)
        assert info.value.resource_type == "workflow"
        audit_emit.assert_not_awaited()

    @patch("contexts.workflow.application.workflow_service.audit.emit", new_callable=AsyncMock)
    async def test_unrelated_integrity_error_propagates(self, audit_emit) -> None:
        repo = AsyncMock()
        repo.restore.side_effect = _integrity_error('violates check constraint "ck_workflows_state"')

        # Not a name collision — must surface as its real cause, not a 409.
        with pytest.raises(IntegrityError):
            await _workflow_service(repo).admin_restore(uuid.uuid4(), admin_user_id=_ADMIN, actor_ip=None)
        audit_emit.assert_not_awaited()


# --------------------------------------------------------------------------- #
# route — restore_resource dispatches each type to its owning facade
# --------------------------------------------------------------------------- #

_FACADE_ATTRS = [
    "IdentityFacade",
    "TenancyFacade",
    "AgentsFacade",
    "WorkflowFacade",
    "ConversationFacade",
]


def _install_recording_facades(monkeypatch, *, result: bool = True) -> dict[str, dict]:
    """Replace every facade in the route module with a recorder that returns
    `result`. Returns a dict populated with {called_method_name: kwargs}."""
    called: dict[str, dict] = {}

    def make_fake():
        class _Fake:
            def __init__(self, _db) -> None:
                pass

            def __getattr__(self, name: str):
                async def _m(**kwargs):
                    called[name] = kwargs
                    return result

                return _m

        return _Fake

    for attr in _FACADE_ATTRS:
        monkeypatch.setattr(admin_projects, attr, make_fake())
    return called


async def _invoke(resource_type: str, resource_id: uuid.UUID):
    return await admin_projects.restore_resource(
        resource_type=resource_type,  # type: ignore[arg-type]
        resource_id=resource_id,
        admin=SimpleNamespace(user_id=_ADMIN),
        ctx=SimpleNamespace(actor_ip=None, request_id=None),
        db=object(),
    )


class TestRestoreRouteDispatch:
    @pytest.mark.parametrize(
        ("resource_type", "method"),
        [
            ("user", "restore_user"),
            ("org", "restore_org"),
            ("project", "restore_project"),
            ("agent", "restore_agent"),
            ("workflow", "restore_workflow"),
            ("chatroom", "restore_chatroom"),
        ],
    )
    async def test_dispatches_to_owning_facade(self, monkeypatch, resource_type, method) -> None:
        called = _install_recording_facades(monkeypatch, result=True)
        rid = uuid.uuid4()

        out = await _invoke(resource_type, rid)

        assert out.restored is True
        assert method in called
        assert called[method]["resource_id"] == rid
        assert called[method]["admin_user_id"] == _ADMIN

    async def test_returns_404_when_not_restored(self, monkeypatch) -> None:
        _install_recording_facades(monkeypatch, result=False)

        with pytest.raises(HTTPException) as exc_info:
            await _invoke("agent", uuid.uuid4())
        assert exc_info.value.status_code == 404

    @pytest.mark.parametrize(
        ("resource_type", "facade_attr", "method"),
        [
            ("agent", "AgentsFacade", "restore_agent"),
            ("workflow", "WorkflowFacade", "restore_workflow"),
            ("org", "TenancyFacade", "restore_org"),
        ],
    )
    async def test_maps_restore_conflict_to_409(
        self, monkeypatch, resource_type, facade_attr, method
    ) -> None:
        # A natural-key collision surfaces uniformly as 409, whichever type it is.
        class _Fake:
            def __init__(self, _db) -> None:
                pass

            def __getattr__(self, name: str):
                async def _m(**kwargs):
                    raise RestoreConflict(resource_type)

                return _m

        monkeypatch.setattr(admin_projects, facade_attr, _Fake)

        with pytest.raises(HTTPException) as exc_info:
            await _invoke(resource_type, uuid.uuid4())
        assert exc_info.value.status_code == 409
        assert resource_type in exc_info.value.detail
