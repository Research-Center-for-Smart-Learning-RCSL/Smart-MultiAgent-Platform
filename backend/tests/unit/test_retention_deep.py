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
            MagicMock(all=MagicMock(return_value=[])),  # no summaries in the room
        ]

        report = await svc.purge_once()

        assert report.messages_deleted == 2
        assert report.attachments_objects_removed == 1
        assert report.summaries_deleted == 0
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
            MagicMock(all=MagicMock(return_value=[])),
        ]

        report = await svc.purge_once()

        assert report.messages_deleted == 3
        assert _audit.await_count == 2
        actions = [c[0][1].action for c in _audit.call_args_list]
        assert all(a == "message.purged_by_retention" for a in actions)

    @patch("contexts.conversation.application.retention_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.retention_service.now", return_value=_NOW)
    async def test_purge_deletes_summaries_that_folded_purged_messages(self, _now, _audit) -> None:
        # R13.26: a summary's text may reproduce the messages it folded, and it
        # is always newer than all of them, so a `created_at < horizon` sweep
        # never reaches it. Leaving it would keep purged content readable past
        # the retention horizon - in the room and in exports, since the summary
        # row is served like any other message.
        db = AsyncMock()
        svc = RetentionService(db, minio=AsyncMock())

        room = uuid.uuid4()
        purged, kept = uuid.uuid4(), uuid.uuid4()
        covering = uuid.uuid4()
        summary_rows = [
            MagicMock(
                id=covering,
                chatroom_id=room,
                metadata={"type": "compact_summary", "compacted_ids": [str(purged), str(kept)]},
            ),
            MagicMock(
                id=uuid.uuid4(),
                chatroom_id=room,
                metadata={"type": "compact_summary", "compacted_ids": [str(kept)]},
            ),
        ]
        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=[MagicMock(id=purged, chatroom_id=room)])),
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(rowcount=1),
            MagicMock(all=MagicMock(return_value=summary_rows)),
            MagicMock(rowcount=1),
        ]

        report = await svc.purge_once()

        assert report.summaries_deleted == 1
        assert _audit.call_args[0][1].metadata["summaries_deleted"] == 1
        # Five statements: victims, attachments, delete messages, summaries,
        # delete summaries. A summary covering only surviving messages is left
        # alone.
        assert db.execute.await_count == 5

    @patch("contexts.conversation.application.retention_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.retention_service.now", return_value=_NOW)
    async def test_purge_issues_no_delete_when_no_summary_is_affected(self, _now, _audit) -> None:
        db = AsyncMock()
        svc = RetentionService(db, minio=AsyncMock())

        room = uuid.uuid4()
        purged = uuid.uuid4()
        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=[MagicMock(id=purged, chatroom_id=room)])),
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(rowcount=1),
            MagicMock(
                all=MagicMock(
                    return_value=[
                        MagicMock(
                            id=uuid.uuid4(),
                            chatroom_id=room,
                            metadata={"type": "compact_summary", "compacted_ids": []},
                        )
                    ]
                )
            ),
        ]

        report = await svc.purge_once()

        assert report.summaries_deleted == 0
        assert db.execute.await_count == 4

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
            MagicMock(all=MagicMock(return_value=[])),
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
        chunk_session.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
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
        chunk_session.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
        mock_sm.return_value = MagicMock(return_value=chunk_session)

        with patch(
            "app.workers.tasks.retention.RetentionService.purge_once",
            return_value=PurgeReport(10, 0, _NOW),
        ):
            session = AsyncMock()
            count = await _purge_messages(session)

        assert count == 1000


class _RaceResult:
    """Result stub answering both the scalar and the row-tuple shape.

    The DB phase reads ``(id, owner_org_id)`` tuples for the org mapping but
    ``.scalars()`` for the ``DELETE ... RETURNING id`` results, so one stub has
    to serve both without the test caring which call it is answering.
    """

    def __init__(self, rows: list, rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def scalars(self):
        m = MagicMock()
        m.all.return_value = [r[0] if isinstance(r, tuple) else r for r in self._rows]
        return m

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _race_session(*, select_rows: list, delete_rows: list, org_ids: list | None = None):
    """Session dispatching on statement shape rather than call order.

    Order-independence is deliberate: these tests pin the invariant (teardown
    follows a committed delete) rather than a particular ``execute`` sequence,
    so restructuring the DB phase cannot make them pass for the wrong reason.
    The two SELECTs are told apart by width — the doomed-org read projects one
    column, the org->project mapping two.
    """
    import sqlalchemy as sa

    session = AsyncMock()

    async def _execute(stmt, *_a, **_kw):
        if isinstance(stmt, sa.Delete):
            return _RaceResult(delete_rows, rowcount=len(delete_rows))
        if len(stmt.selected_columns) == 1:
            return _RaceResult(org_ids or [])
        return _RaceResult(select_rows)

    session.execute.side_effect = _execute
    return session


def _session_maker(session):
    """Stand in for ``get_sessionmaker()`` — the maker is called per session."""

    class _CM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return None

        def begin(self):
            return _CM()

    session.begin = lambda: _CM()
    return lambda: _CM()


class TestPurgeSoftDeletedTenancy:
    @patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
    )
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_sweeps_all_tables(self, _now, _audit, _purge) -> None:
        from app.workers.tasks import retention as ret

        deleted = [uuid.uuid4() for _ in range(3)]
        _purge.return_value = len(deleted)
        # No org-owned projects, so the org mapping read yields nothing.
        session = _race_session(select_rows=[], delete_rows=deleted)

        with patch.object(ret, "get_sessionmaker", return_value=_session_maker(session)):
            count = await ret._purge_soft_deleted_tenancy(session)

        assert count == 3 * len(ret._SOFT_DELETE_TABLES)
        # The doomed-org read precedes the per-table deletes; with no doomed orgs
        # the mapping read is skipped entirely.
        assert session.execute.await_count == 1 + len(ret._SOFT_DELETE_TABLES)

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_retention_guard_covers_whole_cascade_path(self, _now, _audit) -> None:
        """D-1: the retain_until guard must defer purging chatrooms AND their
        ancestors (projects, orgs), since chatrooms cascade from projects and
        projects from orgs — otherwise a project/org purge deletes retained
        research early."""
        import sqlalchemy as sa
        from sqlalchemy.dialects import postgresql

        from app.workers.tasks import retention as ret
        from contexts.agents.infrastructure.tables import agents as agents_tbl
        from contexts.conversation.infrastructure.tables import chatrooms as chatrooms_tbl
        from contexts.tenancy.infrastructure.tables import orgs as orgs_tbl
        from contexts.tenancy.infrastructure.tables import projects as projects_tbl

        session = _race_session(select_rows=[], delete_rows=[])

        with patch.object(ret, "get_sessionmaker", return_value=_session_maker(session)):
            await ret._purge_soft_deleted_tenancy(session)

        # The org->project mapping SELECT carries no `.table`; only the deletes do.
        compiled = {
            str(call.args[0].table.name): str(call.args[0].compile(dialect=postgresql.dialect()))
            for call in session.execute.await_args_list
            if isinstance(call.args[0], sa.Delete)
        }
        for guarded in (chatrooms_tbl.name, projects_tbl.name, orgs_tbl.name):
            assert "retain_until" in compiled[guarded], f"{guarded} purge lacks retention guard"
        # Tables with no cascade path to a submission carry no guard.
        assert "retain_until" not in compiled[agents_tbl.name]


class TestRagSourceTeardownWiring:
    """F-24: retention hard-delete tears down source infra for doomed projects."""

    @patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
    )
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_tears_down_direct_and_org_cascade_projects(self, _now, _audit, purge_batch) -> None:
        import sqlalchemy as sa

        from app.workers.tasks import retention as ret
        from contexts.tenancy.infrastructure.tables import orgs as orgs_tbl
        from contexts.tenancy.infrastructure.tables import projects as projects_tbl

        direct_pid = uuid.uuid4()
        via_org_pid = uuid.uuid4()
        doomed_org = uuid.uuid4()
        purge_batch.return_value = 2

        session = AsyncMock()

        async def _execute(stmt, *_a, **_kw):
            if isinstance(stmt, sa.Delete):
                # Both hard deletes commit: orgs return the doomed parent, projects
                # the directly doomed row. Other tables delete nothing here.
                if stmt.table is orgs_tbl:
                    return _RaceResult([doomed_org], rowcount=1)
                if stmt.table is projects_tbl:
                    return _RaceResult([direct_pid], rowcount=1)
                return _RaceResult([], rowcount=0)
            if len(stmt.selected_columns) == 1:
                return _RaceResult([doomed_org])  # the doomed-org read
            # The org->project mapping read, captured before the cascade.
            return _RaceResult([(via_org_pid, doomed_org)])

        session.execute.side_effect = _execute

        with patch.object(ret, "get_sessionmaker", return_value=_session_maker(session)):
            await ret._purge_soft_deleted_tenancy(session)

        # One batch call carrying both the direct and org-cascade doomed projects.
        purge_batch.assert_awaited_once()
        assert set(purge_batch.await_args.args[0]) == {direct_pid, via_org_pid}


class TestRestoreRaceOrdering:
    """A restore that wins the race must leave every source blob/vector intact.

    Retention used to purge the projects it *selected*, then issue a fresh
    ``deleted_at IS NOT NULL`` delete. A restore landing in between made that
    delete match nothing, so an active project lost data it could never rebuild.
    Teardown must follow the committed delete, never the candidate read.
    """

    @patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
    )
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_no_teardown_when_restore_wins(self, _now, _audit, purge_batch) -> None:
        from app.workers.tasks import retention as ret

        restored_pid = uuid.uuid4()
        org_id = uuid.uuid4()
        # Eligibility reads still see the project; every delete affects zero rows
        # because the restore cleared `deleted_at` first.
        session = _race_session(select_rows=[(restored_pid, org_id)], delete_rows=[], org_ids=[org_id])

        with patch.object(ret, "get_sessionmaker", return_value=_session_maker(session)):
            await ret._purge_soft_deleted_tenancy(session)

        purge_batch.assert_not_awaited()


class TestTenancySweepFlushesItsAuditTail:
    """The destructive phase writes its audits on sessions it opens itself.

    `audit.emit` queues a realtime tail event on whichever session wrote the row,
    and `retention_sweep` only flushes the policy session it passed in. Whoever
    opens a session here therefore has to flush it, or `retention.soft_deleted.swept`
    never reaches the audit stream even though the row is in the table.
    """

    @patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.purge_project_source_infra_batch",
        new_callable=AsyncMock,
    )
    @patch("app.workers.tasks.retention.audit.flush_tail_events", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now", return_value=_NOW)
    async def test_flushes_every_session_it_opened(self, _now, _emit, flush, purge) -> None:
        from app.workers.tasks import retention as ret

        pid = uuid.uuid4()
        purge.return_value = 1
        session = _race_session(select_rows=[], delete_rows=[pid])

        with patch.object(ret, "get_sessionmaker", return_value=_session_maker(session)):
            await ret._purge_soft_deleted_tenancy(session)

        # One for the delete session, one for the teardown session.
        assert flush.await_count == 2, "an unflushed session drops its audit tail silently"

    """F-24: the backstop sweep delegates to the facade sweep and audits the count."""

    @patch(
        "contexts.knowledge.interfaces.facade.KnowledgeFacade.sweep_rag_source_orphans",
        new_callable=AsyncMock,
    )
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_delegates_to_facade_sweep_over_live_set(self, _audit, sweep) -> None:
        from app.workers.tasks.retention import _purge_rag_source_orphans

        live_pid = uuid.uuid4()
        sweep.return_value = 3

        session = AsyncMock()
        live_result = MagicMock()
        live_result.scalars.return_value.all.return_value = [live_pid]
        session.execute.return_value = live_result

        swept = await _purge_rag_source_orphans(session)

        assert swept == 3
        sweep.assert_awaited_once_with({live_pid})
        # Summary audit records the swept count.
        _audit.assert_awaited()


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
        with patch("contexts.conversation.interfaces.facade.ConversationFacade") as MockFacade:
            facade = AsyncMock()
            facade.purge_old_attachments.return_value = 15
            MockFacade.return_value = facade

            count = await _purge_message_attachments(session)

        assert count == 15
        facade.purge_old_attachments.assert_awaited_once_with(max_age_days=3)

    async def test_expire_attachments_policy_is_registered_in_the_sweep(self) -> None:
        # R13.11a: the `[attachment expired]` UI state is only reachable if the
        # nightly sweep actually runs, so registration is part of the contract.
        from app.workers.tasks.retention import _POLICIES, _expire_attachments

        assert ("attachment_expiry", _expire_attachments) in _POLICIES

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
        with patch("contexts.workflow.interfaces.facade.WorkflowFacade") as MockFacade:
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
        with (
            patch(
                "contexts.conversation.infrastructure.presence.scrub_stale_presence",
                new_callable=AsyncMock,
                return_value=(7, set()),
            ),
            patch(
                "contexts.conversation.application.triggers.evaluate_presence_change",
                new_callable=AsyncMock,
            ) as _notify,
        ):
            count = await _scrub_stale_presence(session)

        assert count == 7
        _notify.assert_not_awaited()

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_scrub_stale_presence_pauses_silence_for_emptied_rooms(self, _audit) -> None:
        """B1: a room the sweep left empty must run the same presence-changed
        (silence-pause) path a clean last-leave would -- otherwise a
        self-opening silence agent can still fire into the now-empty room."""
        from app.workers.tasks.retention import _scrub_stale_presence

        session = AsyncMock()
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        with (
            patch(
                "contexts.conversation.infrastructure.presence.scrub_stale_presence",
                new_callable=AsyncMock,
                return_value=(3, {room_a, room_b}),
            ),
            patch(
                "contexts.conversation.application.triggers.evaluate_presence_change",
                new_callable=AsyncMock,
            ) as _notify,
        ):
            count = await _scrub_stale_presence(session)

        assert count == 3
        notified_rooms = {c.kwargs["chatroom_id"] for c in _notify.await_args_list}
        assert notified_rooms == {room_a, room_b}
        for call in _notify.await_args_list:
            assert call.kwargs["has_live_users"] is False

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_scrub_stale_presence_survives_one_room_dispatch_failure(self, _audit) -> None:
        """A single room's presence-changed dispatch failing must not lose the
        redis-scrub count or block notifying the other emptied rooms."""
        from app.workers.tasks.retention import _scrub_stale_presence

        session = AsyncMock()
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        with (
            patch(
                "contexts.conversation.infrastructure.presence.scrub_stale_presence",
                new_callable=AsyncMock,
                return_value=(2, {room_a, room_b}),
            ),
            patch(
                "contexts.conversation.application.triggers.evaluate_presence_change",
                new_callable=AsyncMock,
                side_effect=[RuntimeError("boom"), None],
            ) as _notify,
        ):
            count = await _scrub_stale_presence(session)

        assert count == 2
        assert _notify.await_count == 2
