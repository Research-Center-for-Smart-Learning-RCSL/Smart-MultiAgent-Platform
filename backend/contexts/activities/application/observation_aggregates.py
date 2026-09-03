"""Room-scoped read models behind an observer's computed presentation blocks ([R28.17]).

WHY THE SERVER COMPUTES THESE AT ALL
------------------------------------
An observer agent chooses *which* figure to show and how to frame it; it never
supplies a number. That split is the whole safety argument for letting a model
arrange a creator-facing analysis: a participant can persuade an agent to include
a coverage figure, but cannot change a value in one, because the model is never
asked for a value. Every aggregate here is read-only and commits nothing.

WHAT THEY MAY NOT CONTAIN ([R28.18])
------------------------------------
Truncated participant codes, owner-authored schema field names, and counts. Never
a display name, never a login email, never a submission value. There is
deliberately no label resolver on this path and no legend: the one that exists for
the ``[Recent room activity]`` block ([R30.38]) is not reachable from here, and the
creator holds the roster.

DENOMINATORS ARE SUBMISSIONS, NOT PEOPLE
----------------------------------------
Every figure reports how many *submissions* it counted. A room has no roster, and
coverage reads only submissions carrying ``filled_fields``, so nothing here can say
what fraction of a class did anything. Callers render that denominator verbatim and
never a rate.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import (
    MAX_COVERAGE_FIELDS,
    ActivityType,
    AttemptSummary,
    FieldCoverage,
    FieldCoverageCell,
    MandalaGrid,
)
from contexts.activities.infrastructure.repositories.submission_repo import (
    ActivitySubmissionRepository,
)

#: A mandala grid is three by three. Stated once, and checked server-side as well
#: as in the tool's enum: an enum keeps a mismatched grid unrepresentable through
#: the model, and this keeps it unrepresentable at all.
MANDALA_SIDE = 3
MANDALA_CELLS = MANDALA_SIDE * MANDALA_SIDE

#: The participant worksheet's centre rule, mirrored from ``MandalaGrid.vue``'s
#: ``CENTER_PROPERTY``/``CENTER_INDEX``. Two files, one rule: a figure drawn on a
#: different layout from the form it describes is a figure about nothing.
MANDALA_CENTRE_PROPERTY = "center"
MANDALA_CENTRE_INDEX = 4


class ObservationAggregateService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = ActivitySubmissionRepository(db)

    async def field_coverage(
        self, *, chatroom_id: uuid.UUID, activity_type: ActivityType
    ) -> FieldCoverage | None:
        """Per-field answer counts, or ``None`` when there is nothing to count.

        ``None`` means one of two things and the caller refuses the block either
        way: the type declares no usable properties, or no submission in this room
        carries ``filled_fields`` — the mid-course upgrade case, where rendering an
        empty chart would assert that nobody answered anything.
        """
        fields = declared_fields(activity_type.payload_schema)
        if not fields:
            return None
        counted, tallies = await self._repo.count_field_fills(
            chatroom_id=chatroom_id,
            activity_type_id=activity_type.id,
            field_names=[name for name, _ in fields],
        )
        if counted == 0:
            return None
        return FieldCoverage(
            type_key=activity_type.key,
            type_name=activity_type.name,
            submissions_counted=counted,
            cells=tuple(
                FieldCoverageCell(name=name, title=title, filled=tallies.get(name, 0))
                for name, title in fields
            ),
        )

    async def mandala_grid(
        self, *, chatroom_id: uuid.UUID, activity_type: ActivityType
    ) -> MandalaGrid | None:
        """:meth:`field_coverage` for a nine-property type, as three rows of three.

        Refuses a type of any other width rather than padding or truncating one:
        a grid whose cells do not correspond to the worksheet's cells is a figure
        about nothing, and silently dropping the tenth field would be exactly that.

        The cells are laid out by :func:`_with_centre_in_the_middle`, not by the
        declared order alone — see there for why.
        """
        coverage = await self.field_coverage(chatroom_id=chatroom_id, activity_type=activity_type)
        if coverage is None or len(coverage.cells) != MANDALA_CELLS:
            return None
        cells = _with_centre_in_the_middle(coverage.cells)
        return MandalaGrid(
            type_key=coverage.type_key,
            type_name=coverage.type_name,
            submissions_counted=coverage.submissions_counted,
            rows=tuple(tuple(cells[i : i + MANDALA_SIDE]) for i in range(0, MANDALA_CELLS, MANDALA_SIDE)),
        )

    async def attempt_summary(
        self,
        *,
        chatroom_id: uuid.UUID,
        activity_type: ActivityType | None,
        limit: int,
    ) -> AttemptSummary | None:
        """One row per participant code, newest activity first, or ``None``.

        With no ``activity_type`` the room's every type is in scope, and
        ``attempts`` is then the highest attempt number the participant reached in
        any *single* session rather than a total across worksheets — attempt
        numbers are per session, and summing them across types would report a
        number that exists nowhere.
        """
        counted, rows, truncated = await self._repo.attempt_summary_rows(
            chatroom_id=chatroom_id,
            activity_type_id=activity_type.id if activity_type else None,
            limit=limit,
        )
        if counted == 0:
            return None
        return AttemptSummary(
            type_key=activity_type.key if activity_type else None,
            type_name=activity_type.name if activity_type else None,
            submissions_counted=counted,
            rows=tuple(rows),
            truncated=truncated,
        )


def _with_centre_in_the_middle(cells: Sequence[FieldCoverageCell]) -> list[FieldCoverageCell]:
    """The nine cells as the participant's own worksheet lays them out.

    ``MandalaGrid.vue`` treats ``center`` as a *named opt-in* to the middle box
    and splices it to index 4 wherever the schema declares it, rendering the
    remaining eight as the ring in declared order ([R30.36]). Reading the declared
    order alone put each count on a cell the participant never saw it on, and in
    this figure position is the entire meaning — the serialiser emits an empty
    header row precisely because the columns have no names.

    A schema naming no ``center`` keeps its declared order untouched, which is the
    other half of the same rule: promoting the first field would move a cell its
    author deliberately put first, and an author cannot see that rule from their
    own schema.
    """
    centre = next((c for c in cells if c.name == MANDALA_CENTRE_PROPERTY), None)
    if centre is None:
        return list(cells)
    ring = [c for c in cells if c is not centre]
    return [*ring[:MANDALA_CENTRE_INDEX], centre, *ring[MANDALA_CENTRE_INDEX:]]


def declared_fields(payload_schema: dict[str, Any]) -> list[tuple[str, str]]:
    """``[(property name, display title)]`` in the schema's declared order.

    ``x-order`` wins where an owner set it; properties without one keep their
    declaration order behind those that have one, so a coverage figure reads down
    the worksheet rather than in some order only the JSON knows about.

    This is the schema's order, not necessarily the participant's: the mandala
    worksheet additionally promotes a property named ``center`` to the middle box,
    which :func:`_with_centre_in_the_middle` applies for the grid alone. It is not
    applied here because this function also serves the flat ``field_coverage``
    table, whose rows are labelled — position carries no meaning there, and a
    type of any other width renders in declared order for the participant too.

    Returns ``[]`` for a schema with no object properties, and for one wider than
    :data:`MAX_COVERAGE_FIELDS` — the query builds one aggregate per field, so the
    bound is on the statement, not merely on the picture.
    """
    properties = payload_schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return []
    if len(properties) > MAX_COVERAGE_FIELDS:
        return []

    def sort_key(item: tuple[int, tuple[str, Any]]) -> tuple[int, float, int]:
        index, (_, spec) = item
        order = spec.get("x-order") if isinstance(spec, dict) else None
        if isinstance(order, bool) or not isinstance(order, int | float):
            return (1, 0.0, index)
        return (0, float(order), index)

    ordered = sorted(enumerate(properties.items()), key=sort_key)
    return [
        (name, str(spec.get("title") or name) if isinstance(spec, dict) else name)
        for _, (name, spec) in ordered
    ]


__all__ = [
    "MANDALA_CELLS",
    "MANDALA_CENTRE_INDEX",
    "MANDALA_CENTRE_PROPERTY",
    "MANDALA_SIDE",
    "ObservationAggregateService",
    "declared_fields",
]
