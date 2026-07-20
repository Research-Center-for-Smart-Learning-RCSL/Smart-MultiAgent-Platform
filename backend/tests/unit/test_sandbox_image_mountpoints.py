"""The sandbox images must pre-create every mount point they run as non-root.

Docker seeds a fresh named volume from the image's directory at the mount path,
ownership included. A path that does not exist in the image instead gets a
mountpoint created root-owned -- and these containers run as uid 10001, so the
kernel's own ``mkdir`` of ``inputs/``/``outputs`` then fails with EPERM.

This is not hypothetical. ``2026-07-19-session-dir-room-isolation`` added the
``/session`` mount without adding it here, which broke *every* ``code_exec``
call in *every* chatroom: the kernel could not create ``/session/outputs`` on a
volume it did not own, and ``kernel.py``'s mkdir is not suppressed. Staging does
not rescue it -- ``_tar_staged_inputs`` chowns ``inputs/`` but not the volume
root. See that dossier's D-10.

A container-level assertion lives in the sandbox-images CI tier
(``2026-07-17-sandbox-guest-container-tests`` AC-13); this test is the cheap
half that runs on every PR without a daemon.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DEPLOY = Path(__file__).resolve().parents[3] / "deploy" / "sandbox"

# Mirrors docker_runsc._SANDBOX_UID / _VOLUME_ROOT / _SESSION_ROOT. Duplicated
# rather than imported: the point is to catch the image and the host drifting
# apart, which a shared constant would hide.
_SANDBOX_UID = 10001

# Each image against the named volumes the host actually binds into it.
# mcp-runtime runs the `file` driver, which mkdirs under /workspace
# (driver.py:262) as the same uid, so it has the identical failure mode --
# it simply never had the defect, because its Dockerfile always created the path.
_IMAGE_MOUNTS = [
    ("code-exec", "/workspace"),
    ("code-exec", "/session"),
    ("mcp-runtime", "/workspace"),
]

_MISSING = sorted({img for img, _ in _IMAGE_MOUNTS if not (_DEPLOY / img / "Dockerfile").is_file()})
if _MISSING:
    # deploy/ is not mounted in the wiring tier's container (only backend/ is),
    # and pytest imports every module at collection time. Every Dockerfile these
    # tests read is checked, not just the first: guarding on one and reading two
    # turns a partial checkout into a FileNotFoundError instead of a clean skip.
    pytest.skip(
        f"sandbox Dockerfiles not present ({', '.join(_MISSING)}) -- deploy/ not mounted in this tier",
        allow_module_level=True,
    )


def _dockerfile_lines(image: str) -> list[str]:
    return (_DEPLOY / image / "Dockerfile").read_text(encoding="utf-8").splitlines()


def _logical_lines(image: str) -> list[tuple[int, str]]:
    """(index_of_first_physical_line, joined_text) with ``\\`` continuations folded."""
    out: list[tuple[int, str]] = []
    start, buf = None, ""
    for i, raw in enumerate(_dockerfile_lines(image)):
        if start is None:
            start = i
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        out.append((start, buf + stripped))
        start, buf = None, ""
    if start is not None:
        out.append((start, buf))
    return out


def _command_hits(image: str, command: str, operand: str) -> list[int]:
    """First physical line of each logical line where *command* takes *operand*.

    Scoped per ``&&`` segment, not per line. Matching the whole line would let a
    neighbouring command supply the operand: in
    ``mkdir -p /workspace/cache && chown 10001:10001 /workspace`` the chown
    contributes the ``/workspace`` token, so a line-wide check reports the mount
    point as created when it is not -- which is precisely the D-10 defect. Whole
    tokens rather than substrings, for the same reason.
    """
    hits = []
    for idx, text in _logical_lines(image):
        for segment in text.split("&&"):
            tokens = segment.split()
            if command in tokens and operand in tokens:
                hits.append(idx)
                break
    return hits


@pytest.mark.parametrize(("image", "mount"), _IMAGE_MOUNTS)
def test_sandbox_image_creates_each_mount_point(image: str, mount: str) -> None:
    """Every bind target exists in the image, so Docker seeds the volume from it."""
    assert _command_hits(image, "mkdir", mount), (
        f"{mount} is never created in {image}/Dockerfile. A fresh named volume "
        f"bound there will be root-owned and uid {_SANDBOX_UID} cannot write it."
    )


@pytest.mark.parametrize(("image", "mount"), _IMAGE_MOUNTS)
def test_sandbox_image_chowns_each_mount_point_to_the_sandbox_uid(image: str, mount: str) -> None:
    """Creating the directory is not enough -- it must belong to the sandbox uid."""
    owner_specs = {str(_SANDBOX_UID), f"{_SANDBOX_UID}:{_SANDBOX_UID}"}
    hits = [
        idx
        for idx, text in _logical_lines(image)
        for segment in text.split("&&")
        if {"chown", mount} <= set(segment.split()) and owner_specs & set(segment.split())
    ]
    assert hits, (
        f"{mount} is not chowned to {_SANDBOX_UID} in {image}/Dockerfile. Docker "
        f"copies the image directory's ownership into a fresh volume, so a "
        f"root-owned {mount} yields EPERM on the first mkdir by the sandbox user."
    )


@pytest.mark.parametrize(("image", "mount"), _IMAGE_MOUNTS)
def test_the_chown_precedes_the_user_directive(image: str, mount: str) -> None:
    """A chown after the privilege drop cannot run -- the build step is no longer root."""
    user_idxs = [i for i, ln in enumerate(_dockerfile_lines(image)) if re.match(r"^\s*USER\s+", ln)]

    # Anchoring on the first USER is only sound while there is exactly one. A
    # second (a multi-stage build, or a `USER root` to do privileged work) makes
    # "before the USER" ambiguous, and picking either end silently could pass a
    # chown that really does run unprivileged. Fail loudly and make the author
    # decide instead.
    assert len(user_idxs) == 1, (
        f"{image}/Dockerfile has {len(user_idxs)} USER directives (lines "
        f"{[i + 1 for i in user_idxs]}); this test assumes one privilege drop. "
        "Teach it which USER bounds the privileged section before adding another."
    )

    chown_idxs = _command_hits(image, "chown", mount)
    assert chown_idxs, f"no chown line for {mount} in {image}/Dockerfile"
    assert max(chown_idxs) < user_idxs[0], (
        f"a chown of {mount} appears after USER on line {user_idxs[0] + 1} of "
        f"{image}/Dockerfile; it would run unprivileged and silently fail to take effect"
    )
