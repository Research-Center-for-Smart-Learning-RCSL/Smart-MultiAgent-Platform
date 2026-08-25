"""Structured-activities domain errors → RFC 7807 slugs (Chapter §30).

Errors carry a ``code`` only; the HTTP status for each is assigned in
``interfaces/error_mapping.py`` (the shared context-handler convention).
"""

from __future__ import annotations


class ActivitiesError(Exception):
    code: str = "activities/generic"


class ActivityTypeNotFound(ActivitiesError):
    code = "activities/type-not-found"


class SessionNotFound(ActivitiesError):
    code = "activities/session-not-found"


class ActivityActivationNotFound(ActivitiesError):
    code = "activities/activation-not-found"


class ActivityAlreadyActive(ActivitiesError):
    code = "activities/already-active"


class ActivityNotActive(ActivitiesError):
    code = "activities/not-active"


class SubmissionNotFound(ActivitiesError):
    code = "activities/submission-not-found"


class ActivityTypeKeyConflict(ActivitiesError):
    """Another live type in the project already uses this key (409)."""

    code = "activities/type-key-conflict"


class ActivityTypeActive(ActivitiesError):
    """A behavioral edit (schema/validator) was attempted while the type has an
    active activation in some room; rejected to avoid desyncing an in-flight
    activation (409). Metadata-only edits are unaffected."""

    code = "activities/type-active"


class PayloadSchemaInvalid(ActivitiesError):
    """The registered ``payload_schema`` is not a well-formed JSON Schema (422)."""

    code = "activities/payload-schema-invalid"


class ValidatorConfigInvalid(ActivitiesError):
    """The validator configuration is invalid, e.g. an unregistered in-process
    ``validator_id`` (422)."""

    code = "activities/validator-config-invalid"


class ActivityTypeViolatesPolicy(ActivitiesError):
    """A type's governance fields conflict with the platform policy ([R30.30]).

    Carries the offending field so a facilitator who hits this at activation time
    is told what is wrong and who can fix it, rather than a bare forbidden.
    """

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(detail)
        self.field = field


class ActivityPolicyVersionMismatch(ActivitiesError):
    """The policy changed under an admin's edit (optimistic concurrency)."""


class ActivityPolicyInconsistent(ActivitiesError):
    """The submitted policy contradicts itself (e.g. a default above its own cap)."""


class SubmissionPayloadInvalid(ActivitiesError):
    """A submission payload violates its type's ``payload_schema`` (422)."""

    code = "activities/submission-payload-invalid"


class PlatformActivityTypeReadOnly(ActivitiesError):
    """A Project Owner tried to edit or delete a platform-scoped type ([R30.23]).

    403 rather than the 404 the cross-project guards return: the type is
    legitimately visible to this owner (it is in their list once opted in), so
    hiding it would be a lie. What they lack is the capability, and only a
    platform admin has it — which the client needs to be able to say.
    """

    code = "activities/platform-type-read-only"


class ActivityTypeNotOptedIn(ActivitiesError):
    """A project acted on a platform type it has not opted into ([R30.33]).

    Raised only on the project-scoped opt-out route, where the caller is already
    an authenticated Project Owner and the type's existence is not a secret from
    them. The room-level gates deliberately do NOT use this — they raise
    ``ActivityTypeNotFound`` so a probe from a non-opted-in project cannot
    distinguish "exists but not enabled" from "does not exist".
    """

    code = "activities/type-not-opted-in"


class GroupConfigInvalid(ActivitiesError):
    """A type's ``group_config`` is not a usable consent fraction (422).

    Separate from ``ValidatorConfigInvalid`` because the two are read by
    different audiences: a validator config is owner-confidential ([R30.25]),
    while the consent fraction is shown to every person being asked to vote
    against it.
    """

    code = "activities/group-config-invalid"


class ActivityTypeNotGroupSubmittable(ActivitiesError):
    """A group proposal was made against a type carrying no ``group_config`` (409).

    Not a 404: the type is legitimately visible to this participant -- the round
    is running and its schema is on their screen. What it is not is a group task.
    """

    code = "activities/type-not-group-submittable"


class GroupProposalNotFound(ActivitiesError):
    """No such proposal, or none this caller may see (404).

    Collapses "does not exist", "belongs to another room" and "you are not a
    pinned voter" into one answer, the way ``SessionNotFound`` already does for a
    non-subject: a vote record names people, so a non-voter must not be able to
    confirm one exists.
    """

    code = "activities/group-proposal-not-found"


class GroupProposalAlreadyOpen(ActivitiesError):
    """This group already has an open proposal for this round (409).

    Two competing proposals would split the group's votes and neither could
    pass, so at most one is open per (activation, group) -- enforced by a partial
    unique index, which is also what makes a concurrent double-propose land here
    rather than produce two.
    """

    code = "activities/group-proposal-already-open"


class GroupProposalResolved(ActivitiesError):
    """The proposal has already been decided (409).

    The client refetches rather than showing a stale count: the request was
    legal when it was rendered, and what changed is the group's state.
    """

    code = "activities/group-proposal-resolved"


class NotAGroupMember(ActivitiesError):
    """The caller does not belong to the Member Group they acted for (403).

    Distinct from ``GroupProposalNotFound``, and deliberately so: this is raised
    on *creation*, where the caller named the group themselves, so there is
    nothing to conceal. Voting uses the 404 instead, because there the caller
    named a proposal that may be another group's.
    """

    code = "activities/not-a-group-member"


class MemberGroupNotBoundToRoom(ActivitiesError):
    """The Member Group is not a live group of this room's project, or is not
    bound to this room (403).

    One error for both halves on purpose: a group id from another project is
    indistinguishable here from one that exists but was never bound, and telling
    the two apart would confirm the existence of another tenant's group.
    """

    code = "activities/member-group-not-bound"


class ExampleCourseNotFound(ActivitiesError):
    """No shipped course with that key ([R30.32]).

    Distinct from a course file that exists but does not parse: that is a defect
    in the deployed artifact, and reporting it to a client as "not found" would
    send an operator looking in the wrong place. The catalogue's own
    ``CourseFileInvalid`` covers six causes across both fault domains, so only the
    client-fault one is lifted into the domain here; the rest stay a 500.
    """

    code = "activities/example-course-not-found"


__all__ = [
    "ActivitiesError",
    "ActivityActivationNotFound",
    "ActivityAlreadyActive",
    "ActivityNotActive",
    "ActivityPolicyInconsistent",
    "ActivityPolicyVersionMismatch",
    "ActivityTypeActive",
    "ActivityTypeKeyConflict",
    "ActivityTypeNotFound",
    "ActivityTypeNotGroupSubmittable",
    "ActivityTypeNotOptedIn",
    "ActivityTypeViolatesPolicy",
    "ExampleCourseNotFound",
    "GroupConfigInvalid",
    "GroupProposalAlreadyOpen",
    "GroupProposalNotFound",
    "GroupProposalResolved",
    "MemberGroupNotBoundToRoom",
    "NotAGroupMember",
    "PayloadSchemaInvalid",
    "PlatformActivityTypeReadOnly",
    "SessionNotFound",
    "SubmissionNotFound",
    "SubmissionPayloadInvalid",
    "ValidatorConfigInvalid",
]
