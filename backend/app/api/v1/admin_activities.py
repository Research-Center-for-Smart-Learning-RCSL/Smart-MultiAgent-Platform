"""`/api/admin/activity-*` — read-only cross-project activity governance view ([R30.31]).

Deliberately tenant-unscoped: these are the only reads in the platform that span
every org's activities, which is the point — an admin cannot govern what they
cannot see. `require_admin` is therefore the whole access control, so it is the
first dependency on every handler here.

Cross-context attributes (project name, chatroom name, activity-type name) are
resolved by batch facade calls, never a SQL join: [R30.09] forbids cross-context
joins, and the activities context must not read another context's tables. The
batching shape follows `keys.py`'s per-project hydration.

Unlike `admin_projects` / `admin_orgs` / `admin_rate_limits`, this module runs no
raw SQL of its own — it goes through the context facades, per `backend/CLAUDE.md`.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from contexts.activities.domain.errors import ActivityPolicyVersionMismatch
from contexts.activities.domain.models import PLATFORM_SCOPE, ActivityActivation, ActivityPolicy
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(tags=["admin"])

# Keyset page size. Matches admin_projects/orgs/users so the admin surface has one
# pagination contract rather than a third.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class AdminActivityTypeOut(BaseModel):
    """One activity type, platform-wide.

    Carries `validator_config` deliberately (Q-3 of the dossier): an admin already
    reads it through the project API by bypass, so withholding it here buys no
    confidentiality and costs a screen switch during triage. It may hold answer
    keys, so this model stays admin-only — it must not be reused by a
    non-admin surface ([R30.25]).
    """

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str | None
    key: str
    name: str
    validator_kind: str
    # `dict[str, Any]` rather than a bare `dict`: the codegen widens an
    # unparameterised dict to `Record<string, any>`, and `any` on the one field
    # that may carry answer keys is the wrong default.
    validator_config: dict[str, Any]
    expose_payload_to_agent: bool
    echo_includes_content: bool
    retention_days: int | None
    version: int
    created_at: str


class AdminActivityActivationOut(BaseModel):
    id: uuid.UUID
    chatroom_id: uuid.UUID
    chatroom_name: str | None
    activity_type_id: uuid.UUID
    activity_type_key: str | None
    activity_type_name: str | None
    started_by_user_id: uuid.UUID
    created_at: str


class AdminActivityPolicyOut(BaseModel):
    """The platform governance policy ([R30.29]).

    ``version`` is 0 when no policy has ever been saved — the client uses that to
    know it is creating rather than replacing, and must not send ``If-Match``.
    """

    expose_payload_to_agent_default: bool
    expose_payload_to_agent_locked: bool
    echo_includes_content_default: bool
    echo_includes_content_locked: bool
    retention_days_default: int | None
    retention_days_max: int | None
    version: int
    updated_at: str | None
    updated_by_user_id: uuid.UUID | None


class AdminActivityPolicyIn(BaseModel):
    expose_payload_to_agent_default: bool
    expose_payload_to_agent_locked: bool
    echo_includes_content_default: bool
    echo_includes_content_locked: bool
    # `gt=0` mirrors the table's CHECK constraints: a zero or negative retention
    # is not a shorter horizon, it is nonsense.
    retention_days_default: int | None = Field(default=None, gt=0)
    retention_days_max: int | None = Field(default=None, gt=0)


class AdminPolicyImpactOut(BaseModel):
    """What a candidate policy would block.

    Lets the admin form warn before a tightening strands a class ([R30.30]).
    ``violating_activations`` counts activities running at this moment whose type
    the candidate would refuse — they keep running, because enforcement is at
    authoring and activation start, so this is the number an admin tightening for
    a consent reason has to see before saving.

    ``approximate`` is true when either scan hit its bound, so the counts are
    floors rather than a silent truncation.
    """

    violating_types: int
    violating_activations: int
    approximate: bool


def _policy_out(policy: ActivityPolicy) -> AdminActivityPolicyOut:
    return AdminActivityPolicyOut(
        expose_payload_to_agent_default=policy.expose_payload_to_agent_default,
        expose_payload_to_agent_locked=policy.expose_payload_to_agent_locked,
        echo_includes_content_default=policy.echo_includes_content_default,
        echo_includes_content_locked=policy.echo_includes_content_locked,
        retention_days_default=policy.retention_days_default,
        retention_days_max=policy.retention_days_max,
        version=policy.version,
        updated_at=policy.updated_at.isoformat() if policy.updated_at else None,
        updated_by_user_id=policy.updated_by_user_id,
    )


@router.get("/activity-policy")
async def get_activity_policy(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> AdminActivityPolicyOut:
    """The policy in force, or the permissive default when none is saved."""
    return _policy_out(await ActivitiesFacade(db).get_activity_policy())


@router.post("/activity-policy/impact")
async def preview_activity_policy_impact(
    body: AdminActivityPolicyIn,
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> AdminPolicyImpactOut:
    """Count the live types a candidate policy would block, without saving it.

    POST rather than GET because the candidate policy is a body, not an identity;
    it writes nothing.
    """
    candidate = ActivityPolicy(
        id=None,
        scope=PLATFORM_SCOPE,
        expose_payload_to_agent_default=body.expose_payload_to_agent_default,
        expose_payload_to_agent_locked=body.expose_payload_to_agent_locked,
        echo_includes_content_default=body.echo_includes_content_default,
        echo_includes_content_locked=body.echo_includes_content_locked,
        retention_days_default=body.retention_days_default,
        retention_days_max=body.retention_days_max,
        version=0,
    )
    impact = await ActivitiesFacade(db).preview_policy_impact(candidate)
    return AdminPolicyImpactOut(
        violating_types=impact.violating_types,
        violating_activations=impact.violating_activations,
        approximate=impact.approximate,
    )


@router.put("/activity-policy")
async def put_activity_policy(
    body: AdminActivityPolicyIn,
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> AdminActivityPolicyOut:
    """Create or replace the platform policy.

    ``If-Match`` carries the version the admin's form was built against and is
    required once a policy exists; without it a concurrent edit would be silently
    overwritten. A non-integer header is rejected as a mismatch rather than
    ignored — treating an unparseable precondition as "no precondition" would
    defeat the point.
    """
    expected_version: int | None = None
    if if_match is not None:
        try:
            expected_version = int(if_match.strip().strip('"'))
        except ValueError:
            raise ActivityPolicyVersionMismatch(f"If-Match is not a version: {if_match!r}") from None

    policy = await ActivitiesFacade(db).update_activity_policy(
        expose_payload_to_agent_default=body.expose_payload_to_agent_default,
        expose_payload_to_agent_locked=body.expose_payload_to_agent_locked,
        echo_includes_content_default=body.echo_includes_content_default,
        echo_includes_content_locked=body.echo_includes_content_locked,
        retention_days_default=body.retention_days_default,
        retention_days_max=body.retention_days_max,
        expected_version=expected_version,
        actor_user_id=admin.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return _policy_out(policy)


@router.get("/activity-types")
async def list_all_activity_types(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[AdminActivityTypeOut]:
    """Every live activity type across every project, newest first."""
    types = await ActivitiesFacade(db).list_all_types(cursor=cursor, limit=limit)

    # One batch lookup for the whole page, not one per row.
    projects = await TenancyFacade(db).get_projects([at.project_id for at in types])

    def _project_name(project_id: uuid.UUID) -> str | None:
        """None when the project is gone, so one stale row cannot 500 the page.

        Written as an explicit branch rather than ``getattr(..., "name", None)``:
        the dict is precisely typed, and the getattr form would also swallow a
        rename of ``Project.name`` — leaving a silently blank column behind a
        green typecheck.
        """
        project = projects.get(project_id)
        return project.name if project is not None else None

    return [
        AdminActivityTypeOut(
            id=at.id,
            project_id=at.project_id,
            project_name=_project_name(at.project_id),
            key=at.key,
            name=at.name,
            validator_kind=at.validator_kind.value,
            validator_config=at.validator_config,
            expose_payload_to_agent=at.expose_payload_to_agent,
            echo_includes_content=at.echo_includes_content,
            retention_days=at.retention_days,
            version=at.version,
            created_at=at.created_at.isoformat(),
        )
        for at in types
    ]


@router.get("/activity-activations")
async def list_all_active_activations(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[AdminActivityActivationOut]:
    """Every currently-active activation across every room, newest first.

    Answers "which classrooms are running what right now", which is why the room
    and type names are hydrated rather than left as bare ids.
    """
    activities = ActivitiesFacade(db)
    activations = await activities.list_all_active_activations(cursor=cursor, limit=limit)

    # Two batch lookups for the whole page. `get_types_by_ids` stays inside the
    # activities context; `get_chatrooms` crosses to conversation through its
    # facade rather than joining `chatrooms` ([R30.09]).
    types = await activities.get_types_by_ids([a.activity_type_id for a in activations])
    rooms = await ConversationFacade(db).get_chatrooms([a.chatroom_id for a in activations])

    # Explicit branches rather than getattr defaults: the dicts are precisely
    # typed, so a rename upstream should break the typecheck instead of quietly
    # blanking a column. None still means "the room or type is gone", which the
    # ids below keep from making a row useless.
    def _row(a: ActivityActivation) -> AdminActivityActivationOut:
        room = rooms.get(a.chatroom_id)
        at = types.get(a.activity_type_id)
        return AdminActivityActivationOut(
            id=a.id,
            chatroom_id=a.chatroom_id,
            chatroom_name=room.name if room is not None else None,
            activity_type_id=a.activity_type_id,
            activity_type_key=at.key if at is not None else None,
            activity_type_name=at.name if at is not None else None,
            started_by_user_id=a.started_by_user_id,
            created_at=a.created_at.isoformat(),
        )

    return [_row(a) for a in activations]


__all__ = ["router"]
