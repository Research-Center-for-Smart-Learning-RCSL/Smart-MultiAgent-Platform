"""AV scan for bundled skill files (§31, AC-34 / [R31.20]).

Mirrors `rag_scan_document` deliberately — same scanner, same settings, same retry
shape — with one semantic difference that is the whole point of the file: **a non-clean
verdict here makes the skill unreadable**, where a non-clean RAG document is still
retrievable. A RAG chunk is data the model reads; a skill is instructions it executes
(§8). So the statuses this worker writes are load-bearing in a way the RAG ones are not,
and `skipped` is a refusal rather than a shrug.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.config.settings import get_settings
from contexts.skills.domain.models import SkillScanStatus
from contexts.skills.infrastructure.repositories import SkillFileRepository
from shared_kernel.db.session import get_sessionmaker

_log = logging.getLogger(__name__)


async def skill_scan_file(ctx: dict[str, Any], *, file_id: str) -> str:
    """Scan one `skill_files` row's bytes and record the verdict."""
    _ = ctx
    fid = uuid.UUID(file_id)
    sm = get_sessionmaker()

    if not get_settings().security.file_scan_enabled:
        # No scanner deployed. `file_service._initial_scan_status` already wrote CLEAN on
        # this path, so this is only reached if the setting flipped between upload and
        # scan; writing CLEAN again keeps the two agreeing rather than stranding the row.
        async with sm() as db:
            repo = SkillFileRepository(db)
            existing = await repo.get(fid)
            if existing is None:
                return "not_found"
            async with db.begin():
                await repo.mark_scan(fid, scan_status=SkillScanStatus.CLEAN, expected_sha256=existing.sha256)
        return SkillScanStatus.CLEAN.value

    from shared_kernel.scanning import ScanError, get_scanner
    from shared_kernel.storage.minio_client import get_minio_client

    scanner = get_scanner()
    if scanner is None:
        raise RuntimeError("file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set")

    settings = get_settings()
    async with sm() as db:
        f = await SkillFileRepository(db).get(fid)
    if f is None:
        _log.warning("skill_scan_file: file %s not found", file_id)
        return "not_found"
    # The sha of the bytes this run is about. Every verdict below is conditional on it,
    # because the row is read here and written in a later session, and `update_content`
    # can replace the bytes in between — see `SkillFileRepository.mark_scan`.
    scanned_sha = f.sha256

    if f.size_bytes > settings.security.clamav_max_scan_bytes:
        # Unreachable at stock settings — a skill file caps at 32 MiB (Q-17) and the scan
        # limit defaults to 100 MiB — but an operator can lower the limit under the cap.
        # SKIPPED then means the skill stays unreadable, which is fail-closed and correct:
        # we cannot vouch for bytes we did not scan, and R31.20 makes that a refusal.
        _log.warning(
            "skill_scan_file: file %s skipped — %d bytes exceeds scan limit %d; "
            "the owning skill stays unreadable (R31.20)",
            file_id,
            f.size_bytes,
            settings.security.clamav_max_scan_bytes,
        )
        async with sm() as db, db.begin():
            await SkillFileRepository(db).mark_scan(
                fid, scan_status=SkillScanStatus.SKIPPED, expected_sha256=scanned_sha
            )
        return "skipped:too_large"

    minio = get_minio_client()
    data = await minio.get_object(bucket=minio.skill_bundles_bucket, key=f.minio_key)

    try:
        result = await scanner.scan(data)
    except ScanError:
        _log.exception("skill_scan_file: ClamAV error for file %s", file_id)
        async with sm() as db, db.begin():
            await SkillFileRepository(db).mark_scan(
                fid, scan_status=SkillScanStatus.SKIPPED, expected_sha256=scanned_sha
            )
        # Re-raised so Arq retries: SKIPPED is terminal for readability here, so a
        # transient ClamAV blip that goes unretried would leave a legitimate skill dark.
        # The row is marked first so the gate is closed *during* the retries, not after.
        raise

    from shared_kernel import audit

    scan_status = SkillScanStatus.CLEAN if result.clean else SkillScanStatus.QUARANTINED
    if not result.clean:
        _log.warning("skill_scan_file: file %s quarantined — threat=%s", file_id, result.threat_name)

    async with sm() as db, db.begin():
        landed = await SkillFileRepository(db).mark_scan(
            fid, scan_status=scan_status, expected_sha256=scanned_sha
        )
        if not landed:
            # The bytes were replaced while this scan ran, so the verdict is about a file
            # that is no longer stored. Dropping it is the only safe move in *both*
            # directions: a stale `clean` would overwrite the replacement's `quarantined`
            # and defeat the gate permanently, and a stale `quarantined` would brick a
            # skill whose current file is fine. The replacement's own scan is already
            # queued under its own sha and is the authority on the bytes that exist.
            _log.warning(
                "skill_scan_file: verdict %s for file %s discarded — its bytes were "
                "replaced while the scan ran",
                scan_status.value,
                file_id,
            )
            return "superseded"
        if scan_status is SkillScanStatus.QUARANTINED:
            await audit.emit(
                db,
                audit.AuditEvent(
                    action="skill.file_quarantined",
                    # The skill, not the file: a file has no independent lifecycle, and
                    # the rest of §31's trail keys on the skill (R31.25).
                    resource_type="skill",
                    resource_id=f.skill_id,
                    metadata={
                        "path": f.path,
                        "sha256": scanned_sha,
                        "scan_status": scan_status.value,
                        "threat_name": result.threat_name,
                    },
                ),
            )
    return scan_status.value


# Arq's default is 1 (no retry); 3 gives two automatic retries before the dead-letter
# queue. It matters more here than for RAG: every retry the scanner fails is time the
# skill spends unreadable.
skill_scan_file.max_tries = 3  # type: ignore[attr-defined]


def _client_reason(exc: Exception) -> str:
    """The RFC 7807 title a skills error maps to, for the status endpoint.

    A `BundleInvalid`/`BundleQuarantined`/`SkillTextRejected` carries a reason written for
    the author; anything else gets a generic line so a worker-side stack trace never
    reaches the client.

    `SkillTextRejected` is the service-layer charset gate, which exists precisely to catch
    what a writer's own checks missed — so the one case where it fires on the import path
    is the one where the parser let something through, and discarding its reason would
    leave the author with "import failed" and no field to look at.
    """
    from contexts.skills.domain.errors import BundleInvalid, BundleQuarantined, SkillTextRejected

    if isinstance(exc, BundleInvalid | BundleQuarantined | SkillTextRejected):
        return str(exc)
    return "import failed"


async def _audit_bundle_imported(
    sm: Any, *, result: Any, actor_user_id: str, actor_ip: str | None, request_id: str | None
) -> None:
    """Emit R31.25's `skill.bundle_imported` for a completed import — best effort.

    The import is already committed and marked ready before this runs, so an audit-write
    fault must not fail the job (that would report a live skill as failed). It is logged at
    error level rather than swallowed silently: a gap in the mutation trail is an operator's
    problem to see, not a secret to keep.
    """
    from shared_kernel import audit

    try:
        async with sm() as db, db.begin():
            await audit.emit(
                db,
                audit.AuditEvent(
                    action="skill.bundle_imported",
                    actor_user_id=uuid.UUID(actor_user_id),
                    actor_ip=actor_ip,
                    resource_type="skill",
                    resource_id=result.skill.id,
                    metadata={
                        "scope": result.skill.scope.value,
                        "name": result.skill.name,
                        "source": result.skill.source.value,
                        "bundle_sha256": result.skill.bundle_sha256,
                        "file_count": len(result.files),
                        "warning_count": len(result.warnings),
                    },
                    request_id=uuid.UUID(request_id) if request_id else None,
                ),
            )
    except Exception:
        _log.exception(
            "skill_import_bundle: skill %s imported but its audit event failed to write",
            result.skill.id,
        )


async def skill_import_bundle(
    ctx: dict[str, Any],
    *,
    job_id: str,
    object_key: str,
    scope: str,
    owner_id: str | None,
    actor_user_id: str,
    org_key: str,
    actor_ip: str | None = None,
    request_id: str | None = None,
) -> str:
    """Import one staged bundle (§31, D-58). Reads the zip from MinIO, writes the skill.

    Single-attempt by design (no `max_tries`): `SkillsFacade.import_bundle` commits the
    created skill mid-way, so a retry after that commit would create a **second** skill from
    the same bytes. A transient fault therefore fails the job rather than re-running it, and
    the user re-uploads — the correct trade for a create, unlike the scan worker's retry.
    The slot release and the staging delete run in `finally` so neither leaks on any path.
    """
    _ = ctx
    from contexts.skills.application import bundle_jobs
    from contexts.skills.domain.errors import BundleInvalid, BundleQuarantined, SkillTextRejected
    from contexts.skills.domain.models import SkillScope
    from contexts.skills.interfaces.facade import SkillsFacade
    from shared_kernel.storage import get_minio_client

    jid = uuid.UUID(job_id)
    sm = get_sessionmaker()
    minio = get_minio_client()

    try:
        # Inside the try so the `finally` always covers the slot and staging, even if the
        # first Redis write faults.
        await bundle_jobs.mark_import_running(jid)
        data = await minio.get_object(bucket=minio.skill_bundles_bucket, key=object_key)
        async with sm() as db:
            result = await SkillsFacade(db).import_bundle(
                data=data,
                scope=SkillScope(scope),
                owner_id=uuid.UUID(owner_id) if owner_id else None,
                actor_user_id=uuid.UUID(actor_user_id),
                actor_ip=actor_ip,
                request_id=uuid.UUID(request_id) if request_id else None,
            )
        # The import is done and committed — mark it ready first, so a later fault (the audit
        # write) cannot flip a succeeded import to FAILED. A false 'failed' would send the
        # user to re-import a skill that already exists, where the name collision then fails
        # the retry too, stranding a live skill the UI calls failed.
        await bundle_jobs.mark_import_ready(job_id=jid, skill_id=result.skill.id, warnings=result.warnings)
        await _audit_bundle_imported(
            sm, result=result, actor_user_id=actor_user_id, actor_ip=actor_ip, request_id=request_id
        )
        return "imported"
    except (BundleInvalid, BundleQuarantined, SkillTextRejected) as exc:
        # A rejection, not a fault: it belongs here rather than in the `Exception` arm,
        # which logs a full stack trace for what is routine validation.
        _log.warning("skill_import_bundle: job %s rejected — %s", job_id, exc)
        await bundle_jobs.mark_import_failed(job_id=jid, error=_client_reason(exc))
        return "rejected"
    except Exception as exc:
        _log.exception("skill_import_bundle: job %s failed", job_id)
        await bundle_jobs.mark_import_failed(job_id=jid, error=_client_reason(exc))
        return "failed"
    finally:
        await bundle_jobs.release_import_slot(org_key)
        await minio.remove(bucket=minio.skill_bundles_bucket, key=object_key)


async def skill_export_bundle(
    ctx: dict[str, Any],
    *,
    job_id: str,
    skill_id: str,
    scope: str,
    owner_id: str | None,
) -> str:
    """Pack a skill into a `.zip` in the exports bucket (§31, D-58 / Q-21).

    The reader fetches it by presigned URL from the status endpoint. `export_bundle` applies
    AC-34's fail-closed gate, so a quarantined file fails the job with `skills/unreadable`
    rather than shipping bytes the reader would refuse.
    """
    _ = ctx
    from contexts.skills.application import bundle_jobs
    from contexts.skills.domain.models import SkillScope
    from contexts.skills.interfaces.facade import SkillsFacade
    from shared_kernel.storage import get_minio_client
    from shared_kernel.storage.minio_client import skill_export_key

    jid = uuid.UUID(job_id)
    sm = get_sessionmaker()
    minio = get_minio_client()

    await bundle_jobs.mark_export_running(jid)
    wrote_key: str | None = None
    try:
        async with sm() as db:
            data = await SkillsFacade(db).export_bundle(
                uuid.UUID(skill_id),
                SkillScope(scope),
                owner_id=uuid.UUID(owner_id) if owner_id else None,
            )
        key = skill_export_key(job_id=jid)
        await minio.put_object(
            bucket=minio.exports_bucket, key=key, data=data, content_type="application/zip"
        )
        wrote_key = key
        await bundle_jobs.mark_export_ready(job_id=jid, bucket=minio.exports_bucket, object_key=key)
        return "exported"
    except Exception as exc:
        _log.exception("skill_export_bundle: job %s failed", job_id)
        from contexts.skills.domain.errors import SkillError

        # A domain error (unreadable, not-found) carries a client-safe message; anything else
        # is generic so no worker internal reaches the client.
        reason = str(exc) if isinstance(exc, SkillError) else "export failed"
        await bundle_jobs.mark_export_failed(job_id=jid, error=reason)
        # If the zip was written but `mark_export_ready` then faulted, the client will see
        # 'failed' and never fetch it — delete the orphan rather than lean on the bucket's
        # 24 h lifecycle to reclaim a file no one can reach.
        if wrote_key is not None:
            await minio.remove(bucket=minio.exports_bucket, key=wrote_key)
        return "failed"


__all__ = ["skill_export_bundle", "skill_import_bundle", "skill_scan_file"]
