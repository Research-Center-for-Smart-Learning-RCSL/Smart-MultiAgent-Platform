"""Compiled-SQL invariants for the activities repositories (no DB).

Mirrors ``test_message_repo.py``: mock the ``AsyncSession`` and assert the
statement the repo builds carries the guards its correctness depends on —
``deleted_at IS NULL`` soft-delete filtering, room scoping, the open-session
predicate, and the ``pending``-only validation write-back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from contexts.activities.domain.errors import ActivityTypeKeyConflict
from contexts.activities.domain.models import ActivityTypeScope, ValidatorKind
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.submission_repo import (
    ActivitySubmissionRepository,
)
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository


def _compiled(stmt: object) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})  # type: ignore[attr-defined]
    )


class TestAdminUnscopedListings:
    """The cross-project reads behind the admin governance view ([R30.31]).

    These are the only unscoped queries in the context, so what matters is that
    they still filter soft-deletes / non-active rows, order deterministically, and
    bound the page — an unbounded unscoped scan over every tenant is the failure
    mode worth pinning.
    """

    async def _run(self, repo_call: object) -> str:
        db = AsyncMock()
        page = MagicMock()
        page.all.return_value = []
        db.execute.return_value = page
        await repo_call(db)  # type: ignore[operator]
        return _compiled(db.execute.await_args_list[0].args[0])

    async def test_types_list_all_filters_soft_deleted_and_bounds_the_page(self) -> None:
        compiled = await self._run(lambda db: ActivityTypeRepository(db).list_all(limit=25))

        assert "deleted_at IS NULL" in compiled
        assert "ORDER BY activity_types.created_at DESC, activity_types.id DESC" in compiled
        assert "LIMIT 25" in compiled
        # Unscoped by design: no project predicate may creep in.
        assert "project_id =" not in compiled

    async def test_types_list_all_cursor_uses_a_created_at_anchor_plus_id_tiebreak(self) -> None:
        cursor = uuid.uuid4()
        compiled = await self._run(lambda db: ActivityTypeRepository(db).list_all(cursor=cursor, limit=10))

        # Keyset, not offset — and the tiebreak must be present, or rows sharing a
        # created_at would be skipped or repeated across pages.
        assert "OFFSET" not in compiled.upper()
        assert str(cursor) in compiled
        assert "created_at <" in compiled
        assert "created_at =" in compiled
        assert "id <" in compiled

    async def test_activations_list_all_active_filters_to_active_only(self) -> None:
        compiled = await self._run(lambda db: ActivationRepository(db).list_all_active(limit=25))

        assert "status = 'active'" in compiled
        assert "ORDER BY activity_activations.created_at DESC, activity_activations.id DESC" in compiled
        assert "LIMIT 25" in compiled
        assert "chatroom_id =" not in compiled

    async def test_activations_list_all_active_cursor_is_keyset(self) -> None:
        cursor = uuid.uuid4()
        compiled = await self._run(
            lambda db: ActivationRepository(db).list_all_active(cursor=cursor, limit=10)
        )

        assert "OFFSET" not in compiled.upper()
        assert str(cursor) in compiled
        assert "created_at <" in compiled
        assert "id <" in compiled

    async def test_get_many_short_circuits_without_a_query(self) -> None:
        db = AsyncMock()
        result = await ActivityTypeRepository(db).get_many([])
        assert result == {}
        db.execute.assert_not_awaited()

    async def test_get_many_filters_soft_deleted(self) -> None:
        ids = [uuid.uuid4(), uuid.uuid4()]
        compiled = await self._run(lambda db: ActivityTypeRepository(db).get_many(ids))

        assert "deleted_at IS NULL" in compiled
        for i in ids:
            assert str(i) in compiled


class TestSubmissionRepoScoping:
    async def test_list_recent_for_room_is_scoped_and_bounded(self) -> None:
        room_id = uuid.uuid4()
        db = AsyncMock()
        page = MagicMock()
        page.all.return_value = []
        db.execute.return_value = page

        await ActivitySubmissionRepository(db).list_recent_for_room(chatroom_id=room_id, limit=7)

        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert str(room_id) in compiled
        assert "deleted_at IS NULL" in compiled
        assert "ORDER BY activity_submissions.created_at DESC" in compiled
        assert "LIMIT 7" in compiled
        # Agent-visibility follow-up: the row carries the digest and its type's
        # exposure flag so the context provider can gate per-row.
        assert "activity_submissions.agent_digest" in compiled
        assert "activity_types.expose_payload_to_agent" in compiled

    async def test_recent_rows_say_where_each_digest_came_from(self) -> None:
        """AC-18. Derived by rebuilding the deterministic payload fallback and
        comparing, so it is exact for rows written before the distinction existed
        — which no backfilled column could be. The payload is read to answer the
        question and dropped; it never reaches `RecentActivityRow`."""
        from contexts.activities.domain.agent_digest import build_agent_digest

        payload = {"home": "a house by the sea", "work": ""}
        db = AsyncMock()
        page = MagicMock()
        page.all.return_value = [
            SimpleNamespace(
                created_at=datetime(2026, 8, 24, tzinfo=UTC),
                subject_user_id=uuid.uuid4(),
                subject_member_group_id=None,
                attempt_no=1,
                type_key="mandala-9grid",
                validation_status="validated",
                is_valid=True,
                error_class=None,
                agent_digest=build_agent_digest(payload=payload, detail=None),
                payload=payload,
                expose_payload_to_agent=True,
            ),
            SimpleNamespace(
                created_at=datetime(2026, 8, 24, tzinfo=UTC),
                subject_user_id=uuid.uuid4(),
                subject_member_group_id=None,
                attempt_no=2,
                type_key="mandala-9grid",
                validation_status="validated",
                is_valid=True,
                error_class=None,
                agent_digest="1/2 fields answered: home",
                payload=payload,
                expose_payload_to_agent=True,
            ),
        ]
        db.execute.return_value = page

        rows = await ActivitySubmissionRepository(db).list_recent_for_room(chatroom_id=uuid.uuid4(), limit=30)

        assert [r.digest_is_computed for r in rows] == [False, True]
        assert "a house by the sea" not in repr([r for r in rows if r.digest_is_computed])

    async def test_a_row_with_no_digest_is_not_reported_as_computed(self) -> None:
        db = AsyncMock()
        page = MagicMock()
        page.all.return_value = [
            SimpleNamespace(
                created_at=datetime(2026, 8, 24, tzinfo=UTC),
                subject_user_id=uuid.uuid4(),
                subject_member_group_id=None,
                attempt_no=1,
                type_key="k",
                validation_status="pending",
                is_valid=None,
                error_class=None,
                agent_digest=None,
                payload={"a": "b"},
                expose_payload_to_agent=True,
            )
        ]
        db.execute.return_value = page

        rows = await ActivitySubmissionRepository(db).list_recent_for_room(chatroom_id=uuid.uuid4(), limit=30)

        assert rows[0].digest_is_computed is False

    async def test_record_validation_transitions_only_from_pending(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        db.execute.return_value = result

        await ActivitySubmissionRepository(db).record_validation(
            submission_id=uuid.uuid4(),
            is_valid=True,
            error_class=None,
            sub_scores={},
            latency_ms=12,
            validated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        # JSONB values block literal_binds, so inspect the bound params: the WHERE
        # guard must carry 'pending' and the SET must write 'validated'.
        stmt = db.execute.await_args_list[0].args[0]
        str_values = [
            v for v in stmt.compile(dialect=postgresql.dialect()).params.values() if isinstance(v, str)
        ]
        assert "pending" in str_values
        assert "validated" in str_values

    async def test_record_validation_omits_agent_digest_when_not_given(self) -> None:
        """Agent-visibility follow-up: no ``agent_digest`` kwarg -> the SET clause
        must not touch that column, so the submit-time fallback digest survives."""
        db = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        db.execute.return_value = result

        await ActivitySubmissionRepository(db).record_validation(
            submission_id=uuid.uuid4(),
            is_valid=True,
            error_class=None,
            sub_scores={},
            latency_ms=12,
            validated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "agent_digest" not in sql

    async def test_record_validation_writes_agent_digest_when_given(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        db.execute.return_value = result

        await ActivitySubmissionRepository(db).record_validation(
            submission_id=uuid.uuid4(),
            is_valid=True,
            error_class=None,
            sub_scores={},
            latency_ms=12,
            validated_at=datetime(2026, 1, 1, tzinfo=UTC),
            agent_digest="a rich description",
        )

        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "agent_digest" in sql
        str_values = [
            v for v in stmt.compile(dialect=postgresql.dialect()).params.values() if isinstance(v, str)
        ]
        assert "a rich description" in str_values

    async def test_sweep_stalled_touches_only_pending(self) -> None:
        db = AsyncMock()
        rows = [SimpleNamespace(id=uuid.uuid4(), chatroom_id=uuid.uuid4()) for _ in range(3)]
        result = MagicMock()
        result.__iter__.return_value = iter(rows)
        db.execute.return_value = result

        swept_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        swept = await ActivitySubmissionRepository(db).sweep_stalled(
            cutoff=datetime(2026, 1, 1, tzinfo=UTC), error_class="stalled", swept_at=swept_at
        )

        assert len(swept) == 3
        compiled = _compiled(db.execute.await_args_list[0].args[0])
        # The pending-only predicate is the must-not-weaken guard: the RETURNING
        # rewrite must not touch the WHERE.
        assert "validation_status = 'pending'" in compiled
        # validated_at records the sweep time, not the (earlier) TTL cutoff.
        assert "2026-01-01 12:00:00" in compiled

    async def test_sweep_stalled_returns_identity_of_each_swept_row(self) -> None:
        db = AsyncMock()
        rows = [SimpleNamespace(id=uuid.uuid4(), chatroom_id=uuid.uuid4()) for _ in range(2)]
        result = MagicMock()
        result.__iter__.return_value = iter(rows)
        db.execute.return_value = result

        swept = await ActivitySubmissionRepository(db).sweep_stalled(
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            error_class="stalled",
            swept_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        assert swept == [(r.id, r.chatroom_id) for r in rows]
        # The RETURNING clause must project both columns the watchdog needs to emit.
        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert "RETURNING activity_submissions.id, activity_submissions.chatroom_id" in compiled

    async def test_next_attempt_no_ignores_soft_delete_for_monotonicity(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one.return_value = 4
        db.execute.return_value = result

        n = await ActivitySubmissionRepository(db).next_attempt_no(uuid.uuid4())

        # max(attempt_no)=4 -> next is 5, and the query must NOT filter deleted_at
        # (else a soft-deleted top attempt would let a number be reused).
        assert n == 5
        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert "max(activity_submissions.attempt_no)" in compiled
        assert "deleted_at" not in compiled


class TestPlatformScopedTypeQueries:
    """AC-4/AC-11: the reads that make a platform type reachable from a project.

    ``list_for_project`` is the only query whose shape changed, and the way it
    could go wrong silently is by widening: dropping the opt-in predicate would
    show every tenant every installed example.
    """

    async def _run(self, repo_call: object) -> str:
        db = AsyncMock()
        page = MagicMock()
        page.all.return_value = []
        db.execute.return_value = page
        await repo_call(db)  # type: ignore[operator]
        return _compiled(db.execute.await_args_list[0].args[0])

    async def test_list_for_project_admits_platform_types_only_through_an_optin(self) -> None:
        project_id = uuid.uuid4()
        compiled = await self._run(lambda db: ActivityTypeRepository(db).list_for_project(project_id))

        assert "deleted_at IS NULL" in compiled
        # The project's own rows, OR rows this project holds an opt-in for. The
        # project id must bound BOTH arms — an opt-in subquery without it would
        # return every project's opt-ins.
        assert "project_activity_type_optins" in compiled
        assert compiled.count(str(project_id)) == 2
        assert "ORDER BY activity_types.created_at DESC, activity_types.id DESC" in compiled

    async def test_list_owned_by_project_has_no_optin_arm(self) -> None:
        """The ownership question, as distinct from ``list_for_project``'s usable set.

        The seeder keys idempotency on this: if it grew an opt-in arm it would
        report a read-only platform example as a copy the project already owns.
        """
        project_id = uuid.uuid4()
        compiled = await self._run(lambda db: ActivityTypeRepository(db).list_owned_by_project(project_id))

        assert "activity_types.project_id = " in compiled
        assert "deleted_at IS NULL" in compiled
        assert "project_activity_type_optins" not in compiled
        # One arm, so the project id appears exactly once — two would mean the
        # opt-in subquery crept back in.
        assert compiled.count(str(project_id)) == 1

    async def test_list_platform_returns_only_ownerless_live_rows(self) -> None:
        compiled = await self._run(lambda db: ActivityTypeRepository(db).list_platform())

        assert "activity_types.project_id IS NULL" in compiled
        assert "deleted_at IS NULL" in compiled

    async def test_list_platform_by_keys_short_circuits_without_a_query(self) -> None:
        db = AsyncMock()
        assert await ActivityTypeRepository(db).list_platform_by_keys([]) == []
        db.execute.assert_not_awaited()

    async def test_list_platform_by_keys_is_scoped_to_platform_rows(self) -> None:
        compiled = await self._run(
            lambda db: ActivityTypeRepository(db).list_platform_by_keys(["mandala-9grid", "scamper"])
        )

        # Without the NULL project guard the install idempotency check would see
        # another tenant's identically-keyed project type and skip the install.
        assert "activity_types.project_id IS NULL" in compiled
        assert "mandala-9grid" in compiled
        assert "scamper" in compiled
        assert "deleted_at IS NULL" in compiled

    async def test_create_writes_the_scope_it_was_given(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one.return_value = uuid.uuid4()
        db.execute.return_value = result

        await ActivityTypeRepository(db).create(
            project_id=None,
            key="mandala-9grid",
            name="Mandala",
            payload_schema={},
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config={},
            retention_days=None,
            expose_payload_to_agent=True,
            echo_includes_content=False,
            scope=ActivityTypeScope.PLATFORM,
        )

        # JSONB blocks literal_binds, so read the bound params.
        stmt = db.execute.await_args_list[0].args[0]
        params = stmt.compile(dialect=postgresql.dialect()).params
        assert params["scope"] == "platform"
        assert params["project_id"] is None

    async def test_create_defaults_to_project_scope(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one.return_value = uuid.uuid4()
        db.execute.return_value = result

        await ActivityTypeRepository(db).create(
            project_id=uuid.uuid4(),
            key="k",
            name="n",
            payload_schema={},
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config={},
            retention_days=None,
            expose_payload_to_agent=True,
            echo_includes_content=False,
        )

        stmt = db.execute.await_args_list[0].args[0]
        assert stmt.compile(dialect=postgresql.dialect()).params["scope"] == "project"

    async def test_create_maps_the_platform_key_index_to_a_domain_conflict(self) -> None:
        """AC-2: without this arm a duplicate install is a raw IntegrityError 500.

        The project index name cannot cover it — a NULL ``project_id`` makes that
        partial-unique stop constraining platform rows entirely.
        """
        db = AsyncMock()
        db.execute.side_effect = IntegrityError(
            "INSERT ...",
            {},
            Exception(
                'duplicate key value violates unique constraint "uq_activity_types_platform_key_active"'
            ),
        )

        with pytest.raises(ActivityTypeKeyConflict):
            await ActivityTypeRepository(db).create(
                project_id=None,
                key="mandala-9grid",
                name="Mandala",
                payload_schema={},
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={},
                retention_days=None,
                expose_payload_to_agent=True,
                echo_includes_content=False,
                scope=ActivityTypeScope.PLATFORM,
            )

    async def test_create_maps_the_project_key_index_to_a_domain_conflict(self) -> None:
        """The platform arm's sibling, and until now the untested one.

        `uq_activity_types_project_key_active` has had no coverage at all, and
        neither had the arm mapping it -- an asymmetry worth closing, because it
        is exactly what let the cross-scope question go unasked: nothing pinned
        what the *project* index does or does not constrain. It constrains one
        project's own live keys, and nothing more; a platform type under the same
        key is a different row this index never sees ([R30.02]).
        """
        db = AsyncMock()
        db.execute.side_effect = IntegrityError(
            "INSERT ...",
            {},
            Exception(
                'duplicate key value violates unique constraint "uq_activity_types_project_key_active"'
            ),
        )

        with pytest.raises(ActivityTypeKeyConflict):
            await ActivityTypeRepository(db).create(
                project_id=uuid.uuid4(),
                key="mandala-9grid",
                name="Mandala",
                payload_schema={},
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={},
                retention_days=None,
                expose_payload_to_agent=True,
                echo_includes_content=False,
            )

    async def test_create_reraises_an_unrelated_integrity_error(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = IntegrityError(
            "INSERT ...",
            {},
            Exception('new row violates check constraint "ck_activity_types_project_scope"'),
        )

        # A half-converted row is a bug in the caller, not a key conflict; mapping
        # it to 409 would tell the client to change the key and try again.
        with pytest.raises(IntegrityError):
            await ActivityTypeRepository(db).create(
                project_id=None,
                key="k",
                name="n",
                payload_schema={},
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={},
                retention_days=None,
                expose_payload_to_agent=True,
                echo_includes_content=False,
            )


class TestOptInRepoDeleteByType:
    """AC-5: the shape of the delete an admin type-delete runs.

    Unbounded by project on purpose — the type is going away for everyone — which
    is exactly why the predicate has to be pinned. A stray project bound would
    make the admin delete silently leave most projects orphaned; no predicate at
    all would wipe every project's opt-in for every type.
    """

    async def _run(self, type_id: uuid.UUID) -> str:
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db.execute.return_value = result
        await ProjectActivityTypeOptInRepository(db).remove_all_for_type(type_id)
        return _compiled(db.execute.await_args_list[0].args[0])

    async def test_remove_all_for_type_filters_on_the_type_and_nothing_else(self) -> None:
        type_id = uuid.uuid4()
        compiled = await self._run(type_id)

        assert compiled.startswith("DELETE FROM project_activity_type_optins")
        assert "WHERE" in compiled
        assert f"activity_type_id = '{type_id}'" in compiled
        # Not project-scoped: an admin delete revokes the type for every tenant.
        assert "project_activity_type_optins.project_id =" not in compiled

    async def test_remove_all_for_type_returns_the_projects_it_revoked(self) -> None:
        """The RETURNING clause is what makes the audit count possible."""
        compiled = await self._run(uuid.uuid4())

        assert "RETURNING project_activity_type_optins.project_id" in compiled

    async def test_remove_all_for_type_reads_the_returned_project_ids(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        revoked = [uuid.uuid4(), uuid.uuid4()]
        result.scalars.return_value.all.return_value = revoked
        db.execute.return_value = result

        assert list(await ProjectActivityTypeOptInRepository(db).remove_all_for_type(uuid.uuid4())) == revoked


class TestTypeRepoScoping:
    async def test_get_filters_soft_deleted(self) -> None:
        type_id = uuid.uuid4()
        db = AsyncMock()
        row = MagicMock()
        row.first.return_value = SimpleNamespace()  # not reached — get returns None on falsy
        row.first.return_value = None
        db.execute.return_value = row

        await ActivityTypeRepository(db).get(type_id)

        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert str(type_id) in compiled
        assert "deleted_at IS NULL" in compiled
