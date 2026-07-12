"""Guarded soft-delete restore: discriminate the natural-key collision.

A restore clears ``deleted_at``, re-entering the row into every
``WHERE deleted_at IS NULL`` partial-unique index on that table. If a live row
has since claimed the same natural key (name/email), the UPDATE raises
``IntegrityError``. :func:`raise_restore_conflict` tells that *specific*
collision apart from every other integrity fault by constraint name — the same
discrimination discipline as ``AgentGroupRepository.create_group`` — so the HTTP
boundary can map it to a clean 409 while unrelated errors surface as their real
cause instead of a misleading conflict.
"""

from __future__ import annotations

from typing import NoReturn

from sqlalchemy.exc import IntegrityError


class RestoreConflict(Exception):
    """A soft-deleted resource cannot be restored because its unique natural key
    (name/email) is now held by a live resource. Mapped to HTTP 409 at the route.
    """

    def __init__(self, resource_type: str) -> None:
        self.resource_type = resource_type
        super().__init__(f"cannot restore {resource_type}: unique key already in use")


def raise_restore_conflict(
    exc: IntegrityError,
    *,
    unique_constraint: str | tuple[str, ...],
    resource_type: str,
) -> NoReturn:
    """Re-raise *exc* as :class:`RestoreConflict` iff it violated one of the
    resource's active partial-unique indexes; otherwise re-raise it unchanged.

    Call from a restore's ``except IntegrityError`` block. *unique_constraint*
    is the index name (or names, for a resource with several — e.g. projects'
    per-user and per-org variants) whose violation means "the natural key is
    already taken by a live row".
    """
    names = (unique_constraint,) if isinstance(unique_constraint, str) else unique_constraint
    message = str(exc.orig or exc).lower()
    if any(name in message for name in names):
        raise RestoreConflict(resource_type) from exc
    raise exc


__all__ = ["RestoreConflict", "raise_restore_conflict"]
