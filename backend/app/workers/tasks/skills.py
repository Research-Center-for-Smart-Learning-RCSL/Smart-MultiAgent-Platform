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
        async with sm() as db, db.begin():
            await SkillFileRepository(db).mark_scan(fid, scan_status=SkillScanStatus.CLEAN)
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
            await SkillFileRepository(db).mark_scan(fid, scan_status=SkillScanStatus.SKIPPED)
        return "skipped:too_large"

    minio = get_minio_client()
    data = await minio.get_object(bucket=minio.skill_bundles_bucket, key=f.minio_key)

    try:
        result = await scanner.scan(data)
    except ScanError:
        _log.exception("skill_scan_file: ClamAV error for file %s", file_id)
        async with sm() as db, db.begin():
            await SkillFileRepository(db).mark_scan(fid, scan_status=SkillScanStatus.SKIPPED)
        # Re-raised so Arq retries: SKIPPED is terminal for readability here, so a
        # transient ClamAV blip that goes unretried would leave a legitimate skill dark.
        # The row is marked first so the gate is closed *during* the retries, not after.
        raise

    from shared_kernel import audit

    scan_status = SkillScanStatus.CLEAN if result.clean else SkillScanStatus.QUARANTINED
    if not result.clean:
        _log.warning("skill_scan_file: file %s quarantined — threat=%s", file_id, result.threat_name)

    async with sm() as db, db.begin():
        await SkillFileRepository(db).mark_scan(fid, scan_status=scan_status)
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

__all__ = ["skill_scan_file"]
