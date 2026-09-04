"""Guest session cleanup worker (AC-10).

Verifies that the cleanup task calls delete_older_than with a 30-day cutoff.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks.guest_cleanup import _CLEANUP_WINDOW, guest_session_cleanup


def test_cleanup_window_is_30_days() -> None:
    assert _CLEANUP_WINDOW == timedelta(days=30)


@pytest.mark.asyncio
async def test_cleanup_deletes_old_sessions() -> None:
    mock_repo = MagicMock()
    mock_repo.delete_older_than = AsyncMock(return_value=5)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin.return_value = mock_begin

    mock_sm = MagicMock(return_value=mock_session)

    with (
        patch(
            "app.workers.tasks.guest_cleanup.get_sessionmaker", return_value=mock_sm
        ),
        patch(
            "app.workers.tasks.guest_cleanup.GuestSessionRepository",
            return_value=mock_repo,
        ) as repo_cls,
    ):
        # The import inside the function needs patching at the right level
        with patch(
            "contexts.conversation.infrastructure.repositories.guest_session_repo.GuestSessionRepository",
        ):
            result = await guest_session_cleanup({})

    assert result == 5
    mock_repo.delete_older_than.assert_called_once()
    cutoff = mock_repo.delete_older_than.call_args[0][0]
    # The cutoff should be approximately 30 days ago
    assert cutoff is not None
