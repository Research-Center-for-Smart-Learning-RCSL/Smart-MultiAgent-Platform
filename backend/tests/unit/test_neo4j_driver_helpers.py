"""Unit tests for pure helpers in the Neo4j driver adapter."""

from __future__ import annotations

import uuid

from contexts.knowledge.infrastructure.neo4j_driver import _collapse_config_rows


def test_collapse_dedups_mixed_project_id_to_one_entry_preferring_non_null() -> None:
    cid = uuid.uuid4()
    pid = uuid.uuid4()
    # A config whose entity nodes mix NULL and set project_id yields two DISTINCT
    # (cid, pid) rows; the sweep must see exactly one entry, with the real pid.
    rows = [(str(cid), None), (str(cid), str(pid))]

    collapsed = _collapse_config_rows(rows)

    assert collapsed == [(cid, pid)]


def test_collapse_prefers_non_null_regardless_of_row_order() -> None:
    cid = uuid.uuid4()
    pid = uuid.uuid4()
    collapsed = _collapse_config_rows([(str(cid), str(pid)), (str(cid), None)])
    assert collapsed == [(cid, pid)]


def test_collapse_keeps_null_when_no_project_id_ever_present() -> None:
    cid = uuid.uuid4()
    assert _collapse_config_rows([(str(cid), None)]) == [(cid, None)]


def test_collapse_skips_rows_with_no_config_id() -> None:
    cid = uuid.uuid4()
    assert _collapse_config_rows([(None, None), (str(cid), None)]) == [(cid, None)]


def test_collapse_keeps_distinct_configs_separate() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    pa, pb = uuid.uuid4(), uuid.uuid4()
    collapsed = dict(_collapse_config_rows([(str(a), str(pa)), (str(b), str(pb))]))
    assert collapsed == {a: pa, b: pb}
