"""A payload schema's declared order, which jsonb storage does not keep."""

from __future__ import annotations

from contexts.activities.application.observation_aggregates import declared_fields
from contexts.activities.domain.property_order import ordered_properties
from contexts.activities.infrastructure.repositories.submission_repo import _declared_properties

# Keys as jsonb returns them: shortest first, whatever the author wrote.
_SCHEMA = {
    "type": "object",
    "properties": {
        "why": {"type": "string", "x-order": 3},
        "idea": {"type": "string", "x-order": 1},
        "extra": {"type": "string"},
        "detail": {"type": "string", "x-order": 2},
    },
}


def test_x_order_wins_and_unordered_properties_follow_in_schema_order() -> None:
    assert [name for name, _ in ordered_properties(_SCHEMA)] == ["idea", "detail", "why", "extra"]


def test_the_export_and_the_coverage_figures_agree_on_the_order() -> None:
    assert _declared_properties(_SCHEMA) == ("idea", "detail", "why", "extra")
    assert [name for name, _ in declared_fields(_SCHEMA)] == ["idea", "detail", "why", "extra"]


def test_a_schema_without_properties_declares_nothing() -> None:
    assert ordered_properties({}) == []
    assert ordered_properties(None) == []
    assert ordered_properties({"properties": []}) == []
