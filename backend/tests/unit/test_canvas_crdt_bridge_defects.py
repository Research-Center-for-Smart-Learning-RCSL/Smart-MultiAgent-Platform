"""Regression tests for the canvas CRDT bridge defects (spec 2026-09-14).

Covers B-1 (payload key), B-4 (agent attribution), B-7 (single broadcast site),
B-8 (single element factory), B-9 (incremental delta), and B-10 (MinIO proxy URL).
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from contexts.canvas.application.crdt_relay import CrdtRelay, build_element_dict
from contexts.canvas.domain.models import CanvasObject, CanvasObjectKind
from shared_kernel.storage.minio_client import ObjectStat
from tests.unit.file_ticket_support import decode


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


@pytest.mark.usefixtures("ticket_signer")
class TestCanvasImageLink:
    """B-2: the image URL a browser receives points at the backend, not MinIO.

    The earlier `/minio-assets/` rewrite reached an nginx location that cannot
    resolve MinIO (browser-file-delivery §5), so the link is now a ticket.
    """

    async def test_image_link_is_a_ticket_for_the_upload(self) -> None:
        from app.api.v1.canvas import _image_link

        url = await _image_link(key="proj/room/obj.png", content_type="image/png")
        ticket = await decode(url)
        assert (ticket.bucket, ticket.key) == ("chat-uploads", "proj/room/obj.png")
        assert ticket.content_type == "image/png"
        # Fetched into a blob, so never rendered; an SVG opened directly must not render.
        assert ticket.disposition.startswith("attachment")


def _image(canvas_id: uuid.UUID, **overrides: object) -> CanvasObject:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "canvas_id": canvas_id,
        "kind": CanvasObjectKind.IMAGE,
        "position_x": 0,
        "position_y": 0,
        "width": 1,
        "height": 1,
        "z_index": 0,
        "created_at": datetime(2026, 10, 11, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 11, tzinfo=UTC),
        "minio_path": "proj/canvas/obj/pic.png",
    }
    fields.update(overrides)
    return CanvasObject(**fields)


class TestGetImage:
    """The per-client link endpoint resolves only this canvas's stored images."""

    async def _get(self, obj: CanvasObject | None, canvas_id: uuid.UUID) -> CanvasObject | None:
        from contexts.canvas.application.canvas_service import CanvasService

        service = object.__new__(CanvasService)
        service._repo = MagicMock(get_object=AsyncMock(return_value=obj))
        return await service.get_image(canvas_id=canvas_id, object_id=uuid.uuid4())

    async def test_image_of_this_canvas(self) -> None:
        canvas_id = uuid.uuid4()
        obj = _image(canvas_id)
        assert await self._get(obj, canvas_id) is obj

    async def test_image_of_another_canvas_is_none(self) -> None:
        assert await self._get(_image(uuid.uuid4()), uuid.uuid4()) is None

    async def test_created_object_can_take_the_broadcast_file_id(self) -> None:
        """The `canvas.image_added` event carries only `fileId`; the row must
        answer to it, or the per-client link lookup 404s."""
        from contexts.canvas.application.canvas_service import CanvasService

        file_id, canvas_id = uuid.uuid4(), uuid.uuid4()
        service = object.__new__(CanvasService)
        service._repo = MagicMock(create_object=AsyncMock(return_value=_image(canvas_id, id=file_id)))
        service._db = MagicMock()
        service._room_channel_fn = lambda chatroom_id: "room"
        with (
            patch("contexts.canvas.application.canvas_service.audit.emit", AsyncMock()),
            patch("contexts.canvas.application.canvas_service.Publisher") as pub,
        ):
            pub.return_value.emit = AsyncMock()
            await service.create_object(
                object_id=file_id,
                canvas_id=canvas_id,
                chatroom_id=uuid.uuid4(),
                kind=CanvasObjectKind.IMAGE,
                position_x=0,
                position_y=0,
                width=1,
                height=1,
            )
        assert service._repo.create_object.await_args.kwargs["values"]["id"] == file_id

    async def test_non_image_or_unstored_is_none(self) -> None:
        canvas_id = uuid.uuid4()
        assert await self._get(_image(canvas_id, kind=CanvasObjectKind.NOTE), canvas_id) is None
        assert await self._get(_image(canvas_id, minio_path=None), canvas_id) is None


@pytest.mark.usefixtures("ticket_signer")
class TestImageLinkRoute:
    """A busy room must not share one link: `/api/files` limits each link to a
    few parallel streams, so every client mints its own."""

    @pytest.fixture
    def route(self, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
        import app.api.v1.canvas as canvas_route

        canvas_id = uuid.uuid4()
        state = SimpleNamespace(image=_image(canvas_id), readable=True, module=canvas_route)

        class _Facade:
            def __init__(self, db: object, **_: object) -> None:
                pass

            async def get_by_chatroom(self, chatroom_id: uuid.UUID) -> SimpleNamespace:
                return SimpleNamespace(id=canvas_id)

            async def get_image(self, *, canvas_id: uuid.UUID, object_id: uuid.UUID) -> CanvasObject | None:
                return state.image

        async def _access(db: object, *, principal: object, chatroom_id: uuid.UUID) -> object:
            return object()

        def _can_read(access: object, *, is_admin: bool) -> None:
            if not state.readable:
                raise HTTPException(status_code=404)

        minio = MagicMock(chat_uploads_bucket="chat-uploads")
        minio.stat = AsyncMock(return_value=ObjectStat(size=3, etag="e", content_type="image/png"))
        monkeypatch.setattr(canvas_route, "CanvasFacade", _Facade)
        monkeypatch.setattr(canvas_route, "resolve_room_access", _access)
        monkeypatch.setattr(canvas_route, "ensure_can_read", _can_read)
        monkeypatch.setattr(canvas_route, "get_minio_client", lambda: minio)
        return state

    async def _call(self, state: SimpleNamespace) -> str:
        principal = MagicMock(is_admin=False)
        out = await state.module.get_image_link(uuid.uuid4(), uuid.uuid4(), principal=principal, db=object())
        return str(out.url)

    async def test_each_call_mints_a_distinct_link(self, route: SimpleNamespace) -> None:
        first, second = await self._call(route), await self._call(route)
        assert first != second
        ticket = await decode(first)
        assert (ticket.key, ticket.content_type) == ("proj/canvas/obj/pic.png", "image/png")

    async def test_reader_check_applies(self, route: SimpleNamespace) -> None:
        route.readable = False
        with pytest.raises(HTTPException):
            await self._call(route)

    async def test_unknown_image_is_404(self, route: SimpleNamespace) -> None:
        route.image = None
        with pytest.raises(HTTPException) as exc:
            await self._call(route)
        assert exc.value.status_code == 404
