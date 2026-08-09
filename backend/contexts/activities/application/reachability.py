"""The one place that decides whether a project may use an activity type.

Before platform scope existed this rule was re-typed at seven call sites as
``activity_type.project_id != project_id``. Three of those are room-level paths
that take an ``activity_type_id`` straight from the client body
(``activity-activations``, ``activity-sessions``, ``activity-submissions``), so
each is independently load-bearing for tenant isolation ([R30.09]) — and seven
copies of a rule is how one of them drifts.

The split is deliberate: the *decision* is a pure predicate on the domain model
(``ActivityType.is_visible_to``), and this module supplies the one fact that
predicate cannot compute for itself — whether an opt-in row exists ([R30.33]).

Filtering a listing is not a substitute for calling this. ``list_for_project``
shapes what a picker shows; it cannot bound what an id in a request body reaches.
"""

from __future__ import annotations

import uuid

from contexts.activities.application.ports import ActivityTypeOptInReader, ActivityTypeReader
from contexts.activities.domain.errors import ActivityTypeNotFound
from contexts.activities.domain.models import ActivityType, ActivityTypeScope


async def resolve_reachable_type(
    *,
    type_reader: ActivityTypeReader,
    optin_reader: ActivityTypeOptInReader,
    activity_type_id: uuid.UUID,
    project_id: uuid.UUID,
) -> ActivityType:
    """The live type ``project_id`` may use, or ``ActivityTypeNotFound``.

    Missing, soft-deleted, another project's, and a platform type this project has
    not enabled all collapse into the same 404 on purpose: a distinguishable
    refusal would let a caller in any org enumerate another tenant's type ids, and
    "exists but not enabled for you" is exactly the fact worth not confirming.

    The opt-in table is read only for platform-scoped rows, so the common path
    costs no extra query.
    """
    activity_type = await type_reader.get(activity_type_id)
    if activity_type is None:
        raise ActivityTypeNotFound(str(activity_type_id))

    opted_in = activity_type.scope is ActivityTypeScope.PLATFORM and await optin_reader.exists(
        project_id=project_id, activity_type_id=activity_type_id
    )
    if not activity_type.is_visible_to(project_id, opted_in=opted_in):
        raise ActivityTypeNotFound(str(activity_type_id))
    return activity_type


__all__ = ["resolve_reachable_type"]
