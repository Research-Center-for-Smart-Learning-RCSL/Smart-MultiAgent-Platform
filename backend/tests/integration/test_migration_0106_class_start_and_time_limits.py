"""Exercise 0106 against minimal stand-in tables in an isolated scratch schema."""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.db


def _load():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0106_class_start_and_time_limits.py"
    spec = importlib.util.spec_from_file_location("migration_0106", path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_upgrade_constraints_and_downgrade():
    url = os.environ.get("SMAP_SCRATCH_DATABASE_URL")
    if not url:
        pytest.skip("SMAP_SCRATCH_DATABASE_URL is required for migration DDL")
    migration = _load()
    engine = sa.create_engine(url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            schema = f"class_start_{uuid.uuid4().hex}"
            conn.execute(sa.schema.CreateSchema(schema))
            conn.execute(sa.select(sa.func.set_config("search_path", schema, True)))
            conn.execute(sa.text("CREATE TABLE chatrooms (id uuid PRIMARY KEY, deleted_at timestamptz NULL)"))
            conn.execute(sa.text("CREATE TABLE activity_types (id uuid PRIMARY KEY)"))
            conn.execute(
                sa.text(
                    "CREATE TABLE activity_activations (id uuid PRIMARY KEY, status text NOT NULL, "
                    "created_at timestamptz NOT NULL DEFAULT now())"
                )
            )
            room, kind = uuid.uuid4(), uuid.uuid4()
            conn.execute(sa.text("INSERT INTO chatrooms (id) VALUES (:r)"), {"r": room})
            conn.execute(sa.text("INSERT INTO activity_types (id) VALUES (:t)"), {"t": kind})
            conn.execute(
                sa.text("INSERT INTO activity_activations (id, status) VALUES (:a, 'active')"),
                {"a": uuid.uuid4()},
            )
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                # Existing rows are untouched: no limit, no class start.
                assert (
                    conn.execute(
                        sa.text("SELECT count(*) FROM activity_activations WHERE deadline_at IS NULL")
                    ).scalar_one()
                    == 1
                )
                assert conn.execute(sa.text("SELECT class_start_config IS NULL FROM chatrooms")).scalar_one()
                conn.execute(
                    sa.text(
                        "INSERT INTO chatroom_activity_time_limits (chatroom_id, activity_type_id, "
                        "time_limit_minutes) VALUES (:r, :t, NULL)"
                    ),
                    {"r": room, "t": kind},
                )
                for statement in (
                    "UPDATE activity_types SET default_time_limit_minutes = 0",
                    "UPDATE activity_types SET default_time_limit_minutes = 181",
                    "UPDATE chatroom_activity_time_limits SET time_limit_minutes = 0",
                    # A limit without a deadline, or the reverse, is refused.
                    "UPDATE activity_activations SET time_limit_minutes = 5",
                    "UPDATE activity_activations SET deadline_at = now()",
                ):
                    with conn.begin_nested() as savepoint:
                        with pytest.raises(sa.exc.IntegrityError):
                            conn.execute(sa.text(statement))
                        savepoint.rollback()
                conn.execute(
                    sa.text(
                        "UPDATE activity_activations SET time_limit_minutes = 5, "
                        "deadline_at = created_at + interval '5 minutes'"
                    )
                )
                conn.execute(sa.text("DELETE FROM chatrooms"))
                assert (
                    conn.execute(sa.text("SELECT count(*) FROM chatroom_activity_time_limits")).scalar_one()
                    == 0
                )
                migration.downgrade()
                inspector = sa.inspect(conn)
                assert not inspector.has_table("chatroom_activity_time_limits", schema=schema)
                assert [c["name"] for c in inspector.get_columns("chatrooms", schema=schema)] == [
                    "id",
                    "deleted_at",
                ]
                assert [c["name"] for c in inspector.get_columns("activity_types", schema=schema)] == ["id"]
                assert [c["name"] for c in inspector.get_columns("activity_activations", schema=schema)] == [
                    "id",
                    "status",
                    "created_at",
                ]
            transaction.rollback()
    finally:
        engine.dispose()
