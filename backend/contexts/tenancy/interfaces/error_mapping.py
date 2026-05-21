"""Tenancy domain errors → RFC 7807 registration.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3).
"""

from __future__ import annotations

from fastapi import FastAPI

from contexts.tenancy.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.OrgNotFound: ("tenancy/org-not-found", 404, "Org not found"),
    errors.ProjectNotFound: ("tenancy/project-not-found", 404, "Project not found"),
    errors.MemberNotFound: ("tenancy/member-not-found", 404, "Member not found"),
    errors.OriginalCreatorConflict: (
        "tenancy/original-creator-conflict",
        409,
        "Original Creator conflict",
    ),
    errors.TransferConflict: ("tenancy/transfer-conflict", 409, "Transfer conflict"),
    errors.TransferNotFound: ("tenancy/transfer-not-found", 404, "Transfer not found"),
    errors.InviteNotFound: ("invites/not-found", 404, "Invite not found"),
    errors.InviteExpired: ("invites/expired", 410, "Invite expired"),
    errors.InviteDuplicate: ("invites/duplicate", 409, "Pending invite exists"),
    errors.InviteNotForCaller: ("invites/not-for-caller", 403, "Invite not for caller"),
    errors.NameTaken: ("tenancy/name-taken", 409, "Name already in use"),
    errors.VersionMismatch: ("tenancy/version-mismatch", 412, "Version mismatch (If-Match)"),
    errors.ProjectOwnerRequired: (
        "tenancy/project-owner-required",
        422,
        "Project must have an owner",
    ),
    errors.CannotMigrateIndividualProject: (
        "tenancy/cannot-migrate-individual-project",
        409,
        "Individual project cannot be moved into an Org",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.TenancyError, _MAP)


__all__ = ["register"]
