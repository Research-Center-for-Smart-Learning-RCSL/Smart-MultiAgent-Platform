"""Shared FastAPI dependencies for the v1 API layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from shared_kernel.auth.permissions import Principal


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Reusable limit/offset pagination extracted from query-string params."""

    limit: int = Query(100, ge=1, le=500, description="Max items to return")  # noqa: RUF009
    offset: int = Query(0, ge=0, description="Number of items to skip")  # noqa: RUF009


def parse_if_match(raw: str | None) -> int | None:
    """Parse an ``If-Match`` header into an expected version; ``None`` means unset.

    Callers that require the header type it as ``str`` and get an ``int`` back; callers
    for whom optimistic locking is opt-in pass ``None`` through. A malformed value is a
    412 rather than a 400 — the client sent a precondition we cannot evaluate.

    The same six lines were living in agents.py, chatrooms.py, messages.py,
    prompt_studio.py, and skills.py; collapsed here alongside
    :func:`assert_project_membership` for the reason recorded there.
    """
    if raw is None:
        return None
    try:
        return int(raw.strip().strip('"'))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=412, detail=f"invalid If-Match: {raw!r}") from exc


def require_if_match(raw: str) -> int:
    """:func:`parse_if_match` for routes where FastAPI declares the header required.

    Exists so those callers get an ``int`` instead of an ``int | None`` they would have to
    narrow at every call site. FastAPI rejects the missing header before the handler runs,
    so the guard below is unreachable in practice — it is here because a return type
    should be proven, not asserted.
    """
    parsed = parse_if_match(raw)
    if parsed is None:
        raise HTTPException(status_code=412, detail="If-Match is required")
    return parsed


async def assert_project_owner(
    *,
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
    reason: str = "only a project owner may perform this action",
) -> None:
    """Raise 403 unless the principal is a strict Project Owner (admin bypasses).

    The single owner-gate for route handlers so the admin-bypass + owner-check
    decision lives in one place instead of being copy-pasted (and reaching into
    the private ``_raise_forbidden``) across routers.
    """
    from contexts.tenancy.interfaces.facade import TenancyFacade
    from shared_kernel.auth.dependencies import _raise_forbidden

    if principal.is_admin:
        return
    if not await TenancyFacade(db).is_project_owner(principal.user_id, project_id):
        _raise_forbidden(reason)


async def assert_project_membership(
    *,
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
    reason: str = "caller is not a member of the project",
) -> None:
    """Raise 403 unless the principal has any role on the project (admin bypasses).

    The single membership-gate for read routes on a project-scoped resource —
    was hand-copied identically across graphrag.py, knowmap.py, and
    agent_groups.py (found in code review, 2026-07-10); collapsed here
    alongside :func:`assert_project_owner` for the same reason.
    """
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    if principal.is_admin:
        return
    resolver = await get_role_resolver(db)
    roles = await resolver.roles_for(principal, Scope(project_id=project_id))
    if not roles:
        _raise_forbidden(reason)


async def validate_agent_allowlist(
    *,
    db: AsyncSession,
    config_id: uuid.UUID,
    project_id: uuid.UUID,
    agent_ids: list[uuid.UUID],
    config_id_attr: str,
) -> list[uuid.UUID]:
    """Ensure every id is a live agent in ``project_id`` bound to ``config_id``.

    A document's allowlist may only name agents that actually consume this
    config — naming an agent bound to a different config (or another project)
    would be a no-op at retrieval and an AuthZ smell, so we reject it at the
    boundary. Returns the de-duplicated list on success; raises 422 otherwise.

    ``config_id_attr`` names the Agent attribute to compare against
    ``config_id`` (``"rag_config_id"`` or ``"knowmap_config_id"``) — was
    hand-copied identically (differing only in that attribute name) across
    rag.py and knowmap.py (found in code review, 2026-07-10).
    """
    from contexts.agents.interfaces.facade import AgentsFacade

    if not agent_ids:
        return []
    requested = set(agent_ids)
    bound = {
        a.id
        for a in await AgentsFacade(db).list_agents_for_project(project_id)
        if getattr(a, config_id_attr) == config_id
    }
    unknown = requested - bound
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"agent_ids not bound to this config: {sorted(str(u) for u in unknown)}",
        )
    return list(requested)
