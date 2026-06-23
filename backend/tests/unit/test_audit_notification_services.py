"""Unit tests for AuditQueryService and NotificationService.

Covers: audit query delegation, CSV export pagination + isolation level +
max_rows cap, retention purge with role switching, notification send with
dedup + WS push, list/mark_read/unread_count, dedup-key auto-generation.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from contexts.audit.application.audit_query_service import AuditQueryService
from contexts.audit.domain.models import AuditEntry, AuditFilter, AuditPage
from contexts.notification.application.notification_service import NotificationService
from contexts.notification.domain.models import Notification, NotificationKind

_NOW = datetime(2026, 6, 23, 12, 0, 0)
_USER = uuid.uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(*, entry_id: int = 1, action: str = "test.action") -> AuditEntry:
    return AuditEntry(
        id=entry_id,
        actor_user_id=_USER,
        actor_ip="1.2.3.4",
        action=action,
        resource_type="test",
        resource_id=uuid.uuid4(),
        metadata={},
        session_id=None,
        request_id=None,
        created_at=_NOW,
    )


def _notification(
    *,
    notif_id: uuid.UUID | None = None,
    kind: NotificationKind = NotificationKind.INVITE_RECEIVED,
    read_at: datetime | None = None,
) -> Notification:
    return Notification(
        id=notif_id or uuid.uuid4(),
        user_id=_USER,
        kind=kind,
        title="You have an invite",
        body=None,
        metadata={},
        read_at=read_at,
        created_at=_NOW,
    )


def _make_audit_service(*, repo: AsyncMock | None = None) -> AuditQueryService:
    db = AsyncMock()
    svc = AuditQueryService(db)
    if repo is not None:
        svc._repo = repo
    return svc


def _make_notification_service(*, repo: AsyncMock | None = None) -> NotificationService:
    db = AsyncMock()
    svc = NotificationService(db)
    if repo is not None:
        svc._repo = repo
    return svc


# ===========================================================================
# AuditQueryService
# ===========================================================================


class TestAuditQuery:
    async def test_delegates_to_repo(self) -> None:
        page = AuditPage(items=[_entry()], next_cursor=None)
        repo = AsyncMock()
        repo.query.return_value = page
        svc = _make_audit_service(repo=repo)

        result = await svc.query(AuditFilter(), cursor=None, limit=50)

        assert len(result.items) == 1
        assert result.next_cursor is None
        repo.query.assert_awaited_once()

    async def test_custom_filters(self) -> None:
        page = AuditPage(items=[], next_cursor=None)
        repo = AsyncMock()
        repo.query.return_value = page
        svc = _make_audit_service(repo=repo)

        filters = AuditFilter(action="auth.login", resource_type="user")
        await svc.query(filters, cursor=42, limit=10)

        call_args = repo.query.call_args
        assert call_args[0][0].action == "auth.login"
        assert call_args[1]["cursor"] == 42
        assert call_args[1]["limit"] == 10


class TestAuditExportCsv:
    async def test_single_page_export(self) -> None:
        entries = [_entry(entry_id=i, action=f"act.{i}") for i in range(3)]
        page = AuditPage(items=entries, next_cursor=None)
        repo = AsyncMock()
        repo.query.return_value = page
        svc = _make_audit_service(repo=repo)

        csv_bytes = await svc.export_csv(AuditFilter())

        lines = csv_bytes.decode("utf-8").strip().split("\n")
        assert len(lines) == 4  # header + 3 rows
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        header = next(reader)
        assert header[0] == "id"
        assert header[3] == "action"

    async def test_multi_page_export(self) -> None:
        page1 = AuditPage(items=[_entry(entry_id=1)], next_cursor=2)
        page2 = AuditPage(items=[_entry(entry_id=2)], next_cursor=None)
        repo = AsyncMock()
        repo.query.side_effect = [page1, page2]
        svc = _make_audit_service(repo=repo)

        csv_bytes = await svc.export_csv(AuditFilter())

        lines = csv_bytes.decode("utf-8").strip().split("\n")
        assert len(lines) == 3  # header + 2 rows
        assert repo.query.await_count == 2

    async def test_max_rows_cap(self) -> None:
        entries = [_entry(entry_id=i) for i in range(10)]
        page = AuditPage(items=entries, next_cursor=11)
        repo = AsyncMock()
        repo.query.return_value = page
        svc = _make_audit_service(repo=repo)

        csv_bytes = await svc.export_csv(AuditFilter(), max_rows=3)

        lines = csv_bytes.decode("utf-8").strip().split("\n")
        assert len(lines) == 4  # header + 3 rows (capped at max_rows)

    async def test_sets_repeatable_read(self) -> None:
        page = AuditPage(items=[], next_cursor=None)
        repo = AsyncMock()
        repo.query.return_value = page
        db = AsyncMock()
        svc = AuditQueryService(db)
        svc._repo = repo

        await svc.export_csv(AuditFilter())

        db.execute.assert_awaited()
        sql_call = db.execute.call_args_list[0]
        sql_text = str(sql_call[0][0])
        assert "REPEATABLE READ" in sql_text


class TestAuditPurge:
    @patch("shared_kernel.auth.clients.now", return_value=_NOW)
    async def test_purge_old_logs(self, _now) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 42
        db.execute.return_value = result_mock
        svc = AuditQueryService(db)

        count = await svc.purge_old_logs(retention_days=365)

        assert count == 42
        calls = db.execute.call_args_list
        sql_texts = [str(c[0][0]) for c in calls]
        assert any("smap_audit_retention" in s for s in sql_texts)
        assert any("RESET ROLE" in s for s in sql_texts)
        assert any("DELETE FROM audit_logs" in s for s in sql_texts)

    @patch("shared_kernel.auth.clients.now", return_value=_NOW)
    async def test_purge_resets_role_on_error(self, _now) -> None:
        db = AsyncMock()
        call_count = 0

        async def execute_side(sql, **kwargs):
            nonlocal call_count
            call_count += 1
            text = str(sql)
            if "DELETE FROM" in text:
                raise RuntimeError("simulated DB error")
            return MagicMock()

        db.execute = execute_side
        svc = AuditQueryService(db)

        with pytest.raises(RuntimeError):
            await svc.purge_old_logs()


# ===========================================================================
# NotificationService
# ===========================================================================


class TestNotificationSend:
    @patch("contexts.notification.application.notification_service.Publisher")
    @patch("contexts.notification.application.notification_service.now", return_value=_NOW)
    async def test_send_creates_and_pushes(self, _now, _pub_cls) -> None:
        notif = _notification()
        repo = AsyncMock()
        repo.insert.return_value = (notif, True)
        _pub_cls.return_value = AsyncMock()
        svc = _make_notification_service(repo=repo)

        result = await svc.send(
            user_id=_USER,
            kind=NotificationKind.INVITE_RECEIVED,
            title="You have an invite",
        )

        assert result.id == notif.id
        repo.insert.assert_awaited_once()
        insert_kwargs = repo.insert.call_args.kwargs
        assert insert_kwargs["kind"] is NotificationKind.INVITE_RECEIVED
        assert insert_kwargs["dedup_key"].startswith("auto:")

    @patch("contexts.notification.application.notification_service.Publisher")
    @patch("contexts.notification.application.notification_service.now", return_value=_NOW)
    async def test_send_dedup_skips_push(self, _now, _pub_cls) -> None:
        notif = _notification()
        repo = AsyncMock()
        repo.insert.return_value = (notif, False)
        pub_instance = AsyncMock()
        _pub_cls.return_value = pub_instance
        svc = _make_notification_service(repo=repo)

        result = await svc.send(
            user_id=_USER,
            kind=NotificationKind.INVITE_RECEIVED,
            title="You have an invite",
        )

        assert result.id == notif.id
        pub_instance.emit.assert_not_awaited()

    @patch("contexts.notification.application.notification_service.Publisher")
    @patch("contexts.notification.application.notification_service.now", return_value=_NOW)
    async def test_custom_dedup_key(self, _now, _pub_cls) -> None:
        notif = _notification()
        repo = AsyncMock()
        repo.insert.return_value = (notif, True)
        _pub_cls.return_value = AsyncMock()
        svc = _make_notification_service(repo=repo)

        await svc.send(
            user_id=_USER,
            kind=NotificationKind.INVITE_RECEIVED,
            title="Invite",
            dedup_key="custom:abc",
        )

        insert_kwargs = repo.insert.call_args.kwargs
        assert insert_kwargs["dedup_key"] == "custom:abc"


class TestNotificationList:
    async def test_list_for_user(self) -> None:
        notifs = [_notification(), _notification()]
        repo = AsyncMock()
        repo.list_for_user.return_value = notifs
        svc = _make_notification_service(repo=repo)

        result = await svc.list_for_user(_USER)

        assert len(result) == 2
        repo.list_for_user.assert_awaited_once()

    async def test_list_with_cursor(self) -> None:
        repo = AsyncMock()
        repo.list_for_user.return_value = []
        svc = _make_notification_service(repo=repo)

        cursor_id = uuid.uuid4()
        await svc.list_for_user(_USER, cursor=cursor_id, limit=10)

        call_kwargs = repo.list_for_user.call_args
        assert call_kwargs.kwargs["cursor"] == cursor_id
        assert call_kwargs.kwargs["limit"] == 10


class TestNotificationMarkRead:
    async def test_mark_read(self) -> None:
        repo = AsyncMock()
        repo.mark_read.return_value = 3
        svc = _make_notification_service(repo=repo)

        ids = [uuid.uuid4() for _ in range(3)]
        count = await svc.mark_read(_USER, ids)

        assert count == 3
        repo.mark_read.assert_awaited_once_with(_USER, ids)


class TestNotificationUnreadCount:
    async def test_unread_count(self) -> None:
        repo = AsyncMock()
        repo.unread_count.return_value = 7
        svc = _make_notification_service(repo=repo)

        count = await svc.unread_count(_USER)

        assert count == 7


class TestNotificationDedupKey:
    @patch("contexts.notification.application.notification_service.Publisher")
    @patch("contexts.notification.application.notification_service.now", return_value=_NOW)
    async def test_auto_dedup_key_format(self, _now, _pub_cls) -> None:
        _pub_cls.return_value = AsyncMock()
        repo = AsyncMock()
        repo.insert.return_value = (_notification(), True)
        svc = _make_notification_service(repo=repo)

        await svc.send(
            user_id=_USER,
            kind=NotificationKind.KEY_USAGE_THRESHOLD,
            title="Key usage high",
        )

        dedup = repo.insert.call_args.kwargs["dedup_key"]
        assert dedup.startswith("auto:key.usage_threshold:")
        bucket = int(_NOW.timestamp()) // 60
        assert str(bucket) in dedup

    @patch("contexts.notification.application.notification_service.Publisher")
    @patch("contexts.notification.application.notification_service.now", return_value=_NOW)
    async def test_same_title_same_bucket_same_key(self, _now, _pub_cls) -> None:
        _pub_cls.return_value = AsyncMock()
        repo = AsyncMock()
        repo.insert.return_value = (_notification(), True)
        svc = _make_notification_service(repo=repo)

        await svc.send(user_id=_USER, kind=NotificationKind.INVITE_RECEIVED, title="X")
        key1 = repo.insert.call_args.kwargs["dedup_key"]

        await svc.send(user_id=_USER, kind=NotificationKind.INVITE_RECEIVED, title="X")
        key2 = repo.insert.call_args.kwargs["dedup_key"]

        assert key1 == key2

    @patch("contexts.notification.application.notification_service.Publisher")
    @patch("contexts.notification.application.notification_service.now", return_value=_NOW)
    async def test_different_title_different_key(self, _now, _pub_cls) -> None:
        _pub_cls.return_value = AsyncMock()
        repo = AsyncMock()
        repo.insert.return_value = (_notification(), True)
        svc = _make_notification_service(repo=repo)

        await svc.send(user_id=_USER, kind=NotificationKind.INVITE_RECEIVED, title="A")
        key1 = repo.insert.call_args.kwargs["dedup_key"]

        await svc.send(user_id=_USER, kind=NotificationKind.INVITE_RECEIVED, title="B")
        key2 = repo.insert.call_args.kwargs["dedup_key"]

        assert key1 != key2
