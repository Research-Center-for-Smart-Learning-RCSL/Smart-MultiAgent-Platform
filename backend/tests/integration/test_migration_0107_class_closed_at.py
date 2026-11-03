"""Exercise 0107 against a minimal stand-in table in an isolated scratch schema."""

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
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0107_class_closed_at.py"
    spec = importlib.util.spec_from_file_location("migration_0107", path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_upgrade_leaves_rooms_open_and_downgrade_removes_the_column():
    url = os.environ.get("SMAP_SCRATCH_DATABASE_URL")
    if not url:
        pytest.skip("SMAP_SCRATCH_DATABASE_URL is required for migration DDL")
    migration = _load()
    engine = sa.create_engine(url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            schema = f"class_close_{uuid.uuid4().hex}"
            conn.execute(sa.schema.CreateSchema(schema))
            conn.execute(sa.select(sa.func.set_config("search_path", schema, True)))
            conn.execute(sa.text("CREATE TABLE chatrooms (id uuid PRIMARY KEY, deleted_at timestamptz NULL)"))
            conn.execute(sa.text("INSERT INTO chatrooms (id) VALUES (:r)"), {"r": uuid.uuid4()})
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                assert conn.execute(sa.text("SELECT class_closed_at IS NULL FROM chatrooms")).scalar_one()
                conn.execute(sa.text("UPDATE chatrooms SET class_closed_at = now()"))
                migration.downgrade()
                inspector = sa.inspect(conn)
                assert [c["name"] for c in inspector.get_columns("chatrooms", schema=schema)] == [
                    "id",
                    "deleted_at",
                ]
            transaction.rollback()
    finally:
        engine.dispose()
