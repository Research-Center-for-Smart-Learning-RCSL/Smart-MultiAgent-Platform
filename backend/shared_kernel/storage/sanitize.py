"""Filename and workspace-path sanitization for storage paths."""

from __future__ import annotations

import posixpath

# Kept in step with the `agent_workspace_files.path` column and the upload
# boundary that writes it.
MAX_WORKSPACE_PATH_LEN = 500


def safe_input_name(filename: str) -> str:
    """Reduce a user filename to a flat, traversal-safe basename."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(c for c in base if c.isprintable() and c not in '"\\:*?<>|').strip()
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:200]


def validate_workspace_relpath(raw: str) -> str:
    """Normalise a workspace-relative path; raise if it is not one.

    Preserves directory components, so it must *reject* rather than flatten:
    traversal, absolute paths, null bytes, over-length. It raises instead of
    substituting something acceptable — a caller that silently rewrote
    `../../etc/passwd` into a plausible path would be acting on a name the
    uploader never gave.

    This is the identity rule for `agent_workspace_files.path`: its output keys
    the row (`WorkspaceFileRepository.get_by_path`). Changing what it returns
    re-keys existing rows, so it is deliberately conservative.
    """
    if not isinstance(raw, str):
        raise ValueError("path must be a string")
    if "\x00" in raw:
        raise ValueError("null byte in path")
    normed = posixpath.normpath(raw.strip().replace("\\", "/").strip("/"))
    if normed in (".", "..") or normed.startswith("../") or "/../" in f"/{normed}/":
        raise ValueError("path must not contain '..' components")
    if len(normed) > MAX_WORKSPACE_PATH_LEN:
        raise ValueError(f"path too long (max {MAX_WORKSPACE_PATH_LEN} chars)")
    if not normed:
        raise ValueError("path must be non-empty")
    return normed


def safe_workspace_relpath(raw: str) -> str:
    """The staging rule: validate a stored path, then clean each component.

    Stricter than `validate_workspace_relpath` on one point — a leading `/` is
    rejected rather than stripped. The two are not doing the same job: the upload
    boundary normalises whatever a user typed, whereas staging is handed a path
    that has *already* been through that boundary, so a stored path is expected
    to be relative and an absolute one means an invariant broke upstream.
    Normalising it here would paper over that; a `..` would already have raised.

    Every component is then reduced to a safe name, since each becomes a tar
    member the guest has to hold.

    Lives here rather than beside either caller: the upload boundary
    (`agents.application.workspace_service`) and the sandbox staging helper
    (`agents.infrastructure.sandbox.docker_runsc`) need the same validation, and
    infrastructure may not import application.
    """
    if isinstance(raw, str) and raw.strip().replace("\\", "/").startswith("/"):
        raise ValueError("stored workspace path must be relative")
    normed = validate_workspace_relpath(raw)
    return "/".join(safe_input_name(p) for p in normed.split("/"))


__all__ = [
    "MAX_WORKSPACE_PATH_LEN",
    "safe_input_name",
    "safe_workspace_relpath",
    "validate_workspace_relpath",
]
