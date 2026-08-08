"""`python -m smap.examples <subcommand>` entry point."""

from __future__ import annotations

import uuid

import typer
from loguru import logger

from . import creative_thinking_course as _creative_thinking_course

app = typer.Typer(
    help="SMAP worked-example seeder. Registers example activity types into an existing project.",
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
    """Force group mode.

    Typer collapses a single-command app into the root, which drops the
    subcommand name from the invocation: `python -m smap.examples
    creative-thinking-course` then fails with "Got unexpected extra argument". A
    callback keeps the group even with one command, so the documented invocation
    works and stays stable when a second example is added.
    """


@app.command("creative-thinking-course")
def creative_thinking_course_cmd(
    project_id: str = typer.Option(..., "--project-id"),
    owner_user_id: str = typer.Option(..., "--owner-user-id"),
) -> None:
    """Register the two-unit creative-thinking course example into a project.

    Seeds `mandala-9grid` (unit 2, rendered by the bundled nine-grid plugin) and
    `six-hats-emotion-desk` (unit 4, rendered by the generic schema form). Both
    score with the `filled_count` validator.

    Idempotent -- a type whose key already exists is left untouched, so re-running
    after a partial failure is safe.

    Like every smap CLI this trusts its operator: it calls the facade directly and
    so bypasses the HTTP route's Project Owner check. --owner-user-id is recorded
    as the audit actor and authorizes nothing.
    """
    try:
        project = uuid.UUID(project_id)
        owner = uuid.UUID(owner_user_id)
    except ValueError as exc:
        logger.error("creative-thinking-course needs valid UUIDs: {}", exc)
        raise typer.Exit(code=1) from None

    try:
        report = _creative_thinking_course.run(project, owner)
    except Exception:
        logger.exception("creative-thinking-course failed")
        raise typer.Exit(code=1) from None

    logger.info(
        "creative-thinking-course complete created={} already_present={}",
        report.created,
        report.already_present,
    )


if __name__ == "__main__":
    app()
