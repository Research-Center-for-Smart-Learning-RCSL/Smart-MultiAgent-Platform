"""Pure function that builds a natural-language digest of canvas objects.

The digest is what the CanvasContextProvider injects into the agent's system
prompt.  Capped at _MAX_DIGEST_CHARS to prevent context stuffing ([R13.47]).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from contexts.canvas.domain.models import CanvasObject, CanvasObjectKind

_MAX_DIGEST_CHARS = 2000

_KIND_LABELS: dict[CanvasObjectKind, str] = {
    CanvasObjectKind.NOTE: "sticky note",
    CanvasObjectKind.TEXT: "text block",
    CanvasObjectKind.IMAGE: "image",
    CanvasObjectKind.SHAPE: "shape",
    CanvasObjectKind.DRAWING: "freeform drawing",
    CanvasObjectKind.CONNECTOR: "connector",
}


def _pluralise(label: str, count: int) -> str:
    if count == 1:
        return f"1 {label}"
    return f"{count} {label}s"


def _escape_markdown(text: str) -> str:
    """Escape markdown-special characters to prevent prompt injection."""
    out = text.replace("\\", "\\\\")
    for ch in ("*", "_", "`", "[", "]", "(", ")", "#", ">", "|", "~"):
        out = out.replace(ch, f"\\{ch}")
    return " ".join(out.split())


def build_canvas_digest(
    objects: Sequence[CanvasObject],
    *,
    resolve_label: dict[str, str] | None = None,
) -> str | None:
    """Return a natural-language summary of the canvas objects, or None if empty."""
    if not objects:
        return None

    counts = Counter(obj.kind for obj in objects)
    summary_parts = [_pluralise(_KIND_LABELS[kind], count) for kind, count in counts.items() if count > 0]
    lines: list[str] = [f"The canvas contains {', '.join(summary_parts)}."]

    budget = _MAX_DIGEST_CHARS - len(lines[0]) - 1
    for obj in objects:
        if budget <= 0:
            lines.append("(truncated)")
            break

        label = _KIND_LABELS[obj.kind].capitalize()
        if obj.kind == CanvasObjectKind.IMAGE:
            filename = obj.minio_path.rsplit("/", 1)[-1] if obj.minio_path else "image"
            line = f"- {label}: {_escape_markdown(filename)}"
        elif obj.content:
            preview = _escape_markdown(obj.content[:200])
            line = f'- {label}: "{preview}"'
        else:
            line = f"- {label}"

        budget -= len(line) + 1
        lines.append(line)

    result = "\n".join(lines)
    return result[:_MAX_DIGEST_CHARS] if len(result) > _MAX_DIGEST_CHARS else result


__all__ = ["build_canvas_digest"]
