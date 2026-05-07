"""Tenancy domain errors mapped to RFC 7807 problem slugs by routers."""

from __future__ import annotations


class TenancyError(Exception):
    code: str = "tenancy.generic"


class OrgNotFound(TenancyError):
    code = "tenancy.org-not-found"


class ProjectNotFound(TenancyError):
    code = "tenancy.project-not-found"


class MemberNotFound(TenancyError):
    code = "tenancy.member-not-found"


class OriginalCreatorConflict(TenancyError):
    """Attempt to demote/kick the OC, or self-delete-blocked scenarios."""

    code = "tenancy/original-creator-conflict"


class TransferConflict(TenancyError):
    """409 — another pending transfer exists, or target is not an Owner."""

    code = "tenancy/transfer-conflict"


class TransferNotFound(TenancyError):
    code = "tenancy.transfer-not-found"


class InviteNotFound(TenancyError):
    code = "invites/not-found"


class InviteExpired(TenancyError):
    code = "invites/expired"


class InviteDuplicate(TenancyError):
    code = "invites/duplicate"


class InviteNotForCaller(TenancyError):
    code = "invites/not-for-caller"


class NameTaken(TenancyError):
    code = "tenancy.name-taken"


class VersionMismatch(TenancyError):
    code = "tenancy.version-mismatch"


class ProjectOwnerRequired(TenancyError):
    code = "tenancy.project-owner-required"


class CannotMigrateIndividualProject(TenancyError):
    code = "tenancy.cannot-migrate-individual-project"


__all__ = [
    "CannotMigrateIndividualProject",
    "InviteDuplicate",
    "InviteExpired",
    "InviteNotFound",
    "InviteNotForCaller",
    "MemberNotFound",
    "NameTaken",
    "OrgNotFound",
    "OriginalCreatorConflict",
    "ProjectNotFound",
    "ProjectOwnerRequired",
    "TenancyError",
    "TransferConflict",
    "TransferNotFound",
    "VersionMismatch",
]
