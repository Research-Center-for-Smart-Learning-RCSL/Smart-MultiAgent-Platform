"""Room status semantics for current activity rounds ([R33.05])."""

from __future__ import annotations

import datetime as dt

import pytest

from contexts.dashboard.application.read_service import _traffic_light
from contexts.dashboard.domain.models import TrafficLight


@pytest.mark.parametrize(
    ("minutes_ago", "expected"),
    [
        (0, TrafficLight.GREEN),
        (5, TrafficLight.GREEN),
        (5.01, TrafficLight.YELLOW),
        (15, TrafficLight.YELLOW),
        (15.01, TrafficLight.RED),
    ],
)
def test_active_round_status_uses_latest_start_or_valid_submission(
    minutes_ago: float, expected: TrafficLight
) -> None:
    now = dt.datetime(2026, 10, 20, 10, 0, tzinfo=dt.UTC)
    last_valid = now - dt.timedelta(minutes=minutes_ago)
    assert (
        _traffic_light(
            is_active=True,
            started_at=now - dt.timedelta(hours=1),
            last_valid_submission_at=last_valid,
            now_at=now,
        )
        is expected
    )


def test_new_active_round_is_green_without_a_valid_submission() -> None:
    now = dt.datetime(2026, 10, 20, 10, 0, tzinfo=dt.UTC)
    assert (
        _traffic_light(
            is_active=True,
            started_at=now - dt.timedelta(minutes=2),
            last_valid_submission_at=None,
            now_at=now,
        )
        is TrafficLight.GREEN
    )


def test_ended_round_is_inactive_without_alert() -> None:
    now = dt.datetime(2026, 10, 20, 10, 0, tzinfo=dt.UTC)
    assert (
        _traffic_light(
            is_active=False,
            started_at=now - dt.timedelta(hours=1),
            last_valid_submission_at=None,
            now_at=now,
        )
        is TrafficLight.INACTIVE
    )
