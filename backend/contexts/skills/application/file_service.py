"""Bundled skill files — create, edit, delete, and the read gate (§31, Phase 2).

Two authoring paths, one row shape (Q-20): a file may be uploaded or typed into the UI,
and an uploaded file stays editable afterwards. Import-then-freeze was rejected because
it blocks localising an imported skill, so `source` and `bundle_sha256` are kept for a
"diverged" badge rather than used to lock anything.

Ownership is proven by the caller through `SkillService.get_owned` before anything here
runs — this module never takes a scope. That is deliberate: a second ownership check with
its own copy of the 404-not-403 rule is a second place for the rule to drift.

Bytes live in MinIO under `skill_file_key`; the row carries the metadata. The two can
disagree if a write fails between them, so ordering is chosen to fail safe: bytes first,
row second. An orphaned object is invisible and costs storage; an orphaned row is a file
the model is told exists and cannot read.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.skills.domain.errors import (
    SkillFileLimitExceeded,
    SkillFileNotEditable,
    SkillFileNotFound,
    SkillFilePathTaken,
)
from contexts.skills.domain.models import Skill, SkillFile, SkillFileKind, SkillScanStatus
from contexts.skills.domain.text_rules import path_collision_key
from contexts.skills.infrastructure.repositories import SkillFileRepository
from shared_kernel import audit
from shared_kernel.storage.minio_client import get_minio_client, skill_file_key
from shared_kernel.text_extraction.parsers import MIME_TO_PARSER, ParserError, normalise_mime

_log = logging.getLogger(__name__)

# Q-17's per-file ceiling, and the same number as the multipart cap the RAG path uses
# (`ingest_service.MAX_MULTIPART_BYTES`). That coincidence is load-bearing rather than
# lucky: it is why Phase 2 needs no tus path — a single skill file can never exceed what
# one multipart request carries. Bundles are the thing that needs tus, and they are
# Phase 4.
MAX_SKILL_FILE_BYTES = 32 * 1024 * 1024

# Q-17's per-bundle entry limit, applied to the per-file API so the two agree. It is not a
# new number: a bundle may carry ≤ 500 entries, so a skill assembled one file at a time
# must not be able to exceed what the same skill could carry as a bundle — otherwise
# Phase 4's exporter would emit skills its own importer rejects.
#
# What it does *not* do: retire `_fit_manifest`. 500 entries render ~42 500 bytes against
# read_skill's 16 000 cap (only ~188 short paths fit), so the render still needs its own
# bound. This one bounds storage — `skill-bundles` has no TTL and both delete and edit
# orphan their objects (FU-29) — and the O(n) path scan `_assert_path_free` does per add.
MAX_SKILL_FILES = 500

# `kind` is derived from the path's top-level directory, never taken from the client.
# R31.18 ties each directory to a behaviour — `references/` is text-extracted and served
# by `read_skill`, `scripts/` is staged for the interpreter, `assets/` is opaque — so a
# client-chosen kind would let an uploader put a script under `assets/` and have it
# staged, or mark a binary `reference` and have `read_skill` render its bytes.
# This dict must cover exactly `ALLOWED_TOP_LEVEL_DIRS`: a directory the path rule admits
# but this map misses would KeyError at upload rather than 422 at validation. The
# correspondence is pinned by `test_skill_files.py` rather than asserted here — a bare
# `assert` is stripped under `-O`, so it would vanish in exactly the deployment that
# needs it.
_KIND_BY_TOP_LEVEL: dict[str, SkillFileKind] = {
    "references": SkillFileKind.REFERENCE,
    "scripts": SkillFileKind.SCRIPT,
    "assets": SkillFileKind.ASSET,
}


def kind_for_path(path: str) -> SkillFileKind:
    """R31.18 — the directory decides the kind. Assumes `skill_file_path_reason` passed."""
    return _KIND_BY_TOP_LEVEL[path.split("/")[0]]


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _initial_scan_status(settings_scan_enabled: bool) -> SkillScanStatus:
    """`pending` when a scanner will run, `clean` when none exists to run.

    Mirrors `rag_scan_document`'s first branch, which marks CLEAN when
    `file_scan_enabled` is False. Without this, a self-hosted deployment with no ClamAV
    would leave every file `pending` forever, and AC-34's gate would make every skill
    permanently unreadable — the fail-closed rule turning into a fail-shut one.
    """
    return SkillScanStatus.PENDING if settings_scan_enabled else SkillScanStatus.CLEAN


class SkillFileService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._files = SkillFileRepository(db)

    async def list_for_skill(self, skill_id: uuid.UUID) -> list[SkillFile]:
        return await self._files.list_for_skill(skill_id)

    async def get_owned(self, skill_id: uuid.UUID, file_id: uuid.UUID) -> SkillFile:
        """Fetch a file and prove it belongs to the skill the caller proved they own.

        The `skill_id` comparison is the whole point: without it, a caller who owns skill
        A could name A in the path, quote B's `file_id`, and read or delete it — the
        SEC-H1 shape (§8 threat 3) one level down.
        """
        f = await self._files.get(file_id)
        if f is None or f.skill_id != skill_id:
            raise SkillFileNotFound(file_id)
        return f

    async def read_text(self, file: SkillFile) -> str:
        """The extracted text of a `reference` file (AC-18).

        Assets and scripts are excluded by the caller, not here: `read_skill` serves
        reference text, and an asset has no text by definition (R31.18).

        The extraction is threaded because it runs **inside a turn**. `MIME_TO_PARSER`
        dispatches to `parse_pdf`, which is CPU-bound and may shell out page by page, so a
        32 MiB PDF reference would block the turn worker's event loop — and with it every
        other agent that worker is serving. The blocking parse itself is the house pattern
        (RAG ingests on the request path); running it per tool call inside a turn is new,
        which is what makes the thread hop worth its cost here.
        """
        client = get_minio_client()
        data = await client.get_object(bucket=client.skill_bundles_bucket, key=file.minio_key)
        return await asyncio.to_thread(_extract_text, data, file.mime)

    async def read_bytes(self, file: SkillFile) -> bytes:
        """The file's bytes, unextracted — for staging a `script` into the sandbox.

        Separate from `read_text` rather than a flag on it, because the two answer
        different questions and the difference is R31.18's: `read_text` renders a
        `reference` into the model's context and must parse it, while a script is never
        decoded here at all — it is copied into a tar and interpreted, if ever, inside
        gVisor. Running a script through `_extract_text` would be a decode of something
        that is not text for the model to read, and would corrupt any script that is not
        valid UTF-8.

        No thread hop: this is a byte read, not a parse. Whether the caller may have these
        bytes is the caller's proof, exactly as for `read_text`.
        """
        client = get_minio_client()
        return await client.get_object(bucket=client.skill_bundles_bucket, key=file.minio_key)

    async def add(
        self,
        *,
        skill: Skill,
        path: str,
        data: bytes,
        mime: str,
        scan_enabled: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> SkillFile:
        """Create a file from uploaded bytes or UI-authored text (AC-16).

        `path` has already passed `skill_file_path_reason` at the API boundary.
        """
        await self._assert_path_free(skill.id, path)
        norm_mime = normalise_mime(mime, path)
        kind = kind_for_path(path)
        sha = file_sha256(data)
        key = skill_file_key(skill_id=skill.id, sha256=sha, path=path)

        client = get_minio_client()
        await client.put_object(
            bucket=client.skill_bundles_bucket, key=key, data=data, content_type=norm_mime
        )
        created = await self._files.create(
            skill_id=skill.id,
            path=path,
            kind=kind,
            mime=norm_mime,
            size_bytes=len(data),
            sha256=sha,
            minio_key=key,
            scan_status=_initial_scan_status(scan_enabled),
            extracted_chars=await _count_extracted_chars(data, norm_mime, kind),
        )
        await self._emit("skill.file_created", skill, created, actor_user_id, actor_ip, request_id)
        return created

    async def update_content(
        self,
        *,
        skill: Skill,
        file: SkillFile,
        data: bytes,
        scan_enabled: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> SkillFile:
        """Re-point a file at new bytes (AC-16/AC-17).

        An `asset` is refused: R31.18 defines it as opaque bytes, and the UI has no
        editor that could produce them. Re-uploading is the supported path, which is a
        delete plus an add — two audit events describing what actually happened, rather
        than one `updated` hiding a replaced binary.
        """
        if file.kind is SkillFileKind.ASSET:
            raise SkillFileNotEditable(file.path, kind=file.kind.value)

        sha = file_sha256(data)
        key = skill_file_key(skill_id=skill.id, sha256=sha, path=file.path)
        client = get_minio_client()
        await client.put_object(
            bucket=client.skill_bundles_bucket, key=key, data=data, content_type=file.mime
        )
        updated = await self._files.update_content(
            file.id,
            size_bytes=len(data),
            sha256=sha,
            minio_key=key,
            # New bytes are unscanned. Carrying `clean` forward would let an edit smuggle
            # content past the gate AC-34 exists to hold — the file the scanner cleared
            # is not the file now stored.
            scan_status=_initial_scan_status(scan_enabled),
            extracted_chars=await _count_extracted_chars(data, file.mime, file.kind),
        )
        if updated is None:  # lost a race with a concurrent delete
            raise SkillFileNotFound(file.id, path=file.path)

        await self._emit(
            "skill.file_updated",
            skill,
            updated,
            actor_user_id,
            actor_ip,
            request_id,
            extra={"sha256_before": file.sha256, "sha256_after": updated.sha256},
        )
        return updated

    async def delete(
        self,
        *,
        skill: Skill,
        file: SkillFile,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        removed = await self._files.delete(file.id)
        if not removed:
            raise SkillFileNotFound(file.id, path=file.path)
        # The object is deliberately left in MinIO. Two writers can share one key
        # (content-addressed by sha), so removing it here could pull the bytes out from
        # under a sibling row that legitimately points at the same content. Orphaned
        # objects are the `skill-bundles` bucket's own retention problem, and the bucket
        # has no TTL by design (§21.5) — see FU-29.
        await self._emit("skill.file_deleted", skill, file, actor_user_id, actor_ip, request_id)

    # -- internals -----------------------------------------------------------

    async def _assert_path_free(self, skill_id: uuid.UUID, path: str) -> None:
        """Reject an exact or case-insensitive collision (409), and enforce the count cap.

        The DB's `UNIQUE (skill_id, path)` catches the exact case and is the real
        backstop; this check exists for the *case-insensitive* one it cannot express, and
        it answers the exact case here too so both arrive as a 409 naming the conflict
        rather than one 409 and one IntegrityError 500.

        The count cap rides on the same query rather than paying for its own: this method
        already lists every path the skill owns, so `MAX_SKILL_FILES` is a `len()` on a
        list that had to be fetched anyway.
        """
        existing_paths = await self._files.list_paths_for_skill(skill_id)
        if len(existing_paths) >= MAX_SKILL_FILES:
            raise SkillFileLimitExceeded(limit=MAX_SKILL_FILES)
        key = path_collision_key(path)
        for existing in existing_paths:
            if path_collision_key(existing) == key:
                raise SkillFilePathTaken(path, collides_with=existing)

    async def _emit(
        self,
        action: str,
        skill: Skill,
        file: SkillFile,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {
            "scope": skill.scope.value,
            "skill_name": skill.name,
            "path": file.path,
            "kind": file.kind.value,
            "sha256": file.sha256,
        }
        if extra:
            metadata.update(extra)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                # The *skill* is the resource: a file has no independent lifecycle, and
                # keying on it would scatter one skill's trail across ids that vanish on
                # delete. R31.25 lists file events beside the skill's own for this reason.
                resource_type="skill",
                resource_id=skill.id,
                metadata=metadata,
                request_id=request_id,
            ),
        )


async def enqueue_skill_scan(*, file_id: uuid.UUID, sha256: str) -> None:
    """Queue `skill_scan_file`. Swallows Redis failures, and that has a cost here.

    The RAG sibling swallows because an unscanned RAG document is still retrievable — the
    scan is advisory there. Under AC-34 it is not: a file stuck `pending` makes its skill
    **unreadable**, so a lost enqueue degrades availability rather than safety. That is
    the correct direction to fail, and it is still a silent failure, so it is logged at
    warning with the consequence spelled out. The user-visible recovery is to delete and
    re-add the file, which frees the path and enqueues afresh; FU-30 tracks a re-scan
    endpoint so an operator need not.

    The job id carries the sha, so editing a file to new bytes enqueues a fresh scan
    rather than being deduped onto the retained result of the bytes it replaced — the
    trap F-23 records on the RAG path, where the id carries the ingest attempt for the
    same reason.
    """
    try:
        from shared_kernel.queue import enqueue

        await enqueue("skill_scan_file", file_id=str(file_id), _job_id=f"skill-scan:{file_id}:{sha256}")
    except Exception:
        _log.warning(
            "scan enqueue failed for skill file %s; it stays unscanned and its skill "
            "stays unreadable until the file is re-added (AC-34)",
            file_id,
            exc_info=True,
        )


def _extract_text(data: bytes, mime: str) -> str:
    """Text of a reference file, via the shared extraction table.

    `parsers` is a dispatch table rather than a facade, so the lookup is explicit. An
    unsupported mime is not an error here: `read_skill` would rather serve a plain-text
    decode than refuse a file the author uploaded and referenced.
    """
    parser = MIME_TO_PARSER.get(mime)
    if parser is None:
        return data.decode("utf-8", errors="replace")
    try:
        return parser(data)
    except ParserError:
        return data.decode("utf-8", errors="replace")


def _extracted_chars(data: bytes, mime: str, kind: SkillFileKind) -> int:
    """How much text this file yields — 0 for anything not served as text.

    A count, not the text: `skill_files` stores no extracted text, so this is a display
    figure and a cheap "is there anything here" signal. The text itself is re-extracted
    on read from the bytes in MinIO, which keeps one copy of the truth.

    Pure and blocking. Call :func:`_count_extracted_chars` from async code.
    """
    if kind is not SkillFileKind.REFERENCE:
        return 0
    return len(_extract_text(data, mime))


async def _count_extracted_chars(data: bytes, mime: str, kind: SkillFileKind) -> int:
    """`_extracted_chars` off the event loop.

    The same reasoning as `read_text`, on the write path: this runs the identical
    `_extract_text`, and `MIME_TO_PARSER` dispatches to `parse_pdf`, which is CPU-bound
    and may shell out page by page. Threaded on read and not on write, a 32 MiB PDF upload
    would block the API worker's event loop — every other request on it — for the whole
    parse. A non-reference file short-circuits before the hop, so the ordinary script or
    asset upload pays nothing.
    """
    if kind is not SkillFileKind.REFERENCE:
        return 0
    return await asyncio.to_thread(_extracted_chars, data, mime, kind)


__all__ = [
    "MAX_SKILL_FILES",
    "MAX_SKILL_FILE_BYTES",
    "SkillFileService",
    "enqueue_skill_scan",
    "file_sha256",
    "kind_for_path",
]
