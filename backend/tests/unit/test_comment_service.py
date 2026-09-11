"""Unit tests for contexts.canvas.application.comment_service.

Covers CRUD operations, sanitization, audit emission, and WS publishing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from contexts.canvas.application.comment_service import CommentService, _sanitize_content
from contexts.canvas.domain.models import CanvasComment

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CANVAS_ID = uuid.uuid4()
_CHATROOM_ID = uuid.uuid4()
_OBJECT_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _comment(
    *,
    comment_id: uuid.UUID | None = None,
    object_id: uuid.UUID | None = None,
    content: str = "hello",
    created_by_user_id: uuid.UUID | None = _USER_ID,
    created_by_guest_id: uuid.UUID | None = None,
) -> CanvasComment:
    return CanvasComment(
        id=comment_id or uuid.uuid4(),
        canvas_id=_CANVAS_ID,
        object_id=object_id or _OBJECT_ID,
        content=content,
        created_by_user_id=created_by_user_id,
        created_by_guest_id=created_by_guest_id,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _service(repo_mock: AsyncMock) -> CommentService:
    db = AsyncMock()
    svc = CommentService(db)
    svc._repo = repo_mock
    return svc


def _repo() -> AsyncMock:
    return AsyncMock()


class TestSanitizeContent:
    def test_strips_control_chars(self) -> None:
        assert _sanitize_content("hello\x00world\x07") == "helloworld"

    def test_preserves_newlines_and_tabs(self) -> None:
        assert _sanitize_content("line1\nline2\ttab") == "line1\nline2\ttab"

    def test_caps_at_2000_chars(self) -> None:
        text = "a" * 3000
        result = _sanitize_content(text)
        assert len(result) == 2000

    def test_preserves_non_ascii(self) -> None:
        assert _sanitize_content("你好世界") == "你好世界"

    def test_empty_string(self) -> None:
        assert _sanitize_content("") == ""

    def test_strips_null_byte(self) -> None:
        assert _sanitize_content("ab\x00cd") == "abcd"

    def test_strips_bell(self) -> None:
        assert _sanitize_content("ab\x07cd") == "abcd"

    def test_preserves_space(self) -> None:
        assert _sanitize_content("a b") == "a b"


class TestCreateComment:
    @pytest.mark.asyncio
    @patch("contexts.canvas.application.comment_service.Publisher")
    @patch("contexts.canvas.application.comment_service.audit")
    async def test_creates_and_returns_comment(
        self, mock_audit: AsyncMock, mock_publisher_cls: AsyncMock
    ) -> None:
        expected = _comment(content="test comment")
        repo = _repo()
        repo.create_comment.return_value = expected
        mock_audit.emit = AsyncMock()
        mock_publisher_cls.return_value.emit = AsyncMock()

        svc = _service(repo)
        result = await svc.create_comment(
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            object_id=_OBJECT_ID,
            content="test comment",
            created_by_user_id=_USER_ID,
            actor_user_id=_USER_ID,
        )

        assert result.content == "test comment"
        assert result.created_by_user_id == _USER_ID
        repo.create_comment.assert_awaited_once()
        mock_audit.emit.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("contexts.canvas.application.comment_service.Publisher")
    @patch("contexts.canvas.application.comment_service.audit")
    async def test_sanitizes_content_before_storing(
        self, mock_audit: AsyncMock, mock_publisher_cls: AsyncMock
    ) -> None:
        expected = _comment(content="clean")
        repo = _repo()
        repo.create_comment.return_value = expected
        mock_audit.emit = AsyncMock()
        mock_publisher_cls.return_value.emit = AsyncMock()

        svc = _service(repo)
        await svc.create_comment(
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            object_id=_OBJECT_ID,
            content="has\x00null",
            created_by_user_id=_USER_ID,
        )

        call_kwargs = repo.create_comment.call_args[1]
        assert "\x00" not in call_kwargs["values"]["content"]

    @pytest.mark.asyncio
    @patch("contexts.canvas.application.comment_service.Publisher")
    @patch("contexts.canvas.application.comment_service.audit")
    async def test_publishes_ws_event(
        self, mock_audit: AsyncMock, mock_publisher_cls: AsyncMock
    ) -> None:
        expected = _comment()
        repo = _repo()
        repo.create_comment.return_value = expected
        mock_audit.emit = AsyncMock()
        mock_pub_instance = AsyncMock()
        mock_publisher_cls.return_value = mock_pub_instance

        svc = _service(repo)
        await svc.create_comment(
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            object_id=_OBJECT_ID,
            content="hello",
            created_by_user_id=_USER_ID,
        )

        mock_pub_instance.emit.assert_awaited_once()
        args = mock_pub_instance.emit.call_args
        assert args[0][0] == "canvas.comment_created"


class TestUpdateComment:
    @pytest.mark.asyncio
    @patch("contexts.canvas.application.comment_service.Publisher")
    @patch("contexts.canvas.application.comment_service.audit")
    async def test_update_returns_updated_comment(
        self, mock_audit: AsyncMock, mock_publisher_cls: AsyncMock
    ) -> None:
        comment_id = uuid.uuid4()
        updated = _comment(comment_id=comment_id, content="updated text")
        repo = _repo()
        repo.update_comment.return_value = updated
        mock_audit.emit = AsyncMock()
        mock_publisher_cls.return_value.emit = AsyncMock()

        svc = _service(repo)
        result = await svc.update_comment(
            comment_id=comment_id,
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            content="updated text",
            actor_user_id=_USER_ID,
        )

        assert result is not None
        assert result.content == "updated text"
        mock_audit.emit.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("contexts.canvas.application.comment_service.Publisher")
    @patch("contexts.canvas.application.comment_service.audit")
    async def test_update_returns_none_for_missing_comment(
        self, mock_audit: AsyncMock, mock_publisher_cls: AsyncMock
    ) -> None:
        repo = _repo()
        repo.update_comment.return_value = None
        mock_audit.emit = AsyncMock()
        mock_publisher_cls.return_value.emit = AsyncMock()

        svc = _service(repo)
        result = await svc.update_comment(
            comment_id=uuid.uuid4(),
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            content="new text",
        )

        assert result is None
        mock_audit.emit.assert_not_awaited()


class TestDeleteComment:
    @pytest.mark.asyncio
    @patch("contexts.canvas.application.comment_service.Publisher")
    @patch("contexts.canvas.application.comment_service.audit")
    async def test_delete_returns_true_on_success(
        self, mock_audit: AsyncMock, mock_publisher_cls: AsyncMock
    ) -> None:
        repo = _repo()
        repo.delete_comment.return_value = True
        mock_audit.emit = AsyncMock()
        mock_publisher_cls.return_value.emit = AsyncMock()

        svc = _service(repo)
        result = await svc.delete_comment(
            comment_id=uuid.uuid4(),
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            object_id=_OBJECT_ID,
            actor_user_id=_USER_ID,
        )

        assert result is True
        mock_audit.emit.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("contexts.canvas.application.comment_service.Publisher")
    @patch("contexts.canvas.application.comment_service.audit")
    async def test_delete_returns_false_when_already_deleted(
        self, mock_audit: AsyncMock, mock_publisher_cls: AsyncMock
    ) -> None:
        repo = _repo()
        repo.delete_comment.return_value = False
        mock_audit.emit = AsyncMock()
        mock_publisher_cls.return_value.emit = AsyncMock()

        svc = _service(repo)
        result = await svc.delete_comment(
            comment_id=uuid.uuid4(),
            canvas_id=_CANVAS_ID,
            chatroom_id=_CHATROOM_ID,
            object_id=_OBJECT_ID,
        )

        assert result is False
        mock_audit.emit.assert_not_awaited()


class TestListComments:
    @pytest.mark.asyncio
    async def test_delegates_to_repo(self) -> None:
        comments = [_comment(content="a"), _comment(content="b")]
        repo = _repo()
        repo.list_comments.return_value = comments

        svc = _service(repo)
        result = await svc.list_comments(
            _OBJECT_ID, canvas_id=_CANVAS_ID, limit=10, offset=0
        )

        assert len(result) == 2
        repo.list_comments.assert_awaited_once_with(
            _OBJECT_ID, canvas_id=_CANVAS_ID, limit=10, offset=0
        )

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_comments(self) -> None:
        repo = _repo()
        repo.list_comments.return_value = []

        svc = _service(repo)
        result = await svc.list_comments(_OBJECT_ID, canvas_id=_CANVAS_ID)

        assert result == []


class TestCountCommentsByObject:
    @pytest.mark.asyncio
    async def test_returns_counts(self) -> None:
        oid1 = uuid.uuid4()
        oid2 = uuid.uuid4()
        repo = _repo()
        repo.count_comments_by_object.return_value = {oid1: 3, oid2: 1}

        svc = _service(repo)
        result = await svc.count_comments_by_object(
            [oid1, oid2], canvas_id=_CANVAS_ID
        )

        assert result == {oid1: 3, oid2: 1}

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_input(self) -> None:
        repo = _repo()
        repo.count_comments_by_object.return_value = {}

        svc = _service(repo)
        result = await svc.count_comments_by_object([], canvas_id=_CANVAS_ID)

        assert result == {}
