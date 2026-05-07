"""Bootstrap CLI entry point (`python -m smap.bootstrap <subcommand>`).

Composed with Typer for ergonomics; each subcommand delegates to its sibling
module. `all` runs them in the dependency order documented in
`docs/operations.md` §5:

  vault-init → vault-approle → db-init → minio-init → qdrant-init → neo4j-init
  → create-admin (Phase C-dependent)

Every subcommand is idempotent; replaying `all` against a bootstrapped stack
prints only `already-present` entries.
"""

from __future__ import annotations

import typer
from loguru import logger

from app.config.settings import get_settings

from . import (
    create_admin as _create_admin,
)
from . import (
    db_init as _db_init,
)
from . import (
    minio_init as _minio_init,
)
from . import (
    neo4j_init as _neo4j_init,
)
from . import (
    qdrant_init as _qdrant_init,
)
from . import (
    vault_approle as _vault_approle,
)
from . import (
    vault_init as _vault_init,
)

app = typer.Typer(help="SMAP bootstrap CLI (Phase B).", no_args_is_help=True)


@app.command("vault-init")
def vault_init(
    root_token: str | None = typer.Option(None, "--root-token", help="Operator root/dev token."),
) -> None:
    _vault_init.run(get_settings(), root_token=root_token).print_human()


@app.command("vault-approle")
def vault_approle(
    root_token: str | None = typer.Option(None, "--root-token"),
    rotate_secret_id: bool = typer.Option(
        False,
        "--rotate-secret-id",
        help="Force-rotate secret_id even for an existing role.",
    ),
) -> None:
    report, creds = _vault_approle.run(
        get_settings(), root_token=root_token, rotate_secret_id=rotate_secret_id
    )
    report.print_human()
    # Secret_id only lands on stdout once (O5.01-style); operator must capture.
    for c in creds:
        if c.secret_id is not None:
            typer.echo(f"[vault-approle] role={c.name} role_id={c.role_id} " f"secret_id={c.secret_id}")


@app.command("db-init")
def db_init() -> None:
    _db_init.run(get_settings()).print_human()


@app.command("minio-init")
def minio_init(
    root_token: str | None = typer.Option(None, "--root-token"),
) -> None:
    _minio_init.run(get_settings(), root_token=root_token).print_human()


@app.command("qdrant-init")
def qdrant_init() -> None:
    _qdrant_init.run(get_settings()).print_human()


@app.command("neo4j-init")
def neo4j_init() -> None:
    _neo4j_init.run(get_settings()).print_human()


@app.command("create-admin")
def create_admin(
    email: str = typer.Option(..., "--email"),
    password: str | None = typer.Option(None, "--password"),
    force: bool = typer.Option(False, "--force"),
    rescue: bool = typer.Option(False, "--rescue"),
) -> None:
    _create_admin.run(
        get_settings(),
        email=email,
        password=password,
        force=force,
        rescue=rescue,
    ).print_human()


@app.command("all")
def run_all(
    root_token: str | None = typer.Option(None, "--root-token"),
    admin_email: str | None = typer.Option(None, "--admin-email"),
) -> None:
    settings = get_settings()
    steps = (
        ("vault-init", lambda: _vault_init.run(settings, root_token=root_token)),
        (
            "vault-approle",
            lambda: _vault_approle.run(settings, root_token=root_token)[0],
        ),
        ("db-init", lambda: _db_init.run(settings)),
        ("minio-init", lambda: _minio_init.run(settings, root_token=root_token)),
        ("qdrant-init", lambda: _qdrant_init.run(settings)),
        ("neo4j-init", lambda: _neo4j_init.run(settings)),
    )
    for name, fn in steps:
        try:
            fn().print_human()  # type: ignore[no-untyped-call]
        except Exception:
            logger.exception("bootstrap step {} failed", name)
            raise typer.Exit(code=1) from None
    if admin_email:
        _create_admin.run(settings, email=admin_email).print_human()


if __name__ == "__main__":
    app()
