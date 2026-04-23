"""`smap.bootstrap create-admin` — seed the first platform Admin.

The concrete `users` / `admins` schema lands in Phase C. Until that migration
is in place we emit a `skipped` change rather than fail, so `bootstrap all`
stays green against a fresh Phase-B stack. Once Phase C merges its tables,
this subcommand inserts a fully-verified admin user with an Argon2id hash.

Safeguards per O5.01 / O5.03 / O5.04:
  * Refuses to run if ≥1 admin already exists unless `--force` is given.
  * Prints the generated password exactly once to stdout.
  * `--rescue` creates an emergency admin iff zero active admins exist — same
    primitive used by operators to recover from an accidental last-admin
    demotion (see `docs/operations.md` §5.2).
"""

from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config.settings import Settings

from ._common import BootstrapReport

_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#%^*-_"


def _generate_password(length: int = 24) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _tables_exist(conn: "object") -> bool:
    # Duck-typed on SQLAlchemy Connection to keep imports shallow.
    rows = conn.execute(  # type: ignore[attr-defined]
        text(
            "SELECT to_regclass('public.users') AS users, "
            "       to_regclass('public.admins') AS admins"
        )
    ).mappings().first()
    return bool(rows and rows["users"] and rows["admins"])


def _sync_dsn(settings: Settings) -> str:
    dsn = settings.database.dsn
    return dsn.replace("+asyncpg", "+psycopg") if "+asyncpg" in dsn else dsn


def run(
    settings: Settings,
    *,
    email: str,
    password: str | None = None,
    force: bool = False,
    rescue: bool = False,
) -> BootstrapReport:
    report = BootstrapReport(subcommand="create-admin")
    if not email or "@" not in email:
        raise ValueError("create-admin needs a valid --email.")

    engine = create_engine(_sync_dsn(settings))
    password = password or _generate_password()
    hasher = PasswordHasher()
    password_hash = hasher.hash(password)

    try:
        with engine.begin() as conn:
            if not _tables_exist(conn):
                report.skipped(
                    "admin",
                    "users/admins tables not yet migrated (Phase C ships them).",
                )
                return report

            existing = conn.execute(
                text("SELECT COUNT(*)::int AS n FROM admins")
            ).scalar_one()
            if existing > 0 and not (force or rescue):
                raise RuntimeError(
                    "admins table already contains rows; pass --force or --rescue."
                )
            if rescue and existing > 0:
                raise RuntimeError("--rescue only permitted when zero admins exist.")

            user_id = conn.execute(
                text(
                    """
                    INSERT INTO users (email, password_hash, status, email_verified)
                    VALUES (:email, :pw, 'active', TRUE)
                    RETURNING id
                    """
                ),
                {"email": email, "pw": password_hash},
            ).scalar_one()
            conn.execute(
                text("INSERT INTO admins (user_id) VALUES (:uid)"),
                {"uid": user_id},
            )
            report.did("admin", f"email={email}")
    except (OperationalError, ProgrammingError) as exc:
        report.skipped("admin", str(exc).splitlines()[0])
        return report
    finally:
        engine.dispose()

    # Print the generated password exactly once (O5.01).
    if password and report.changes and report.changes[-1].kind.value == "did":
        print(f"[create-admin] generated password for {email}: {password}")
    return report
