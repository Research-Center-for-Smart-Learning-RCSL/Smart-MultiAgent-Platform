"""`python -m smap.maintenance <subcommand>` entry point."""

from __future__ import annotations

import typer
from loguru import logger

from . import purge_session_dirs as _purge_session_dirs

app = typer.Typer(
    help="SMAP one-shot maintenance CLI. Every destructive command is dry-run until armed.",
    no_args_is_help=True,
)


@app.command("purge-session-dirs")
def purge_session_dirs_cmd(
    arm: bool = typer.Option(
        False,
        "--arm",
        help="Actually delete. Without this the command only reports what it would do.",
    ),
) -> None:
    """Clear legacy /workspace/sessions/ trees from per-agent volumes.

    Repairs the cross-room exposure fixed by 2026-07-19-session-dir-room-isolation:
    session state moved to a per-room volume, but volumes created before that keep
    their old tree, still readable from every room the agent serves.

    Idempotent -- safe to re-run after a partial failure.
    """
    try:
        report = _purge_session_dirs.run(armed=arm)
    except _purge_session_dirs.PurgeUnavailable as exc:
        # Not a traceback: this is the expected failure when the command is run
        # somewhere without a daemon, and it must not read as "nothing to do".
        logger.error("purge-session-dirs could not run: {}", exc)
        raise typer.Exit(code=1) from None
    logger.info(
        "purge-session-dirs complete dry_run={} volumes_seen={} purged={} would_purge={} failed={}",
        report.dry_run,
        report.seen,
        report.purged,
        report.would_purge,
        report.failed,
    )
    if report.dry_run and report.would_purge:
        logger.warning("Nothing was deleted. Re-run with --arm once the above looks right.")
    if report.failed:
        # A partial repair leaves some volumes still exposed, so this must not
        # exit 0 -- an operator scripting it needs the failure to surface.
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
