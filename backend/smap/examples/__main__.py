"""`python -m smap.examples <subcommand>` entry point."""

from __future__ import annotations

import uuid

import typer
from loguru import logger

from app.plugins.activity_validators import register_first_party_validators

from ._catalogue import CourseFileInvalid, load_course
from ._seeding import run as run_seed

DEFAULT_COURSE = "creative-thinking"

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
def seed_course_cmd(
    project_id: str = typer.Option(..., "--project-id"),
    owner_user_id: str = typer.Option(..., "--owner-user-id"),
    course: str = typer.Option(DEFAULT_COURSE, "--course"),
) -> None:
    """Register a shipped example course's activity types into a project.

    Defaults to the two-unit creative-thinking course: `mandala-9grid` (unit 2,
    rendered by the bundled nine-grid plugin) and `six-hats-emotion-desk` (unit 4,
    rendered by the generic schema form). Both score with the `filled_count`
    validator. --course names any file in
    contexts/activities/infrastructure/examples/courses/.

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

    # Before load_course, not just before seeding: the catalogue now checks a
    # course's validator_config against the in-process registry, and a CLI process
    # runs no app startup steps, so an unpopulated registry would read every
    # shipped course as naming an unregistered validator. Idempotent, and
    # `_seeding` calls it again for the case where seeding is driven directly.
    register_first_party_validators()

    try:
        definition = load_course(course)
    except CourseFileInvalid as exc:
        logger.error("creative-thinking-course cannot read the course: {}", exc)
        raise typer.Exit(code=1) from None
    except Exception:
        # A course file is hand-edited data, so anything the loader did not
        # anticipate is still an operator error rather than a crash: exit the
        # same way, and keep the traceback in the log instead of on the terminal.
        logger.exception("creative-thinking-course could not load course {}", course)
        raise typer.Exit(code=1) from None

    try:
        report = run_seed(
            project_id=project,
            owner_user_id=owner,
            activity_types=definition.activity_types,
        )
    except Exception:
        logger.exception("creative-thinking-course failed")
        raise typer.Exit(code=1) from None

    logger.info(
        "creative-thinking-course complete course={} created={} already_present={}",
        definition.course_key,
        report.created,
        report.already_present,
    )


if __name__ == "__main__":
    app()
