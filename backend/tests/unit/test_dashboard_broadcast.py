"""Project dashboard frames are project-wide invalidations, not room facts ([R33.03])."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.interfaces import broadcast


@pytest.mark.parametrize(
    "dispatch",
    [
        broadcast.dispatch_dashboard_activation_changed,
        broadcast.dispatch_dashboard_submission_validated,
    ],
)
@pytest.mark.asyncio
async def test_project_dashboard_broadcast_does_not_expose_room_metadata(dispatch) -> None:
    project_id = uuid.uuid4()
    room_id = uuid.uuid4()
    publisher = MagicMock()
    publisher.emit = AsyncMock()
    with (
        patch.object(broadcast, "_resolve_project_id", new=AsyncMock(return_value=project_id)),
        patch.object(broadcast, "Publisher", return_value=publisher),
    ):
        await dispatch(room_id)

    publisher.emit.assert_awaited_once()
    event_name, payload = publisher.emit.await_args.args
    assert event_name == "dashboard.invalidated"
    assert payload == {}
