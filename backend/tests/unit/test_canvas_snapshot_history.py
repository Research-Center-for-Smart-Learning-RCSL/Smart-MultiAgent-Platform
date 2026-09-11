"""Unit tests for canvas snapshot history: pruning, restore, label, get-by-id.

Tests the service layer (CanvasService) with a mocked CanvasRepository and
the route-level _sanitize_label helper.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.canvas.application.canvas_service import CanvasService
from contexts.canvas.domain.models import CanvasObject, CanvasObjectKind, CanvasSnapshot

_NOW = datetime(2026, 9, 10, tzinfo=UTC)
_CANVAS_ID = uuid.uuid4()
_CHATROOM_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _make_snapshot(
    *,
    canvas_id: uuid.UUID = _CANVAS_ID,
    snapshot_data: dict[str, Any] | None = None,
    label: str | None = None,
    created_at: datetime = _NOW,
) -> CanvasSnapshot:
    return CanvasSnapshot(
        id=uuid.uuid4(),
        canvas_id=canvas_id,
        snapshot_data=snapshot_data or {"objects": []},
        agent_digest="1 note",
        created_by_user_id=_USER_ID,
        label=label,
        created_at=created_at,
    )


def _make_object(canvas_id: uuid.UUID = _CANVAS_ID) -> CanvasObject:
    return CanvasObject(
        id=uuid.uuid4(),
        canvas_id=canvas_id,
        kind=CanvasObjectKind.NOTE,
        position_x=10.0,
        position_y=20.0,
        width=100.0,
        height=100.0,
        z_index=0,
        content="hello",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _mock_audit() -> MagicMock:
    m = MagicMock()
    m.emit = AsyncMock()
    m.AuditEvent = MagicMock()
    return m


def _mock_publisher() -> MagicMock:
    pub_instance = MagicMock()
    pub_instance.emit = AsyncMock()
    pub_cls = MagicMock(return_value=pub_instance)
    return pub_cls


def _mock_service() -> tuple[CanvasService, MagicMock]:
    db = AsyncMock()
    svc = CanvasService(db, room_channel_fn=lambda cid: f"ws:room:{cid}")
    repo = MagicMock()
    svc._repo = repo  # type: ignore[attr-defined]
    return svc, repo


def _relay_patch():
    return patch(
        "contexts.canvas.application.crdt_relay.get_crdt_relay",
        return_value=MagicMock(has=MagicMock(return_value=False)),
    )


# ---------------------------------------------------------------------------
# Snapshot pruning (AC-7)
# ---------------------------------------------------------------------------


class TestSnapshotPruning:
    @pytest.mark.anyio
    async def test_prune_oldest_when_at_cap(self) -> None:
        svc, repo = _mock_service()
        repo.count_snapshots = AsyncMock(return_value=50)
        repo.delete_oldest_snapshot = AsyncMock()
        repo.list_objects = AsyncMock(return_value=[])
        new_snap = _make_snapshot()
        repo.create_snapshot = AsyncMock(return_value=new_snap)

        with (
            patch("contexts.canvas.application.canvas_service.audit", _mock_audit()),
            patch("contexts.canvas.application.canvas_service.Publisher", _mock_publisher()),
            _relay_patch(),
        ):
            result = await svc.create_snapshot(
                canvas_id=_CANVAS_ID,
                chatroom_id=_CHATROOM_ID,
                actor_user_id=_USER_ID,
            )

        repo.delete_oldest_snapshot.assert_awaited_once_with(_CANVAS_ID)
        assert result.id == new_snap.id

    @pytest.mark.anyio
    async def test_no_prune_when_below_cap(self) -> None:
        svc, repo = _mock_service()
        repo.count_snapshots = AsyncMock(return_value=10)
        repo.delete_oldest_snapshot = AsyncMock()
        repo.list_objects = AsyncMock(return_value=[])
        repo.create_snapshot = AsyncMock(return_value=_make_snapshot())

        with (
            patch("contexts.canvas.application.canvas_service.audit", _mock_audit()),
            patch("contexts.canvas.application.canvas_service.Publisher", _mock_publisher()),
            _relay_patch(),
        ):
            await svc.create_snapshot(
                canvas_id=_CANVAS_ID,
                chatroom_id=_CHATROOM_ID,
            )

        repo.delete_oldest_snapshot.assert_not_awaited()


# ---------------------------------------------------------------------------
# Restore (AC-4, AC-5, AC-9)
# ---------------------------------------------------------------------------


class TestSnapshotRestore:
    @pytest.mark.anyio
    async def test_restore_creates_autosave_and_replaces_objects(self) -> None:
        svc, repo = _mock_service()
        target_snap = _make_snapshot(
            snapshot_data={
                "objects": [
                    {
                        "kind": "note",
                        "content": "restored",
                        "position_x": 5,
                        "position_y": 10,
                        "width": 200,
                        "height": 150,
                        "z_index": 1,
                        "style": {"color": "red"},
                    }
                ]
            }
        )
        existing_obj = _make_object()
        auto_save = _make_snapshot(label="Auto-save before restore")

        repo.get_snapshot = AsyncMock(return_value=target_snap)
        repo.count_snapshots = AsyncMock(return_value=5)
        repo.delete_oldest_snapshot = AsyncMock()
        repo.list_objects = AsyncMock(side_effect=[[], [existing_obj]])
        repo.create_snapshot = AsyncMock(return_value=auto_save)
        repo.batch_delete_objects = AsyncMock(return_value=1)
        repo.create_object = AsyncMock(return_value=existing_obj)

        mock_pub = _mock_publisher()

        with (
            patch("contexts.canvas.application.canvas_service.audit", _mock_audit()),
            patch("contexts.canvas.application.canvas_service.Publisher", mock_pub),
            _relay_patch(),
        ):
            result = await svc.restore_snapshot(
                canvas_id=_CANVAS_ID,
                chatroom_id=_CHATROOM_ID,
                snapshot_id=target_snap.id,
                actor_user_id=_USER_ID,
            )

        assert result is not None
        assert result.label == "Auto-save before restore"
        repo.batch_delete_objects.assert_awaited_once()
        repo.create_object.assert_awaited()

        pub_instance = mock_pub.return_value
        emit_calls = pub_instance.emit.call_args_list
        event_types = [c[0][0] for c in emit_calls]
        assert "canvas.snapshot_restored" in event_types

    @pytest.mark.anyio
    async def test_restore_returns_none_for_missing_snapshot(self) -> None:
        svc, repo = _mock_service()
        repo.get_snapshot = AsyncMock(return_value=None)

        result = await svc.restore_snapshot(
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            snapshot_id=uuid.uuid4(),
        )

        assert result is None


# ---------------------------------------------------------------------------
# Label (AC-6)
# ---------------------------------------------------------------------------


class TestSnapshotLabel:
    @pytest.mark.anyio
    async def test_label_passed_to_repo(self) -> None:
        svc, repo = _mock_service()
        repo.count_snapshots = AsyncMock(return_value=0)
        repo.list_objects = AsyncMock(return_value=[])
        snap_with_label = _make_snapshot(label="My checkpoint")
        repo.create_snapshot = AsyncMock(return_value=snap_with_label)

        with (
            patch("contexts.canvas.application.canvas_service.audit", _mock_audit()),
            patch("contexts.canvas.application.canvas_service.Publisher", _mock_publisher()),
            _relay_patch(),
        ):
            result = await svc.create_snapshot(
                canvas_id=_CANVAS_ID,
                chatroom_id=_CHATROOM_ID,
                label="My checkpoint",
            )

        call_values = repo.create_snapshot.call_args[1]["values"]
        assert call_values["label"] == "My checkpoint"
        assert result.label == "My checkpoint"


# ---------------------------------------------------------------------------
# Get by ID (AC-8)
# ---------------------------------------------------------------------------


class TestSnapshotGetById:
    @pytest.mark.anyio
    async def test_get_returns_snapshot(self) -> None:
        svc, repo = _mock_service()
        snap = _make_snapshot()
        repo.get_snapshot = AsyncMock(return_value=snap)

        result = await svc.get_snapshot(_CANVAS_ID, snap.id)
        assert result is not None
        assert result.id == snap.id
        repo.get_snapshot.assert_awaited_once_with(snap.id, canvas_id=_CANVAS_ID)

    @pytest.mark.anyio
    async def test_get_returns_none_for_missing(self) -> None:
        svc, repo = _mock_service()
        repo.get_snapshot = AsyncMock(return_value=None)

        result = await svc.get_snapshot(_CANVAS_ID, uuid.uuid4())
        assert result is None


# ---------------------------------------------------------------------------
# Label sanitization (route-level helper)
# ---------------------------------------------------------------------------


class TestSanitizeLabel:
    def test_strips_control_characters(self) -> None:
        from app.api.v1.canvas import _sanitize_label

        assert _sanitize_label("hello\x00world") == "helloworld"
        assert _sanitize_label("a\x0b\x0cb") == "ab"

    def test_collapses_whitespace(self) -> None:
        from app.api.v1.canvas import _sanitize_label

        assert _sanitize_label("  too   many   spaces  ") == "too many spaces"

    def test_truncates_to_200(self) -> None:
        from app.api.v1.canvas import _sanitize_label

        long_label = "x" * 300
        assert len(_sanitize_label(long_label)) == 200

    def test_preserves_valid_unicode(self) -> None:
        from app.api.v1.canvas import _sanitize_label

        assert _sanitize_label("快照标签 v2") == "快照标签 v2"
