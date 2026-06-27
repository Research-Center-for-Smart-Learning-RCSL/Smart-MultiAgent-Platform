"""Filename sanitization for storage paths."""

from __future__ import annotations


def safe_input_name(filename: str) -> str:
    """Reduce a user filename to a flat, traversal-safe basename."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(c for c in base if c.isprintable() and c not in '"\\:*?<>|').strip()
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:200]


__all__ = ["safe_input_name"]
