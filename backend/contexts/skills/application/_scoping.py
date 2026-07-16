"""Project -> owning-org resolution for §31's containment predicate.

Deliberately **not** a reuse of `prompt_studio/application/_scoping.py`. That helper is
`get_project(project_id).owner_org_id` with `include_deleted=False`, so it collapses
three distinct outcomes — no such project, soft-deleted project, individually-owned
project — into a bare `None`. §5's org row must tell them apart: an org skill binding
into an individually-owned project is a policy refusal, while binding into a deleted or
nonexistent project is a stale reference, and [R31.25]'s audit event has to name which
one fired. A `None` cannot carry that.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from contexts.tenancy.interfaces.facade import TenancyFacade


class ProjectOwnership(enum.Enum):
    """Which of the four states a project id resolves to."""

    NONEXISTENT = "nonexistent"
    DELETED = "deleted"
    INDIVIDUAL = "individual"
    ORG_OWNED = "org_owned"


@dataclass(frozen=True, slots=True)
class ProjectScope:
    ownership: ProjectOwnership
    owner_org_id: uuid.UUID | None = None

    @property
    def is_live(self) -> bool:
        return self.ownership in (ProjectOwnership.INDIVIDUAL, ProjectOwnership.ORG_OWNED)


async def resolve_project_scope(tenancy: TenancyFacade, project_id: uuid.UUID) -> ProjectScope:
    """Discriminate a project id four ways.

    `include_deleted=True` is what makes `project is None` mean "no such project" rather
    than the union of that and "soft-deleted" — with the default filter the lookup itself
    destroys the distinction it is being asked about.
    """
    project = await tenancy.get_project(project_id, include_deleted=True)
    if project is None:
        return ProjectScope(ProjectOwnership.NONEXISTENT)
    if project.deleted_at is not None:
        return ProjectScope(ProjectOwnership.DELETED)
    if project.owner_org_id is None:
        # projects.owner_user_id XOR owner_org_id (0002_tenancy) — an individually-owned
        # project has no org, so no org skill can contain an agent living in it.
        return ProjectScope(ProjectOwnership.INDIVIDUAL)
    return ProjectScope(ProjectOwnership.ORG_OWNED, owner_org_id=project.owner_org_id)


__all__ = ["ProjectOwnership", "ProjectScope", "resolve_project_scope"]
