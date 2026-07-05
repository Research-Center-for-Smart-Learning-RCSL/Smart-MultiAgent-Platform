"""Shared project -> owning-org lookup for prompt_studio's scope resolution.

ConfigService.resolve_for_project and TemplateService.resolve_for_project both
need this same first step before diverging into their own resolution
algorithms (first-enabled-match vs merge-all-with-a-suppression-flag), so only
this narrow lookup is factored out -- unifying the rest would just reintroduce
the two algorithms' differences as awkward branching in one shared function.
"""

from __future__ import annotations

import uuid

from contexts.tenancy.interfaces.facade import TenancyFacade


async def resolve_owning_org_id(tenancy: TenancyFacade, project_id: uuid.UUID) -> uuid.UUID | None:
    project = await tenancy.get_project(project_id)
    return project.owner_org_id if project is not None else None


__all__ = ["resolve_owning_org_id"]
