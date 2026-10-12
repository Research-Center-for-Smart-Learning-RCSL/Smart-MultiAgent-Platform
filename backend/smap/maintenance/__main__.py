"""`python -m smap.maintenance <subcommand>` entry point."""

from __future__ import annotations

import asyncio
import uuid

import typer
from loguru import logger

from contexts.identity.domain.errors import IdentityError

from . import email_domain_policy as _email_domain_policy
from . import encrypt_proxy_headers as _encrypt_proxy_headers
from . import neo4j_evidence_refs as _neo4j_evidence_refs
from . import purge_session_dirs as _purge_session_dirs
from . import rebuild_agent_group_concept_maps as _rebuild_group_maps
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


@app.command("encrypt-proxy-headers")
def encrypt_proxy_headers_cmd() -> None:
    """Encrypt legacy plaintext proxy_headers from migration 0088.

    Migration 0088 copies proxy_headers from config JSONB into the
    encrypted_proxy_headers column without Vault encryption (Vault may
    be sealed at migration time). This command re-encrypts any rows
    still holding unencrypted proxy_headers.

    Dry-run unless SMAP_ENCRYPT_PROXY_HEADERS_ARMED is set to a truthy
    value. Idempotent.
    """
    report = _encrypt_proxy_headers.run()
    logger.info(
        "encrypt-proxy-headers complete dry_run={} scanned={} already_encrypted={} encrypted={} failed={}",
        report.dry_run,
        report.scanned,
        report.already_encrypted,
        report.encrypted,
        report.failed,
    )
    if report.dry_run and report.encrypted:
        logger.warning(
            "{} row(s) need encryption. Re-run with {}=1 to encrypt.",
            report.encrypted,
            _encrypt_proxy_headers._ARMED_ENV,
        )
    if report.failed:
        raise typer.Exit(code=1)


@app.command("neo4j-evidence-refs")
def neo4j_evidence_refs_cmd() -> None:
    """Copy Neo4j relation evidence onto `evidence_refs` (rename release A).

    **Run only once release A is on every web and worker replica**; see
    docs/runbook-upgrade.md. Each config is copied under its build lock, in
    batches; a config that is building is skipped, which exits non-zero so the
    run is repeated. "remaining=0" is the gate for the rename's next release.

    Dry-run unless SMAP_NEO4J_EVIDENCE_REFS_ARMED is set to a truthy value.
    Idempotent.
    """
    report = asyncio.run(_neo4j_evidence_refs.run())
    logger.info(
        "neo4j-evidence-refs complete dry_run={} pending={} copied={} remaining={} "
        "busy={} lost_lock={} failed={} unattributed={}",
        report.dry_run,
        report.pending,
        report.copied,
        report.remaining,
        len(report.busy),
        len(report.lost_lock),
        len(report.failed),
        report.unattributed,
    )
    if report.dry_run and report.pending:
        logger.warning(
            "{} edge(s) need copying. Re-run with {}=1 to copy.",
            report.pending,
            _neo4j_evidence_refs.ARMED_ENV,
        )
    for label, ids in (
        ("skipped, building", report.busy),
        ("stopped, lock lost mid-copy (a build may have run alongside)", report.lost_lock),
        ("failed, see the error above", report.failed),
    ):
        if ids:
            logger.warning("{}: {}", label, ", ".join(str(c) for c in ids))
    if report.unattributed:
        logger.warning(
            "{} edge(s) have no usable graphrag_config_id; inspect them by hand", report.unattributed
        )
    if not report.dry_run and report.remaining:
        # Busy, lost, failed or unattributed configs, or edges an N-1 writer
        # changed during the run: the gate is not met, and a scripted run must see it.
        raise typer.Exit(code=1)


@app.command("rebuild-agent-group-concept-maps")
def rebuild_agent_group_concept_maps_cmd() -> None:
    """Rebuild every agent_group Concept Map in place so its member provenance is current.

    Needed once after the R11.22 provenance change: relations built earlier name
    only one member per room, and a delta build never rewrites them. Run it
    before Project Owners rely on per-member read filters; see
    docs/runbook-upgrade.md.

    Every listed map re-extracts its whole history on its project's builder
    key. Dry-run (lists the maps and their message counts) unless
    SMAP_REBUILD_AGENT_GROUP_CONCEPT_MAPS_ARMED is set to a truthy value.
    """
    report = asyncio.run(_rebuild_group_maps.run())
    for gm in report.maps:
        logger.info(
            "agent_group map config={} project={} group={} messages={}",
            gm.config_id,
            gm.project_id,
            gm.agent_group_id,
            gm.messages,
        )
    logger.info(
        "rebuild-agent-group-concept-maps complete dry_run={} maps={} messages={} "
        "enqueued={} deduped={} failed={}",
        report.dry_run,
        len(report.maps),
        sum(gm.messages for gm in report.maps),
        report.enqueued,
        report.deduped,
        len(report.failed),
    )
    if report.dry_run and report.maps:
        logger.warning(
            "Nothing was enqueued. Re-run with {}=1 to rebuild the maps above.",
            _rebuild_group_maps.ARMED_ENV,
        )
    if report.failed:
        logger.warning("enqueue failed for: {}", ", ".join(str(c) for c in report.failed))
        raise typer.Exit(code=1)


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
