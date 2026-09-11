"""Unit tests for contexts.canvas.application.canvas_context_provider.

The provider is best-effort: it returns a formatted block or None, never
raises. Mocks CanvasRepository and CanvasService to avoid DB dependency.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from contexts.canvas.application.canvas_context_provider import CanvasContextProvider
from contexts.canvas.domain.models import Canvas

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_ROOM = uuid.uuid4()
_CANVAS_ID = uuid.uuid4()


def _canvas(expose: bool = True) -> Canvas:
    return Canvas(
        id=_CANVAS_ID,
        chatroom_id=_ROOM,
        expose_to_agents=expose,
        created_at=_NOW,
    )


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock()


class TestReturnsNoneWhenNoCanvas:
    async def test_no_canvas_row(self, mock_db: AsyncMock) -> None:
        with patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo:
            MockRepo.return_value.get_by_chatroom = AsyncMock(return_value=None)
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)
        assert result is None


class TestReturnsNoneWhenNotExposed:
    async def test_expose_false(self, mock_db: AsyncMock) -> None:
        with patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo:
            MockRepo.return_value.get_by_chatroom = AsyncMock(return_value=_canvas(expose=False))
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)
        assert result is None


class TestReturnsFormattedBlock:
    async def test_with_digest(self, mock_db: AsyncMock) -> None:
        with (
            patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo,
            patch("contexts.canvas.application.canvas_context_provider.CanvasService") as MockService,
        ):
            MockRepo.return_value.get_by_chatroom = AsyncMock(return_value=_canvas(expose=True))
            MockService.return_value.latest_digest = AsyncMock(
                return_value='The canvas contains 1 sticky note.\n- Sticky note: "hello"'
            )
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)

        assert result is not None
        assert result.startswith("[Canvas content]")
        assert "sticky note" in result

    async def test_empty_digest_returns_none(self, mock_db: AsyncMock) -> None:
        with (
            patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo,
            patch("contexts.canvas.application.canvas_context_provider.CanvasService") as MockService,
        ):
            MockRepo.return_value.get_by_chatroom = AsyncMock(return_value=_canvas(expose=True))
            MockService.return_value.latest_digest = AsyncMock(return_value=None)
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)
        assert result is None


class TestDigestCapping:
    async def test_digest_capped_at_2000_chars(self, mock_db: AsyncMock) -> None:
        long_digest = "x" * 3000
        with (
            patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo,
            patch("contexts.canvas.application.canvas_context_provider.CanvasService") as MockService,
        ):
            MockRepo.return_value.get_by_chatroom = AsyncMock(return_value=_canvas(expose=True))
            MockService.return_value.latest_digest = AsyncMock(return_value=long_digest)
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)

        assert result is not None
        content_after_header = result.split("\n", 1)[1]
        assert len(content_after_header) <= 2000


class TestCrdtFallbackChain:
    """AC-9: CanvasContextProvider returns digest from CRDT, not just snapshots."""

    async def test_uses_in_memory_crdt(self, mock_db: AsyncMock) -> None:
        """When CrdtRelay has the doc in memory, digest comes from it."""
        import pycrdt

        from contexts.canvas.application.crdt_relay import CrdtRelay

        relay = CrdtRelay()
        await relay.get_or_load(
            _CANVAS_ID,
            legacy_objects=[],
        )
        # Manually insert an element into the in-memory doc
        doc = relay._docs[_CANVAS_ID].doc
        elements = doc.get("elements", type=pycrdt.Array)
        elements.append(
            pycrdt.Map(
                {
                    "id": str(uuid.uuid4()),
                    "type": "rectangle",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                    "isDeleted": False,
                }
            )
        )

        with (
            patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo,
            patch("contexts.canvas.application.canvas_context_provider.get_crdt_relay", return_value=relay),
        ):
            MockRepo.return_value.get_by_chatroom = AsyncMock(return_value=_canvas(expose=True))
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)

        assert result is not None
        assert "[Canvas content]" in result
        relay.evict(_CANVAS_ID)

    async def test_uses_persisted_crdt_state(self, mock_db: AsyncMock) -> None:
        """When no in-memory doc but crdt_state exists, digest comes from it."""
        import pycrdt

        src = pycrdt.Doc()
        elems = src.get("elements", type=pycrdt.Array)
        elems.append(
            pycrdt.Map(
                {
                    "id": str(uuid.uuid4()),
                    "type": "text",
                    "text": "hello world",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 50,
                    "isDeleted": False,
                }
            )
        )
        crdt_bytes = src.get_update()

        canvas_with_crdt = Canvas(
            id=_CANVAS_ID,
            chatroom_id=_ROOM,
            expose_to_agents=True,
            created_at=_NOW,
            crdt_state=crdt_bytes,
        )

        from contexts.canvas.application.crdt_relay import CrdtRelay

        empty_relay = CrdtRelay()

        with (
            patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo,
            patch(
                "contexts.canvas.application.canvas_context_provider.get_crdt_relay", return_value=empty_relay
            ),
        ):
            MockRepo.return_value.get_by_chatroom = AsyncMock(return_value=canvas_with_crdt)
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)

        assert result is not None
        assert "[Canvas content]" in result
        assert "text" in result.lower()


class TestBestEffort:
    async def test_exception_returns_none(self, mock_db: AsyncMock) -> None:
        with patch("contexts.canvas.application.canvas_context_provider.CanvasRepository") as MockRepo:
            MockRepo.return_value.get_by_chatroom = AsyncMock(side_effect=RuntimeError("db down"))
            result = await CanvasContextProvider(mock_db).query(chatroom_id=_ROOM)
        assert result is None
