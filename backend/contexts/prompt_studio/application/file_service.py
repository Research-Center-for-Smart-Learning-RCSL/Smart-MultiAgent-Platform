"""Reference-file application service (§29 / R29.06, AC-7).

Upload validates format + size, virus-scans (fail-closed when a configured
scanner cannot verify the file), extracts text synchronously, enforces the
per-config extracted-text budget, and stores the bytes in MinIO plus the row
(with its extracted text inlined) in Postgres. Extraction is done in a thread
so the parse never blocks the event loop.

D-1: extraction runs synchronously in the request rather than the worker (§8),
because AC-7 requires a synchronous RFC-7807 rejection when the 200 KB
extracted-text budget is exceeded — impossible to enforce asynchronously.
Reference files are bounded to 5 MB and the parsers are pure, so the bounded
parse in a threadpool does not block the loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.prompt_studio.domain.errors import (
    ExtractedTextBudgetExceeded,
    FileFormatUnsupported,
    FileInfected,
    FileTooLarge,
    ReferenceFileExtractionFailed,
    ReferenceFileNotFound,
)
from contexts.prompt_studio.domain.models import (
    ALLOWED_FILE_EXTENSIONS,
    EXTRACTED_TEXT_BUDGET,
    FILE_MAX_BYTES,
    AssistantFile,
    ScanStatus,
)
from contexts.prompt_studio.infrastructure.repositories import AssistantConfigRepository
from shared_kernel import audit
from shared_kernel.scanning import ScanError, get_scanner
from shared_kernel.storage.minio_client import MinioClient, prompt_assistant_file_key
from shared_kernel.storage.sanitize import safe_input_name
from shared_kernel.text_extraction import (
    ParserError,
    parse_docx,
    parse_markdown,
    parse_pdf,
    parse_plaintext,
)

_PARSER_BY_EXT: dict[str, Callable[[bytes], str]] = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "md": parse_markdown,
    "txt": parse_plaintext,
}


def _extension(filename: str) -> str:
    _, _, ext = filename.rpartition(".")
    return ext.lower().strip()


class FileService:
    def __init__(self, db: AsyncSession, storage: MinioClient) -> None:
        self._db = db
        self._storage = storage
        self._configs = AssistantConfigRepository(db)

    async def list_files(self, config_id: uuid.UUID) -> list[AssistantFile]:
        return await self._configs.list_files(config_id)

    async def upload_reference_file(
        self,
        *,
        config_id: uuid.UUID,
        filename: str,
        data: bytes,
        mime: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> AssistantFile:
        ext = _extension(filename)
        if ext not in ALLOWED_FILE_EXTENSIONS:
            raise FileFormatUnsupported(filename)
        if len(data) > FILE_MAX_BYTES:
            raise FileTooLarge(f"{len(data)} > {FILE_MAX_BYTES}")

        await self._scan_or_reject(
            data, actor_user_id=actor_user_id, actor_ip=actor_ip, request_id=request_id
        )

        text = await self._extract(ext, data)
        extracted_chars = len(text)
        existing_total = await self._configs.sum_extracted_chars(config_id, only_clean=True)
        if existing_total + extracted_chars > EXTRACTED_TEXT_BUDGET:
            raise ExtractedTextBudgetExceeded(f"{existing_total + extracted_chars} > {EXTRACTED_TEXT_BUDGET}")

        safe_name = safe_input_name(filename)
        sha = hashlib.sha256(data).hexdigest()
        key = prompt_assistant_file_key(config_id=config_id, sha256=sha, filename=safe_name)
        await self._storage.put_object(
            bucket=self._storage.prompt_assistant_files_bucket,
            key=key,
            data=data,
            content_type=mime,
        )
        file = await self._configs.add_file(
            config_id=config_id,
            values={
                "filename": safe_name,
                "size_bytes": len(data),
                "sha256": sha,
                "mime": mime,
                "minio_key": key,
                "scan_status": ScanStatus.CLEAN.value,
                "extracted_chars": extracted_chars,
                "extracted_text": text,
            },
        )
        await self._emit(
            "prompt_studio.file_uploaded", config_id, file.id, actor_user_id, actor_ip, request_id
        )
        return file

    async def remove_reference_file(
        self,
        *,
        config_id: uuid.UUID,
        file_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        file = await self._configs.get_file(config_id, file_id)
        if file is None:
            raise ReferenceFileNotFound(str(file_id))
        await self._configs.delete_file(config_id, file_id)
        await self._storage.remove(bucket=self._storage.prompt_assistant_files_bucket, key=file.minio_key)
        await self._emit(
            "prompt_studio.file_removed", config_id, file_id, actor_user_id, actor_ip, request_id
        )

    async def _scan_or_reject(
        self,
        data: bytes,
        *,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> None:
        scanner = get_scanner()
        if scanner is None:
            # R22.15.07 explicitly permits running without a configured scanner.
            return
        try:
            result = await scanner.scan(data)
        except ScanError as exc:
            # Configured but unverifiable — fail closed for attacker-influenceable
            # reference material rather than admitting an unscanned file.
            raise FileInfected("scan_unavailable") from exc
        if not result.clean:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="prompt_studio.file_quarantined",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="prompt_assistant_file",
                    metadata={"threat": result.threat_name or "unknown"},
                    request_id=request_id,
                ),
            )
            raise FileInfected(result.threat_name or "infected")

    @staticmethod
    async def _extract(ext: str, data: bytes) -> str:
        parser = _PARSER_BY_EXT[ext]
        try:
            return await asyncio.to_thread(parser, data)
        except ParserError as exc:
            raise ReferenceFileExtractionFailed(str(exc)) from exc

    async def _emit(
        self,
        action: str,
        config_id: uuid.UUID,
        file_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> None:
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="prompt_assistant_file",
                resource_id=file_id,
                metadata={"config_id": str(config_id)},
                request_id=request_id,
            ),
        )


__all__ = ["FileService"]
