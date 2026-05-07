"""Tenancy domain errors → RFC 7807 registration."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.tenancy.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

_MAP: dict[type[errors.TenancyError], tuple[str, int, str]] = {
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


async def _handler(request: Request, exc: errors.TenancyError) -> JSONResponse:
    slug, status, title = _MAP.get(
        type(exc),
        ("tenancy/generic", 400, "Tenancy error"),
    )
    problem = Problem(
        type=problem_type(slug),
        title=title,
        status=status,
        detail=str(exc),
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.TenancyError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
