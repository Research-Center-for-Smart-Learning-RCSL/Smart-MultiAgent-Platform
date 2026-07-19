"""The graphrag_build_state gauge's label mapping.

Audit M2 fixed a label set that no BuildState mapped to, which made every terminal
build report "idle". The `.get(..., "idle")` default it left behind would have
reintroduced exactly that for any state added later -- including
`recovery_unavailable` (2026-07-17-graphrag-reset-expired-recovery, AC-11), whose whole
point is that the config is NOT healthy.
"""

from __future__ import annotations

import logging

import pytest

from app.workers.tasks.graphrag import (
    BUILD_STATE_METRIC_LABELS,
    build_state_metric_label,
)
from contexts.knowledge.domain.graphrag import BuildState


def test_recovery_unavailable_has_its_own_label() -> None:
    assert build_state_metric_label(BuildState.RECOVERY_UNAVAILABLE.value) == "recovery_unavailable"
    assert "recovery_unavailable" in BUILD_STATE_METRIC_LABELS


@pytest.mark.parametrize(
    ("state", "label"),
    [
        (BuildState.IDLE, "idle"),
        (BuildState.FAILED, "failed"),
        (BuildState.FAILED_COMPENSATING, "compensating"),
    ],
)
def test_known_terminal_states_keep_their_labels(state: BuildState, label: str) -> None:
    assert build_state_metric_label(state.value) == label
    assert label in BUILD_STATE_METRIC_LABELS


def test_unmapped_state_reports_failed_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Never "idle": an unmapped state must not read as a healthy config."""
    with caplog.at_level(logging.ERROR):
        assert build_state_metric_label("some_future_state") == "failed"
    assert "no metric label" in caplog.text
