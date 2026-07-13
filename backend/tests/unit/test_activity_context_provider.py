"""ActivityContextProvider — block formatting, coverage gate, best-effort (AC-1,2,4).

The provider is where the observer block is built; these cover the deterministic
formatting, the None-on-empty coverage gate, and the None-on-exception
degradation. Engine-level gating (observer vs non-observer) is covered in
``test_turn_engine_observer_activity.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.activities.application.activity_context_provider import ActivityContextProvider
from contexts.activities.domain.models import RecentActivityRow, ValidationStatus

_FACADE = "contexts.activities.interfaces.facade.ActivitiesFacade"
_NOW = dt.datetime(2026, 7, 13, 10, 30, tzinfo=dt.UTC)


def _row(**over: object) -> RecentActivityRow:
    base = {
        "created_at": _NOW,
        "subject_user_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "attempt_no": 3,
        "type_key": "creativity_probe",
        "validation_status": ValidationStatus.VALIDATED,
        "is_valid": True,
        "error_class": None,
    }
    base.update(over)
    return RecentActivityRow(**base)  # type: ignore[arg-type]


def _facade_returning(rows: list[RecentActivityRow]) -> MagicMock:
    facade = MagicMock()
    facade.list_recent_activity = AsyncMock(return_value=rows)
    return facade


class TestFormatting:
    async def test_block_lists_deterministic_facts(self) -> None:
        rows = [
            _row(attempt_no=1, validation_status=ValidationStatus.VALIDATED, is_valid=True),
            _row(
                attempt_no=2,
                validation_status=ValidationStatus.VALIDATED,
                is_valid=False,
                error_class="off_topic",
            ),
            _row(attempt_no=3, validation_status=ValidationStatus.PENDING, is_valid=None),
            _row(attempt_no=4, validation_status=ValidationStatus.ERROR, is_valid=None),
        ]
        with patch(_FACADE, return_value=_facade_returning(rows)):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert block.startswith("[Recent room activity]\n")
        assert "#1 creativity_probe: valid" in block
        assert "#2 creativity_probe: invalid [off_topic]" in block
        assert "#3 creativity_probe: pending" in block
        assert "#4 creativity_probe: error" in block
        # deterministic subject label + iso timestamp, no LLM inference
        assert "u:11111111" in block
        assert "2026-07-13T10:30" in block

    async def test_passes_limit_through(self) -> None:
        facade = _facade_returning([_row()])
        with patch(_FACADE, return_value=facade):
            await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4(), limit=5)
        assert facade.list_recent_activity.await_args.args[1] == 5


class TestCoverageGateAndFailure:
    async def test_none_when_no_activity(self) -> None:
        with patch(_FACADE, return_value=_facade_returning([])):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())
        assert block is None

    async def test_none_on_exception(self) -> None:
        facade = MagicMock()
        facade.list_recent_activity = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(_FACADE, return_value=facade):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())
        assert block is None
