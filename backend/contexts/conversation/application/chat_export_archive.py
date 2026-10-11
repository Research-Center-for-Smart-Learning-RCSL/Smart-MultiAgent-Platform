"""Packaging a chat export: attachment files, the transcript, and the ZIP.

The read phase (`ChatExportService`) writes each message as one JSON line to
a work directory and lists the attachments it saw. Everything here runs after
the database session is released: attachments are fetched into the archive
first, so the transcript can then say exactly which ones made it.

Design and the decisions behind it:
docs/tasks/2026-10-11-chat-export-archive-content/spec.md (Q-1..Q-4, §7.2, §7.7).
"""

from __future__ import annotations

import html as _html
import json
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from contexts.conversation.domain.models import AttachmentStatus, ScanStatus
from shared_kernel.auth.clients import as_utc, now
from shared_kernel.storage.sanitize import safe_input_name

OmittedReason = Literal["expired", "quarantined", "missing", "size_budget"]

SCHEMA_VERSION: Final = 2
MESSAGES_FILE: Final = "messages.jsonl"
README_NAME: Final = "README.txt"

_REASON_TEXT: Final[dict[str, str]] = {
    "expired": "expired",
    "quarantined": "quarantined by the malware scan",
    "missing": "no longer in storage",
    "size_budget": "over the archive's attachment size limit",
}

# Already compressed: deflating them again costs CPU for nothing.
_STORED_TYPES: Final = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
        "application/zip",
        "application/gzip",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
    }
)


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """An attachment of an exported message, as the read phase saw it."""

    id: str
    message_id: str
    filename: str
    mime: str
    size_bytes: int
    status: str
    scan_status: str
    minio_path: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Outcome:
    archive_path: str | None = None
    omitted_reason: OmittedReason | None = None


@dataclass(slots=True)
class Packing:
    """Which attachments went into the archive, and why the rest did not."""

    outcomes: dict[str, Outcome] = field(default_factory=dict)

    @property
    def archived(self) -> int:
        return sum(1 for o in self.outcomes.values() if o.archive_path)

    @property
    def omitted(self) -> int:
        return sum(1 for o in self.outcomes.values() if o.omitted_reason)


def status_reason(ref: AttachmentRef) -> OmittedReason | None:
    """Why an attachment is excluded before its object is even looked at.

    A quarantined object is a scan verdict, so it is never read. Expiry is
    checked against the horizon as well as the status, because the bucket
    lifecycle and the nightly sweep run on independent clocks (R13.11a).
    """
    if ref.status == AttachmentStatus.QUARANTINED.value or ref.scan_status == ScanStatus.QUARANTINED.value:
        return "quarantined"
    if ref.status == AttachmentStatus.EXPIRED.value:
        return "expired"
    if ref.expires_at is not None and as_utc(ref.expires_at) <= now():
        return "expired"
    return None


class EntryNames:
    """Archive entry names: ``attachments/<message-id>/<flat safe name>``.

    The uploaded filename is attacker-controlled. Only its sanitised basename
    is used, under a directory named by the message UUID, so no entry can
    carry a separator, a ``..`` segment or an absolute path (zip-slip).
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def allocate(self, message_id: str, filename: str) -> str:
        base = safe_input_name(filename)
        stem, dot, ext = base.rpartition(".")
        if not stem:
            stem, dot, ext = base, "", ""
        candidate, n = f"attachments/{message_id}/{base}", 1
        while candidate in self._taken:
            n += 1
            candidate = f"attachments/{message_id}/{stem} ({n}){dot}{ext}"
        self._taken.add(candidate)
        return candidate


def compression_for(mime: str) -> int:
    base = mime.split(";", 1)[0].strip().lower()
    if base in _STORED_TYPES or base.startswith(("video/", "audio/")):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


# ---- the transcript ----------------------------------------------------------


def iter_messages(work: Path) -> Iterator[dict[str, Any]]:
    with (work / MESSAGES_FILE).open(encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


def _attachment_entries(message: dict[str, Any], packing: Packing | None) -> list[dict[str, Any]]:
    """The manifest's view of a message's attachments; never the storage path."""
    out: list[dict[str, Any]] = []
    for a in message.get("attachments") or []:
        entry = {k: a[k] for k in ("id", "filename", "mime", "size_bytes", "status")}
        outcome = packing.outcomes.get(a["id"]) if packing else None
        entry["archived"] = bool(outcome and outcome.archive_path)
        if outcome and outcome.archive_path:
            entry["archive_path"] = outcome.archive_path
        elif outcome and outcome.omitted_reason:
            entry["omitted_reason"] = outcome.omitted_reason
        out.append(entry)
    return out


def _sender_label(message: dict[str, Any]) -> str:
    sender_id = message.get("sender_id")
    if message.get("sender_name"):
        label = str(message["sender_name"])
    else:
        label = str(message.get("sender_type") or "")
        if sender_id:
            label += f" ({str(sender_id)[:8]})"
    if message.get("created_at"):
        label += f" — {message['created_at']}"
    if message.get("edited_at"):
        label += " (edited)"
    return label


def write_json(target: Path, work: Path, header: dict[str, Any], packing: Packing | None) -> int:
    """Stream the manifest: the header, then one message object at a time."""
    count = 0
    with target.open("w", encoding="utf-8") as out:
        head = json.dumps({"schema_version": SCHEMA_VERSION, **header}, ensure_ascii=False, default=str)
        out.write(head[:-1] + ',"messages":[')
        for m in iter_messages(work):
            if count:
                out.write(",")
            m["attachments"] = _attachment_entries(m, packing)
            out.write(json.dumps(m, ensure_ascii=False, separators=(",", ":"), default=str))
            count += 1
        out.write("]}")
    return count


def _markdown_attachments(message: dict[str, Any], packing: Packing | None) -> list[str]:
    entries = _attachment_entries(message, packing)
    if not entries:
        return []
    if packing is None:
        return ["", f"_Attachments: {', '.join(a['filename'] for a in entries)}_"]
    lines = ["", "_Attachments:_", ""]
    for a in entries:
        if a.get("archive_path"):
            lines.append(f"- {a['filename']} ({a['archive_path']})")
        else:
            lines.append(f"- _{a['filename']}: not included ({_REASON_TEXT[a['omitted_reason']]})_")
    return lines


def write_markdown(target: Path, work: Path, header: dict[str, Any], packing: Packing | None) -> int:
    count = 0
    room = header.get("chatroom", {})
    with target.open("w", encoding="utf-8") as out:
        out.write(f"# {room.get('name') or room.get('id') or ''}\n\n")
        if header.get("exported_at"):
            out.write(f"_Exported {header['exported_at']}_\n\n")
        for m in iter_messages(work):
            lines = [f"## {_sender_label(m)}", "", m.get("content_md") or ""]
            lines += _markdown_attachments(m, packing)
            lines += ["", "---", ""]
            out.write("\n".join(lines) + "\n")
            count += 1
    return count


_PDF_STYLE: Final = (
    "body{font-family:sans-serif;font-size:12px;color:#1f2328;}"
    "h1{font-size:20px;}"
    ".meta{color:#6b7280;font-size:11px;}"
    ".msg{margin:12px 0;padding:8px 12px;border:1px solid #e5e7eb;border-radius:6px;}"
    ".msg--agent{border-left:3px solid #2563eb;background:#f8fafc;}"
    ".msg--system{border:none;color:#6b7280;font-style:italic;text-align:center;}"
    ".msg-head{font-weight:600;font-size:11px;margin-bottom:4px;}"
    "pre{background:#f8fafc;padding:8px;border-radius:4px;}"
    "table{border-collapse:collapse;}td,th{border:1px solid #e5e7eb;padding:4px;}"
)


def pdf_html(work: Path, header: dict[str, Any], packing: Packing | None) -> str:
    """The whole document; PDF stays capped because WeasyPrint lays it out in memory."""
    room = header.get("chatroom", {})
    title = _html.escape(str(room.get("name") or room.get("id") or ""))
    parts = [f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_PDF_STYLE}</style></head>']
    parts.append(f"<body><h1>{title}</h1>")
    if header.get("exported_at"):
        parts.append(f'<p class="meta">Exported {_html.escape(str(header["exported_at"]))}</p>')
    for m in iter_messages(work):
        sender_type = _html.escape(str(m.get("sender_type") or ""))
        # content_html is already sanitised (render_safe_html / bleach), so
        # embedding it carries the same XSS contract as the chat UI.
        parts.append(f'<div class="msg msg--{sender_type}">')
        parts.append(f'<div class="msg-head">{_html.escape(_sender_label(m))}</div>')
        parts.append(f'<div class="msg-body">{m.get("content_html") or ""}</div>')
        entries = _attachment_entries(m, packing)
        if entries:
            notes = [
                a["filename"]
                if packing is None
                else (
                    f"{a['filename']} ({a['archive_path']})"
                    if a.get("archive_path")
                    else f"{a['filename']} (not included: {_REASON_TEXT[a['omitted_reason']]})"
                )
                for a in entries
            ]
            parts.append(f'<div class="meta">Attachments: {_html.escape(", ".join(notes))}</div>')
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


def write_transcript(
    export_format: str,
    target: Path,
    work: Path,
    header: dict[str, Any],
    packing: Packing | None,
    html_to_pdf: Callable[[str], bytes],
) -> None:
    if export_format == "json":
        write_json(target, work, header, packing)
    elif export_format == "pdf":
        target.write_bytes(html_to_pdf(pdf_html(work, header, packing)))
    else:
        write_markdown(target, work, header, packing)


def readme(transcript_name: str, refs: list[AttachmentRef], packing: Packing) -> str:
    lines = [
        f"This archive holds the chat transcript ({transcript_name}) and, under attachments/,",
        "the files attached to its messages.",
        "",
    ]
    omitted = [(r, packing.outcomes[r.id]) for r in refs if packing.outcomes[r.id].omitted_reason]
    if not omitted:
        lines.append("Every attachment is included.")
    else:
        lines.append(f"Not included ({len(omitted)}):")
        for ref, outcome in omitted:
            reason = _REASON_TEXT[outcome.omitted_reason or "missing"]
            lines.append(f"- {safe_input_name(ref.filename)} (message {ref.message_id}): {reason}")
    return "\n".join(lines) + "\n"


__all__ = [
    "MESSAGES_FILE",
    "README_NAME",
    "SCHEMA_VERSION",
    "AttachmentRef",
    "EntryNames",
    "OmittedReason",
    "Outcome",
    "Packing",
    "compression_for",
    "readme",
    "status_reason",
    "write_transcript",
]
