"""Filename and workspace-path sanitization for storage paths."""

from __future__ import annotations

import posixpath

# An application cap, not a column constraint: `agent_workspace_files.path` is
# unbounded `sa.Text`. Bounds the note the model reads and the tar member names.
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
    traversal, absolute paths, non-printable characters, over-length. It raises
    instead of substituting something acceptable — a caller that silently rewrote
    `../../etc/passwd` into a plausible path would be acting on a name the
    uploader never gave.

    **Idempotent, and it must stay that way.** Its output keys the row
    (`WorkspaceFileRepository.get_by_path`) and is later re-validated at sandbox
    staging, so a path it accepts must survive a second pass unchanged. It did not
    at first: stripping whitespace before slashes let `/ /x.csv` normalise to
    ` /x.csv`, which re-validates to `/x.csv` and is then rejected as absolute —
    one upload permanently breaking the agent's staging. Whitespace is therefore
    stripped per component, before any slash handling.
    """
    if not isinstance(raw, str):
        raise ValueError("path must be a string")
    # Covers NUL, newlines and zero-width formatting characters alike. Newlines
    # matter beyond tidiness: these paths are interpolated into the one-line note
    # the model reads, where a newline would let an upload forge a prompt section.
    if not raw.isprintable():
        raise ValueError("path contains non-printable characters")
    parts = [p.strip() for p in raw.replace("\\", "/").split("/")]
    normed = posixpath.normpath("/".join(parts).strip("/"))
    if normed in (".", "..") or normed.startswith("../") or "/../" in f"/{normed}/":
        raise ValueError("path must not contain '..' components")
    if not normed:
        raise ValueError("path must be non-empty")
    if len(normed) > MAX_WORKSPACE_PATH_LEN:
        raise ValueError(f"path too long (max {MAX_WORKSPACE_PATH_LEN} chars)")
    return normed


def safe_workspace_relpath(raw: str) -> str:
    """The staging rule: re-validate a *stored* workspace path.

    Deliberately returns the path unchanged rather than cleaning it. Staging is
    handed a path that already passed `validate_workspace_relpath` at upload and
    that the designer is shown in the UI, so any rewrite here would re-introduce
    the very defect this module's callers exist to fix — telling someone a path
    that is not where the file is. Cleaning each component through
    `safe_input_name` did exactly that: it strips leading dots, so the stored
    `.config/app.json` staged as `config/app.json` and the model was handed a path
    the designer could not recognise.

    Stricter on one point: a leading `/` is rejected rather than stripped. The two
    functions are not doing the same job — upload normalises whatever a user
    typed, staging checks an invariant on a path that has already been normalised,
    so an absolute path arriving here means something upstream broke and must not
    be papered over.

    Lives here rather than beside either caller: the upload boundary
    (`agents.application.workspace_service`) and the sandbox staging helper
    (`agents.infrastructure.sandbox.docker_runsc`) need the same validation, and
    infrastructure may not import application.
    """
    if isinstance(raw, str) and raw.strip().replace("\\", "/").startswith("/"):
        raise ValueError("stored workspace path must be relative")
    return validate_workspace_relpath(raw)


__all__ = [
    "MAX_WORKSPACE_PATH_LEN",
    "safe_input_name",
    "safe_workspace_relpath",
    "validate_workspace_relpath",
]
