"""Migration 0084's constraints, executed rather than described (AC-1, AC-15).

Every claim here is a `CHECK` or a primary key, and the unit tier's
`literal_binds` compilation cannot see either — `backend/CLAUDE.md`'s
"PostgreSQL-specific SQL needs a db-tier test". The singleton guarantee in
particular is load-bearing: the bootstrap advisory lock only has to elect a
winner *because* a second row is impossible at the schema level.

The downgrade is asserted to touch PostgreSQL only. Alembic can neither write nor
read the legacy Redis keys, which is why the documented rollback order puts
`prepare-email-domain-policy-rollback` before any downgrade or old image.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0084_schema.py -m db
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.db

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")
_ROOT = Path(__file__).resolve().parents[2]

_INSERT = "INSERT INTO email_domain_policies (id, mode, rollout_state, version) VALUES (:id, :m, :s, :v)"


@pytest.fixture
def scratch_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Engine]:
    """A throwaway database migrated to 0084."""
    if not _SCRATCH_URL:
        pytest.skip(
            "SMAP_SCRATCH_DATABASE_URL is not set. This test drops and recreates the public "
            "schema, so it requires a dedicated throwaway database, never the db-tier one."
        )

    from app.config.settings import get_settings

    monkeypatch.setenv("SMAP_DB_DSN", _SCRATCH_URL)
    get_settings.cache_clear()
    try:
        configured = get_settings().database.dsn
        if configured != _SCRATCH_URL:
            pytest.fail(
                "refusing to run: the DSN override did not take effect. Alembic would migrate "
                f"{configured!r}, not the scratch database {_SCRATCH_URL!r}."
            )
        engine = sa.create_engine(_SCRATCH_URL.replace("+asyncpg", "+psycopg"))
        try:
            with engine.begin() as reset:
                reset.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
                reset.execute(sa.text("CREATE SCHEMA public"))
            command.upgrade(Config(str(_ROOT / "alembic.ini")), "0084_email_domain_policies")
            yield engine
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def test_the_table_is_created_empty(scratch_engine: sa.engine.Engine) -> None:
    """No default row: applying the migration changes no behaviour until the
    startup import runs, which is what makes it safe to deploy ahead of the
    application."""
    with scratch_engine.connect() as conn:
        count = conn.execute(sa.text("SELECT count(*) FROM email_domain_policies")).scalar_one()
    assert count == 0


def test_a_second_row_is_impossible(scratch_engine: sa.engine.Engine) -> None:
    with scratch_engine.begin() as conn:
        conn.execute(sa.text(_INSERT), {"id": 1, "m": "off", "s": "active", "v": 1})

    with pytest.raises(sa.exc.IntegrityError), scratch_engine.begin() as conn:
        conn.execute(sa.text(_INSERT), {"id": 2, "m": "off", "s": "active", "v": 1})


@pytest.mark.parametrize(
    ("mode", "state", "version"),
    [
        ("sideways", "active", 1),
        ("off", "some_future_phase", 1),
        ("off", "active", 0),
    ],
)
def test_an_unparseable_row_cannot_be_written_even_by_an_operator(
    scratch_engine: sa.engine.Engine, mode: str, state: str, version: int
) -> None:
    """The API is not the only writer. A row whose mode or state the application
    cannot parse would be indistinguishable from a corrupt cache and would fail
    every request closed, so the legal sets live in the schema."""
    with pytest.raises(sa.exc.IntegrityError), scratch_engine.begin() as conn:
        conn.execute(sa.text(_INSERT), {"id": 1, "m": mode, "s": state, "v": version})


def test_the_lists_default_to_empty_arrays(scratch_engine: sa.engine.Engine) -> None:
    with scratch_engine.begin() as conn:
        conn.execute(sa.text(_INSERT), {"id": 1, "m": "off", "s": "compatibility", "v": 1})
    with scratch_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT allow_domains, deny_domains, legacy_mirrored_version FROM email_domain_policies")
        ).one()
    assert row.allow_domains == []
    assert row.deny_domains == []
    # NULL until a rollback preparation writes and reads back the legacy triple.
    assert row.legacy_mirrored_version is None


def test_the_downgrade_drops_the_table_and_touches_nothing_else(
    scratch_engine: sa.engine.Engine,
) -> None:
    """PostgreSQL only. Alembic cannot restore the legacy Redis keys, so a
    downgrade run before `prepare-email-domain-policy-rollback` leaves an old
    image with no policy at all — which is why the operations manual makes the
    verified marker a precondition rather than a suggestion."""
    command.downgrade(Config(str(_ROOT / "alembic.ini")), "0083_widen_agent_effort")

    with scratch_engine.connect() as conn:
        present = conn.execute(sa.text("SELECT to_regclass('public.email_domain_policies')")).scalar_one()
        # The tables the migration did not create are untouched.
        users = conn.execute(sa.text("SELECT to_regclass('public.users')")).scalar_one()
    assert present is None
    assert users is not None
