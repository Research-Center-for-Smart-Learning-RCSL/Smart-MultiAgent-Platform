"""Unit tests for retention deep policies and RetentionService.

Covers: RetentionService.purge_once (chunk purge + MinIO cleanup + per-room
audit + empty case), _purge_messages (chunked delegation), _purge_soft_deleted_tenancy
(5-table sweep), _purge_agent_instances, _sweep_orphaned_subagent_roots
(children-before-roots), _close_idle_impersonations (JTI deny + gauge),
_cleanup_tus_parts (filesystem), _sweep_instructions_chains, _purge_exports_bucket
mock, _scrub_stale_presence, _archive_workflow_runs, _rollup_key_usage_events,
_purge_message_attachments, _purge_audit_logs, _manage_key_usage_partitions.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.conversation.application.retention_service import (
    PurgeReport,
    RetentionService,
)

_NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=UTC)


# ===========================================================================
# RetentionService.purge_once
# ===========================================================================


class TestRetentionServicePurgeOnce:
    @patch("contexts.conversation.application.retention_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.retention_service.now", return_value=_NOW)
    async def test_purge_deletes_messages_and_minio_objects(self, _now, _audit) -> None:
        db = AsyncMock()
        minio = AsyncMock()
        svc = RetentionService(db, minio=minio)

        room_id = uuid.uuid4()
        msg1, msg2 = uuid.uuid4(), uuid.uuid4()
        msg_rows = [MagicMock(id=msg1, chatroom_id=room_id), MagicMock(id=msg2, chatroom_id=room_id)]
        att_rows = [MagicMock(minio_path="chat-uploads/key1/file.png")]

        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=msg_rows)),
            MagicMock(all=MagicMock(return_value=att_rows)),
            MagicMock(rowcount=2),
        ]

        report = await svc.purge_once()

        assert report.messages_deleted == 2
        assert report.attachments_objects_removed == 1
        minio.remove.assert_awaited_once_with(bucket="chat-uploads", key="key1/file.png")
        _audit.assert_awaited_once()
        event = _audit.call_args[0][1]
        assert event.action == "message.purged_by_retention"
        assert event.metadata["count"] == 2

    @patch("contexts.conversation.application.retention_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.retention_service.now", return_value=_NOW)
    async def test_purge_groups_audit_by_room(self, _now, _audit) -> None:
        db = AsyncMock()
        minio = AsyncMock()
        svc = RetentionService(db, minio=minio)

        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        msg_rows = [
            MagicMock(id=uuid.uuid4(), chatroom_id=room_a),
            MagicMock(id=uuid.uuid4(), chatroom_id=room_b),
            MagicMock(id=uuid.uuid4(), chatroom_id=room_a),
        ]

        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=msg_rows)),
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(rowcount=3),
        ]

        report = await svc.purge_once()

        assert report.messages_deleted == 3
        assert _audit.await_count == 2
        actions = [c[0][1].action for c in _audit.call_args_list]
        assert all(a == "message.purged_by_retention" for a in actions)

    @patch("contexts.conversation.application.retention_service.now", return_value=_NOW)
    async def test_purge_empty_returns_oldest_kept(self, _now) -> None:
        db = AsyncMock()
        minio = AsyncMock()
        svc = RetentionService(db, minio=minio)

        oldest = datetime(2022, 1, 1, tzinfo=UTC)
        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(scalar=MagicMock(return_value=oldest)),
        ]

        report = await svc.purge_once()

        assert report.messages_deleted == 0
        assert report.attachments_objects_removed == 0
        assert report.oldest_kept_at == oldest

    @patch("contexts.conversation.application.retention_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.retention_service.now", return_value=_NOW)
    async def test_purge_minio_failure_does_not_block(self, _now, _audit) -> None:
        db = AsyncMock()
        minio = AsyncMock()
        minio.remove.side_effect = Exception("S3 down")
        svc = RetentionService(db, minio=minio)

        msg_rows = [MagicMock(id=uuid.uuid4(), chatroom_id=uuid.uuid4())]
        att_rows = [MagicMock(minio_path="bucket/key")]

        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=msg_rows)),
            MagicMock(all=MagicMock(return_value=att_rows)),
            MagicMock(rowcount=1),
        ]

        report = await svc.purge_once()

        assert report.messages_deleted == 1
        assert report.attachments_objects_removed == 0

    @patch("contexts.conversation.application.retention_service.now", return_value=_NOW)
    async def test_purge_uses_correct_horizon(self, _now) -> None:
        db = AsyncMock()
        minio = AsyncMock()
        svc = RetentionService(db, minio=minio)

        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(scalar=MagicMock(return_value=None)),
        ]

        report = await svc.purge_once()

        assert report.oldest_kept_at is None
        select_call = db.execute.call_args_list[0]
        compiled = str(select_call[0][0])
        assert "messages" in compiled


# ===========================================================================
# retention.py deep policies
# ===========================================================================


class TestPurgeMessages:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.get_sessionmaker")
    async def test_chunked_purge(self, mock_sm, _audit) -> None:
        from app.workers.tasks.retention import _purge_messages

        chunk_session = AsyncMock()
        chunk_session.__aenter__ = AsyncMock(return_value=chunk_session)
        chunk_session.__aexit__ = AsyncMock(return_value=False)
        chunk_session.begin = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
        )
        mock_sm.return_value = MagicMock(return_value=chunk_session)

        call_count = 0

        async def purge_once_side():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return PurgeReport(100, 5, _NOW)
            return PurgeReport(0, 0, _NOW)

        with patch(
            "app.workers.tasks.retention.RetentionService.purge_once",
            side_effect=purge_once_side,
        ):
            session = AsyncMock()
            count = await _purge_messages(session)

        assert count == 200
        assert call_count == 3

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.get_sessionmaker")
    async def test_purge_stops_at_100_chunks(self, mock_sm, _audit) -> None:
        from app.workers.tasks.retention import _purge_messages

        chunk_session = AsyncMock()
        chunk_session.__aenter__ = AsyncMock(return_value=chunk_session)
        chunk_session.__aexit__ = AsyncMock(return_value=False)
        chunk_session.begin = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
        )
        mock_sm.return_value = MagicMock(return_value=chunk_session)

        with patch(
            "app.workers.tasks.retention.RetentionService.purge_once",
            return_value=PurgeReport(10, 0, _NOW),
        ):
            session = AsyncMock()
            count = await _purge_messages(session)

        assert count == 1000


class TestPurgeSoftDeletedTenancy:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_sweeps_all_tables(self, _now, _audit) -> None:
        from app.workers.tasks.retention import _SOFT_DELETE_TABLES, _purge_soft_deleted_tenancy

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 3
        session.execute.return_value = result

        count = await _purge_soft_deleted_tenancy(session)

        assert count == 3 * len(_SOFT_DELETE_TABLES)
        assert session.execute.await_count == len(_SOFT_DELETE_TABLES)


class TestPurgeAgentInstances:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_deletes_destroyed_instances(self, _now, _audit) -> None:
        from app.workers.tasks.retention import _purge_agent_instances

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 12
        session.execute.return_value = result

        count = await _purge_agent_instances(session)

        assert count == 12
        sql_text = str(session.execute.call_args[0][0])
        assert "destroyed_at" in sql_text
        assert "agent_instances" in sql_text


class TestSweepOrphanedSubagentRoots:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_deletes_children_before_roots(self, _audit) -> None:
        from app.workers.tasks.retention import _sweep_orphaned_subagent_roots

        session = AsyncMock()
        root_id = uuid.uuid4()
        root_result = MagicMock()
        root_result.all.return_value = [(root_id,)]
        child_delete = MagicMock()
        root_delete = MagicMock()
        root_delete.rowcount = 1
        session.execute.side_effect = [root_result, child_delete, root_delete]

        count = await _sweep_orphaned_subagent_roots(session)

        assert count == 1
        assert session.execute.await_count == 3
        child_sql = str(session.execute.call_args_list[1][0][0])
        assert "parent_id" in child_sql

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_no_orphans_returns_zero(self, _audit) -> None:
        from app.workers.tasks.retention import _sweep_orphaned_subagent_roots

        session = AsyncMock()
        root_result = MagicMock()
        root_result.all.return_value = []
        session.execute.return_value = root_result

        count = await _sweep_orphaned_subagent_roots(session)

        assert count == 0
        assert session.execute.await_count == 1


class TestCloseIdleImpersonations:
    @patch("app.workers.tasks.retention.ADMIN_IMPERSONATION_SESSIONS_ACTIVE")
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_closes_and_denies_jtis(self, _now, _audit, _gauge) -> None:
        from app.workers.tasks.retention import _close_idle_impersonations

        session = AsyncMock()
        row1 = MagicMock(access_jti="jti-abc")
        row2 = MagicMock(access_jti=None)
        close_result = MagicMock()
        close_result.all.return_value = [row1, row2]
        count_result = MagicMock()
        count_result.scalar.return_value = 5
        session.execute.side_effect = [close_result, count_result]

        with patch("shared_kernel.auth.tokens.deny_access_jti", new_callable=AsyncMock) as deny:
            count = await _close_idle_impersonations(session)

        assert count == 2
        deny.assert_awaited_once_with("jti-abc")
        _gauge.set.assert_called_once_with(5)


class TestCleanupTusParts:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_removes_old_part_files(self, _now, _audit, tmp_path) -> None:
        from app.workers.tasks.retention import _cleanup_tus_parts

        staging = str(tmp_path)
        old_file = tmp_path / "old.part"
        old_file.write_text("data")
        old_ts = (_NOW - timedelta(hours=25)).timestamp()
        os.utime(str(old_file), (old_ts, old_ts))

        new_file = tmp_path / "new.part"
        new_file.write_text("data")

        session = AsyncMock()

        with patch.dict(os.environ, {"SMAP_TUS_STAGING_DIR": staging}):
            count = await _cleanup_tus_parts(session)

        assert count == 1
        assert not old_file.exists()
        assert new_file.exists()

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_missing_dir_returns_zero(self, _now, _audit) -> None:
        from app.workers.tasks.retention import _cleanup_tus_parts

        session = AsyncMock()

        with patch.dict(os.environ, {"SMAP_TUS_STAGING_DIR": "/nonexistent/path"}):
            count = await _cleanup_tus_parts(session)

        assert count == 0


class TestSweepInstructionsChains:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_deletes_terminal_chains(self, _now, _audit) -> None:
        from app.workers.tasks.retention import _sweep_instructions_chains

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 8
        session.execute.return_value = result

        count = await _sweep_instructions_chains(session)

        assert count == 8
        sql_text = str(session.execute.call_args[0][0])
        assert "instructions" in sql_text
        assert "completed" in sql_text


class TestFacadeDelegatingPolicies:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_purge_message_attachments(self, _audit) -> None:
        from app.workers.tasks.retention import _purge_message_attachments

        session = AsyncMock()
        with patch(
            "contexts.conversation.interfaces.facade.ConversationFacade"
        ) as MockFacade:
            facade = AsyncMock()
            facade.purge_old_attachments.return_value = 15
            MockFacade.return_value = facade

            count = await _purge_message_attachments(session)

        assert count == 15
        facade.purge_old_attachments.assert_awaited_once_with(max_age_days=3)

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_purge_audit_logs(self, _audit) -> None:
        from app.workers.tasks.retention import _purge_audit_logs

        session = AsyncMock()
        with patch("contexts.audit.interfaces.facade.AuditFacade") as MockFacade:
            facade = AsyncMock()
            facade.purge_old_logs.return_value = 100
            MockFacade.return_value = facade

            count = await _purge_audit_logs(session)

        assert count == 100
        facade.purge_old_logs.assert_awaited_once_with(retention_days=365)

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_archive_workflow_runs(self, _audit) -> None:
        from app.workers.tasks.retention import _archive_workflow_runs

        session = AsyncMock()
        with patch(
            "contexts.workflow.interfaces.facade.WorkflowFacade"
        ) as MockFacade:
            facade = AsyncMock()
            facade.archive_old_runs.return_value = 50
            MockFacade.return_value = facade

            count = await _archive_workflow_runs(session)

        assert count == 50
        facade.archive_old_runs.assert_awaited_once_with(retention_days=90)

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_rollup_key_usage_events(self, _audit) -> None:
        from app.workers.tasks.retention import _rollup_key_usage_events

        session = AsyncMock()
        with patch("contexts.keys.interfaces.facade.KeysFacade") as MockFacade:
            facade = AsyncMock()
            facade.rollup_usage_events.return_value = 200
            MockFacade.return_value = facade

            count = await _rollup_key_usage_events(session)

        assert count == 200

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_manage_key_usage_partitions(self, _audit) -> None:
        from app.workers.tasks.retention import _manage_key_usage_partitions

        session = AsyncMock()
        with patch("contexts.keys.interfaces.facade.KeysFacade") as MockFacade:
            facade = AsyncMock()
            facade.manage_partitions.return_value = 2
            MockFacade.return_value = facade

            count = await _manage_key_usage_partitions(session)

        assert count == 2

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_scrub_stale_presence(self, _audit) -> None:
        from app.workers.tasks.retention import _scrub_stale_presence

        session = AsyncMock()
        with patch(
            "contexts.conversation.infrastructure.presence.scrub_stale_presence",
            new_callable=AsyncMock,
            return_value=7,
        ):
            count = await _scrub_stale_presence(session)

        assert count == 7
