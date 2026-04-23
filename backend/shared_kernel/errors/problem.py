"""RFC 7807 Problem model + SmapError base exception.

Handlers live in `shared_kernel.errors.handlers` so the pydantic schema has
zero FastAPI dependency and can be imported from the domain layer if needed
for type-only references (guarded by TYPE_CHECKING).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

_PROBLEM_BASE = "https://smap.local/problems"


def problem_type(slug: str) -> str:
    """Build a canonical Problem `type` URL from a slug.

    ``problem_type("rate-limited")`` → ``"https://smap.local/problems/rate-limited"``.
    """
    if not slug or slug.startswith("/") or slug.endswith("/"):
        raise ValueError(f"Invalid problem slug: {slug!r}")
    return f"{_PROBLEM_BASE}/{slug}"


class Problem(BaseModel):
    """RFC 7807 Problem object.

    `type` is always absolute and always under the canonical prefix.
    Additional members (e.g. `field_errors`) flow through `extras` and are
    serialised flat at the top level by the handler.
    """

    type: str
    title: str
    status: int = Field(ge=100, le=599)
    detail: str | None = None
    instance: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    def dump(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        if self.instance is not None:
            body["instance"] = self.instance
        for k, v in self.extras.items():
            if k in body:
                continue  # never let extras overwrite reserved members
            body[k] = v
        return body


class SmapError(Exception):
    """Base exception translated to RFC 7807 by the global handler."""

    def __init__(
        self,
        *,
        type_: str,
        title: str,
        status: int,
        detail: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.problem = Problem(
            type=type_,
            title=title,
            status=status,
            detail=detail,
            extras=extras or {},
        )


class NotImplementedProblem(SmapError):
    """Default body for stub endpoints."""

    def __init__(self, what: str = "This endpoint is not implemented yet.") -> None:
        super().__init__(
            type_=problem_type("not-implemented"),
            title="Not Implemented",
            status=501,
            detail=what,
        )
