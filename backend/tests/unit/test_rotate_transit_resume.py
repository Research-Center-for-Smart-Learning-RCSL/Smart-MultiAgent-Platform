"""M.5.1 — rotate-transit resume-cursor decision (D.10 / §7.6).

Regression guard for the second-rotation skip bug: when a new rotation starts
(a different Transit target version), the per-table resume cursor MUST reset to
the table head. The previous behaviour kept the prior rotation's ``last_id``
(== the table's max id), so the next rotation's ``id > last_id`` filter matched
zero rows and every DEK was silently left at the old version — a data-loss-class
failure once ``min_decryption_version`` is later raised.
"""

from __future__ import annotations

from dataclasses import dataclass

from smap.rotation.rotate_transit import _resume_cursor


@dataclass
class _Progress:
    target_transit_version: int
    last_id: int | None
    rows_rewrapped: int


def test_no_existing_row_starts_fresh() -> None:
    # resume=False → caller does the fresh-start upsert (cursor at the head).
    assert _resume_cursor(None, target_version=2) == (None, 0, False)


def test_new_rotation_resets_cursor() -> None:
    # Rotation 1 → v2 completed, cursor parked at the table's max id (999).
    existing = _Progress(target_transit_version=2, last_id=999, rows_rewrapped=500)
    # Rotation 2 → v3 must restart at the head (resume=False, last_id=None),
    # NOT inherit last_id=999 (which would skip every row).
    assert _resume_cursor(existing, target_version=3) == (None, 0, False)


def test_same_rotation_resumes_from_checkpoint() -> None:
    existing = _Progress(target_transit_version=3, last_id=420, rows_rewrapped=42)
    assert _resume_cursor(existing, target_version=3) == (420, 42, True)
