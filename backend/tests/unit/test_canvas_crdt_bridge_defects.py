"""Regression tests for the canvas CRDT bridge defects (spec 2026-09-14).

Covers B-1 (payload key), B-4 (agent attribution), B-7 (single broadcast site),
B-8 (single element factory), B-9 (incremental delta), and B-10 (MinIO proxy URL).
"""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.canvas.application.crdt_relay import CrdtRelay, build_element_dict


class TestBuildElementDict:
    """B-8: element factory produces correct defaults for each kind."""

    def test_shape_defaults(self) -> None:
        elem = build_element_dict("note", elem_id="e1", x=10, y=20, width=100, height=80)
        assert elem["type"] == "rectangle"
        assert elem["strokeColor"] == "#1e1e1e"
        assert elem["strokeWidth"] == 2
        assert elem["roughness"] == 1
        assert elem["isDeleted"] is False
        assert "fileId" not in elem

    def test_image_defaults(self) -> None:
        elem = build_element_dict("image", elem_id="img-1", image=True)
        assert elem["type"] == "image"
        assert elem["strokeColor"] == "transparent"
        assert elem["strokeWidth"] == 0
        assert elem["roughness"] == 0
        assert elem["fileId"] == "img-1"
        assert elem["status"] == "saved"

    def test_text_element_has_font_props(self) -> None:
        elem = build_element_dict("text", content="hello")
        assert elem["type"] == "text"
        assert elem["fontSize"] == 20
        assert elem["fontFamily"] == 1
        assert elem["text"] == "hello"

    def test_style_overrides(self) -> None:
        elem = build_element_dict(
            "shape",
            style={"backgroundColor": "red", "strokeColor": "blue"},
        )
        assert elem["backgroundColor"] == "red"
        assert elem["strokeColor"] == "blue"

    def test_custom_data(self) -> None:
        elem = build_element_dict(
            "shape",
            custom_data={"createdByAgentId": "agent-123"},
        )
        assert elem["customData"] == {"createdByAgentId": "agent-123"}

    def test_no_custom_data_by_default(self) -> None:
        elem = build_element_dict("shape")
        assert "customData" not in elem


class TestIncrementalDelta:
    """B-9: relay returns incremental delta smaller than full state."""

    async def test_inject_delta_smaller_than_full(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        # Build a doc with pre-existing content
        for i in range(10):
            await relay.inject_elements(
                canvas_id,
                [{"id": f"pre-{i}", "type": "rect", "text": f"content-{i}" * 50}],
            )
        # Now add one more element
        full_state, delta = await relay.inject_elements(
            canvas_id,
            [{"id": "new-elem", "type": "rect", "text": "small"}],
        )
        assert len(delta) < len(full_state)

    async def test_update_delta_smaller_than_full(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        for i in range(10):
            await relay.inject_elements(
                canvas_id,
                [{"id": f"e-{i}", "type": "rect", "text": f"content-{i}" * 50, "isDeleted": False}],
            )
        full_state, delta = await relay.update_element(canvas_id, "e-0", {"text": "updated"})
        assert len(delta) < len(full_state)

    async def test_delete_delta_smaller_than_full(self) -> None:
        relay = CrdtRelay()
        canvas_id = uuid.uuid4()
        for i in range(10):
            await relay.inject_elements(
                canvas_id,
                [{"id": f"e-{i}", "type": "rect", "text": f"content-{i}" * 50, "isDeleted": False}],
            )
        full_state, delta = await relay.delete_element(canvas_id, "e-0")
        assert len(delta) < len(full_state)


class TestPayloadKeyConsistency:
    """B-1: persist_and_broadcast_crdt always uses key 'data'."""

    @pytest.mark.asyncio
    async def test_broadcast_uses_data_key(self) -> None:
        from contexts.canvas.interfaces.facade import CanvasFacade

        db = AsyncMock()
        db.info = {}
        db.sync_session = MagicMock()
        facade = CanvasFacade.__new__(CanvasFacade)
        facade._db = db

        mock_service = MagicMock()
        mock_service.update_crdt_state = AsyncMock()
        mock_service.sync_text_from_crdt = AsyncMock()
        facade._service = mock_service

        captured: dict | None = None

        async def capture_emit(event_type: str, data: dict | None = None) -> int:
            nonlocal captured
            captured = data
            return 1

        with patch("contexts.canvas.interfaces.facade.Publisher") as mock_pub_cls:
            mock_pub = MagicMock()
            mock_pub.emit = AsyncMock(side_effect=capture_emit)
            mock_pub_cls.return_value = mock_pub
            await facade.persist_and_broadcast_crdt(
                uuid.uuid4(),
                full_state=b"\x00\x01",
                delta=b"\x02\x03",
                deferred=False,
            )
        assert captured is not None
        assert "data" in captured
        assert "update" not in captured
        expected_b64 = base64.b64encode(b"\x02\x03").decode("ascii")
        assert captured["data"] == expected_b64


class TestDeferredBroadcast:
    """B-3: deferred=True enqueues broadcast instead of emitting immediately."""

    @pytest.mark.asyncio
    async def test_deferred_does_not_emit_immediately(self) -> None:
        from contexts.canvas.interfaces.facade import CanvasFacade

        db = AsyncMock()
        db.info = {}
        db.sync_session = MagicMock()
        facade = CanvasFacade.__new__(CanvasFacade)
        facade._db = db

        mock_service = MagicMock()
        mock_service.update_crdt_state = AsyncMock()
        mock_service.sync_text_from_crdt = AsyncMock()
        facade._service = mock_service

        with patch("contexts.canvas.interfaces.facade.Publisher") as mock_pub_cls:
            mock_pub = MagicMock()
            mock_pub.emit = AsyncMock()
            mock_pub_cls.return_value = mock_pub
            await facade.persist_and_broadcast_crdt(
                uuid.uuid4(),
                full_state=b"\x00",
                delta=b"\x01",
                deferred=True,
            )
            mock_pub.emit.assert_not_awaited()

        pending = db.info.get("_pending_crdt_broadcasts", [])
        assert len(pending) == 1
        assert "data" in pending[0][1]


class TestMinioUrlProxy:
    """B-2: _minio_url_to_proxy strips internal hostname."""

    def test_rewrites_minio_url(self) -> None:
        from app.api.v1.canvas import _minio_url_to_proxy

        url = "http://minio:9000/chat-uploads/proj/room/img.png?X-Amz-Signature=abc"
        result = _minio_url_to_proxy(url)
        assert result.startswith("/minio-assets/")
        assert "minio:9000" not in result
        assert "chat-uploads/proj/room/img.png" in result
        assert "X-Amz-Signature=abc" in result

    def test_no_query_string(self) -> None:
        from app.api.v1.canvas import _minio_url_to_proxy

        url = "http://minio:9000/bucket/key"
        result = _minio_url_to_proxy(url)
        assert result == "/minio-assets/bucket/key"
