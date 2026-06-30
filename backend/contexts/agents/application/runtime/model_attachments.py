"""Turn a triggering message's attachments into provider-neutral content blocks.

The turn engine sends conversation history to the LLM as text. To let an agent
*see* an uploaded file (vision / PDF / text), the triggering user message's
attachments are converted here into neutral content blocks that each provider
adapter then renders in its own format (image / document / text):

    {"type": "text",     "text": str}
    {"type": "image",    "media_type": "image/png",       "data": "<base64>", "filename": str}
    {"type": "document", "media_type": "application/pdf",  "data": "<base64>", "filename": str}

Adapters that target a model which cannot view a given block fall back to the
filename note rather than sending an unsupported block (which would 4xx). This
module owns *selection* (which attachments, within what budget) and *encoding*;
per-model capability gating lives in each adapter, keyed on the resolved model.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence

# Anthropic's vision set is the narrowest of the three providers, so it bounds
# what we treat as an inline image; everything else degrades to a filename note.
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_TEXT_MIMES = frozenset({"application/json", "application/xml", "application/x-yaml", "application/yaml"})

# Budgets: keep well under the providers' per-request limits (Anthropic 32 MB /
# image source ~5 MB) and bound how much a single turn can balloon.
MAX_ATTACH_FILES = 6
MAX_ATTACH_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 50_000


def _norm_mime(mime: str | None) -> str:
    m = (mime or "").split(";", 1)[0].strip().lower()
    return "image/jpeg" if m == "image/jpg" else m


def classify(mime: str | None) -> str:
    """Coarse kind for routing: ``image`` | ``pdf`` | ``text`` | ``other``."""
    m = _norm_mime(mime)
    if m in _IMAGE_MIMES:
        return "image"
    if m == "application/pdf":
        return "pdf"
    if m.startswith("text/") or m in _TEXT_MIMES:
        return "text"
    return "other"


def _note(filename: str, mime: str, reason: str) -> dict[str, str]:
    return {"type": "text", "text": f"[Attached file {filename} ({_norm_mime(mime)}) — {reason}]"}


def build_blocks(items: Sequence[tuple[str, str, bytes | None]]) -> list[dict[str, str]]:
    """Build neutral content blocks from ``(filename, mime, data)`` triples.

    ``data`` is ``None`` when the bytes could not be read; that attachment still
    yields a note so the model knows a file was attached. Selection stops at the
    file-count / total-byte budgets; over-budget or oversized files become notes
    rather than being silently dropped (R: no silent truncation)."""
    blocks: list[dict[str, str]] = []
    total = 0
    inlined = 0
    for filename, mime, data in items:
        if inlined >= MAX_ATTACH_FILES:
            blocks.append(_note(filename, mime, "skipped (attachment count budget exceeded)"))
            continue
        if data is None:
            blocks.append(_note(filename, mime, "unavailable"))
            continue
        if len(data) > MAX_FILE_BYTES:
            blocks.append(_note(filename, mime, "too large to display inline"))
            continue
        if total + len(data) > MAX_ATTACH_TOTAL_BYTES:
            blocks.append(_note(filename, mime, "skipped (attachment size budget exceeded)"))
            continue
        kind = classify(mime)
        if kind == "image":
            blocks.append(
                {
                    "type": "image",
                    "media_type": _norm_mime(mime),
                    "data": base64.b64encode(data).decode("ascii"),
                    "filename": filename,
                }
            )
        elif kind == "pdf":
            blocks.append(
                {
                    "type": "document",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(data).decode("ascii"),
                    "filename": filename,
                }
            )
        elif kind == "text":
            text = data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
            blocks.append({"type": "text", "text": f"[Attached file: {filename}]\n{text}"})
        else:
            blocks.append(_note(filename, mime, "preview not supported"))
            continue
        total += len(data)
        inlined += 1
    return blocks


__all__ = [
    "MAX_ATTACH_FILES",
    "MAX_ATTACH_TOTAL_BYTES",
    "build_blocks",
    "classify",
]
