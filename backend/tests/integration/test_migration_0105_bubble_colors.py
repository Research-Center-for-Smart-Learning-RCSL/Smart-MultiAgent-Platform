"""Exercise 0105 on existing bindings in an isolated scratch schema."""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.db


def test_upgrade_existing_bindings_constraint_and_downgrade():
    url = os.environ.get("SMAP_SCRATCH_DATABASE_URL")
    if not url:
        pytest.skip("SMAP_SCRATCH_DATABASE_URL is required for migration DDL")
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0105_agent_bubble_colors.py"
    spec = importlib.util.spec_from_file_location("migration_0105", path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            schema = f"bubble_colors_{uuid.uuid4().hex}"
            conn.execute(sa.schema.CreateSchema(schema))
            conn.execute(sa.select(sa.func.set_config("search_path", schema, True)))
            conn.execute(sa.text("CREATE TYPE chatroom_agent_role AS ENUM ('normal', 'observer')"))
            conn.execute(sa.text("CREATE TABLE chatroom_agents (role chatroom_agent_role NOT NULL)"))
            conn.execute(sa.text("INSERT INTO chatroom_agents (role) VALUES ('normal'), ('observer')"))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                assert (
                    conn.execute(
                        sa.text("SELECT count(*) FROM chatroom_agents WHERE bubble_color IS NULL")
                    ).scalar_one()
                    == 2
                )
                conn.execute(
                    sa.text("UPDATE chatroom_agents SET bubble_color = 'teal' WHERE role = 'normal'")
                )
                with conn.begin_nested() as savepoint:
                    with pytest.raises(sa.exc.IntegrityError):
                        conn.execute(
                            sa.text(
                                "UPDATE chatroom_agents SET bubble_color = 'teal' WHERE role = 'observer'"
                            )
                        )
                    savepoint.rollback()
                # Pre-0105 code demotes without naming `bubble_color`; the trigger
                # must clear it so the CHECK does not refuse that UPDATE.
                conn.execute(sa.text("UPDATE chatroom_agents SET role = 'observer' WHERE role = 'normal'"))
                assert (
                    conn.execute(
                        sa.text("SELECT count(*) FROM chatroom_agents WHERE bubble_color IS NOT NULL")
                    ).scalar_one()
                    == 0
                )
                migration.downgrade()
                columns = sa.inspect(conn).get_columns("chatroom_agents", schema=schema)
                assert [column["name"] for column in columns] == ["role"]
                assert conn.execute(sa.text("SELECT count(*) FROM chatroom_agents")).scalar_one() == 2
                assert not sa.inspect(conn).has_type("chatroom_agent_bubble_color", schema=schema)
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                            "WHERE p.proname = 'smap_chatroom_agents_clear_observer_color' "
                            "AND n.nspname = :schema"
                        ),
                        {"schema": schema},
                    ).scalar_one()
                    == 0
                )
            transaction.rollback()
    finally:
        engine.dispose()
