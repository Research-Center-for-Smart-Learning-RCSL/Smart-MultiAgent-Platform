"""`python -m smap.maintenance <subcommand>` entry point."""

from __future__ import annotations

import asyncio
import uuid

import typer
from loguru import logger

from contexts.identity.domain.errors import IdentityError

from . import email_domain_policy as _email_domain_policy
from . import purge_session_dirs as _purge_session_dirs
from . import reconcile_attachment_sizes as _reconcile_attachment_sizes
from . import reconcile_model_catalog as _reconcile_model_catalog
from . import repair_compaction_summaries as _repair_compaction_summaries

app = typer.Typer(
    help="SMAP one-shot maintenance CLI. Every destructive command is dry-run until armed.",
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
    """Force group mode.

    Typer collapses a single-command app into the root, which drops the
    subcommand name from the invocation: `python -m smap.maintenance
    purge-session-dirs` then fails with "Got unexpected extra argument". A
    callback keeps the group even with one command, so the documented
    invocation works and stays stable when a second command is added.
    """


@app.command("purge-session-dirs")
def purge_session_dirs_cmd() -> None:
    """Clear legacy /workspace/sessions/ trees from per-agent volumes.

    Repairs the cross-room exposure fixed by 2026-07-19-session-dir-room-isolation:
    session state moved to a per-room volume, but volumes created before that keep
    their old tree, still readable from every room the agent serves.

    Dry-run unless SMAP_PURGE_SESSION_DIRS_ARMED is set to a truthy value. That is
    an environment variable rather than a --arm flag because typer 0.12.5 against
    the installed click mis-converts flag defaults, which inverted exactly this
    decision; see purge_session_dirs._ARMED_ENV.

    Idempotent -- safe to re-run after a partial failure.
    """
    try:
        report = _purge_session_dirs.run()
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


@app.command("reconcile-attachment-sizes")
def reconcile_attachment_sizes_cmd() -> None:
    """Report attachments whose stored object size disagrees with `size_bytes`.

    Surfaces what the TUS partial-write defect fixed by
    2026-07-22-attachment-lifecycle-and-rendering may already have written: a
    staged file longer than declared, recorded with the client-declared length.

    Read-only -- no delete, no re-derive, no status change, so it needs no
    arming. Detection is partial: no content digest is stored for chat
    attachments, so a corrupt file whose length matches the declared length is
    undetectable. A clean report means no length disagreement was found, not
    that no corruption exists. Escalate any finding for manual review.
    """
    report = _reconcile_attachment_sizes.run()
    logger.info(
        "reconcile-attachment-sizes complete examined={} mismatched={} missing_unexpectedly={} unreadable={}",
        report.examined,
        len(report.mismatched),
        len(report.missing_unexpectedly),
        report.unreadable,
    )
    for m in report.mismatched:
        logger.warning(
            "size mismatch attachment={} path={} recorded={} stored={}",
            m.attachment_id,
            m.minio_path,
            m.recorded_bytes,
            m.stored_bytes,
        )
    for attachment_id in report.missing_unexpectedly:
        logger.warning("object missing while row is still live attachment={}", attachment_id)
    if report.unreadable:
        # Not a failure exit: the examined rows still produced a usable report,
        # and an operator needs to see it rather than a traceback.
        logger.warning(
            "{} object(s) could not be read; those rows were not reconciled",
            report.unreadable,
        )


@app.command("repair-compaction-summaries")
def repair_compaction_summaries_cmd() -> None:
    """Void compaction summaries left bad by the pre-scoping compaction defects.

    Repairs what `docs/tasks/2026-07-22-compaction-scoping-and-durability` fixed
    prospectively: summaries written from an empty provider response, and
    overlapping folds by one agent caused by the lock releasing before the
    summary committed.

    Only summary-row metadata is edited -- compaction never deleted a folded
    message, so no conversation content is at risk. Summaries with no producer
    are counted but not touched: the loader already treats them as belonging to
    no one, so those rooms have their full history back already.

    Dry-run unless SMAP_REPAIR_COMPACTION_SUMMARIES_ARMED is set to a truthy
    value; see repair_compaction_summaries._ARMED_ENV for why that is an
    environment variable rather than a flag. Scans `messages` without a metadata
    index -- run it in a maintenance window.
    """
    report = _repair_compaction_summaries.run()
    if report.dry_run and report.would_void:
        logger.warning("Nothing was changed. Re-run armed once the above looks right.")
    if report.orphaned:
        logger.warning(
            "{} summary row(s) cover deleted messages; reported only, see the module docstring",
            len(report.orphaned),
        )


@app.command("reconcile-model-catalog")
def reconcile_model_catalog_cmd(
    provider: str = typer.Option(..., help="claude | openai | gemini"),
    key_id: uuid.UUID = typer.Option(..., help="An active api_keys row for that provider."),
) -> None:
    """Diff the capability table's model lineup against one provider's live
    `models` endpoint (Q-4, R9.03a).

    Read-only and report-only: it never edits `model_specs.py`. `stale`
    entries are catalogued but no longer served upstream; `unseen` entries
    are served but not catalogued (Q-2's floor already covers a BYO-key
    user naming one -- this is a "consider adding a row" list). Reads the
    key via the same envelope-decrypt path every provider call uses; the
    key itself is never logged.
    """
    report = asyncio.run(_reconcile_model_catalog.run(provider=provider, key_id=key_id))
    logger.info(
        "reconcile-model-catalog complete provider={} catalogued={} upstream={}",
        report.provider,
        len(report.catalogued),
        len(report.upstream),
    )
    if report.stale:
        logger.warning("catalogued but no longer served: {}", sorted(report.stale))
    if report.unseen:
        logger.info("served but not catalogued: {}", sorted(report.unseen))
    if not report.stale and not report.unseen:
        logger.info("table matches upstream for {}", provider)


def _run_transition(name: str, coro_factory: object) -> None:
    """Run one rollout transition and print the resulting state and version.

    Every failure exits non-zero and is reported as a message rather than a
    traceback: these are operator commands whose failure modes are expected
    conditions (wrong phase, unverifiable mirror), and a stack trace would read
    as a bug rather than as "do not proceed".

    `IdentityError` is caught alongside the transition error because the
    transitions read and write the legacy Redis keys: an unreachable Redis
    (`EmailDomainPolicyUnavailable`) or a corrupt triple
    (`InvalidLegacyEmailDomainPolicy`) are expected conditions here too, and are
    exactly the moment an operator needs a sentence rather than a traceback —
    they are deciding whether the freeze committed and whether it is safe to
    start an old image.
    """
    try:
        report = asyncio.run(coro_factory())  # type: ignore[operator]
    except (_email_domain_policy.RolloutTransitionError, IdentityError) as exc:
        logger.error("{} could not run: {}", name, exc)
        raise typer.Exit(code=1) from None
    logger.info(
        "{} complete changed={} rollout_state={} version={} legacy_mirrored_version={}",
        name,
        report.changed,
        report.rollout_state,
        report.version,
        report.legacy_mirrored_version,
    )
    if not report.changed:
        logger.warning("Nothing changed -- the policy was already in that state.")


@app.command("activate-email-domain-policy")
def activate_email_domain_policy_cmd() -> None:
    """Make PostgreSQL authoritative for the email-domain policy (R19a.13).

    **Run only after every replica of the previous image has drained.** Those
    replicas read the three `config:email_domain:*` Redis keys and know nothing
    about the policy row, so activating while one is still serving means two
    versions enforcing two policies. Nothing here can verify that they are gone:
    automatic replica discovery is out of scope, so the assertion is yours, and
    setting SMAP_ACTIVATE_EMAIL_DOMAIN_POLICY_ARMED is where you make it.

    Adopts one final snapshot of the legacy keys as it switches, so a
    compatibility-era `redis-cli` edit is not silently reverted at the moment
    PostgreSQL takes over. Idempotent: re-running against an already-active row
    reports the state it found and changes nothing.
    """
    if not _email_domain_policy.activation_armed():
        logger.error(
            "Refusing to activate: set {}=1 to assert that every replica running the "
            "previous image has drained. See docs/operations.md 7a.6.",
            _email_domain_policy.ACTIVATE_ARMED_ENV,
        )
        raise typer.Exit(code=1)
    _run_transition("activate-email-domain-policy", _email_domain_policy.activate)


@app.command("prepare-email-domain-policy-rollback")
def prepare_email_domain_policy_rollback_cmd() -> None:
    """Fence policy writes and mirror the policy into the legacy Redis keys.

    Freezes first, then writes all three legacy keys atomically and reads them
    back; only an exact readback records `legacy_mirrored_version`. That marker
    equalling `version` is the documented precondition for starting an old image
    or running the Alembic downgrade -- Alembic touches PostgreSQL only and can
    neither write nor check Redis.

    A failure leaves the policy frozen and unmarked, which is safe (writes stay
    fenced, new readers keep reading PostgreSQL) and safe to retry. **Do not
    proceed to an old image after a failed run.**
    """
    _run_transition("prepare-email-domain-policy-rollback", _email_domain_policy.prepare_rollback)


@app.command("cancel-email-domain-policy-rollback")
def cancel_email_domain_policy_rollback_cmd() -> None:
    """Lift the write fence after a prepared rollback was not taken.

    Returns the policy to `active` and clears `legacy_mirrored_version`, because
    a marker that outlives its freeze would vouch for a legacy snapshot the next
    Admin edit invalidates. Run it only once the old images are gone again.
    """
    _run_transition("cancel-email-domain-policy-rollback", _email_domain_policy.cancel_rollback)


if __name__ == "__main__":
    app()
