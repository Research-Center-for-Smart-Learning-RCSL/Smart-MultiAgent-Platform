"""The sandbox mount roots are hardcoded in four places and must agree.

``docker_runsc.py`` says so outright next to ``_VOLUME_ROOT``: the value "must
equal ``file_tool._ROOT`` or a staged path resolves to nothing; nothing enforces
that agreement". This is that enforcement.

Two independent pairs, each with a silent failure mode:

* ``/workspace`` -- the host mounts the agent volume there, the API-side guard
  resolves tool arguments against it, and the in-image driver resolves them
  again on the other side of the container boundary. A disagreement does not
  raise: the host stages a file where the driver does not look, so the agent
  sees an empty workspace and a ``file`` read that "succeeds" with nothing.
* ``/session`` -- the host binds the per-room volume there and the kernel reads
  its default from the same literal. A disagreement leaves the kernel writing
  to the container layer, where artifacts are collected normally and then
  discarded with the container.

The constants are read rather than imported into one shared name on purpose:
a shared constant would make the four sites agree by construction and hide the
drift this test exists to catch. That is the same reasoning
``test_sandbox_image_mountpoints.py`` records for duplicating ``_SANDBOX_UID``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from contexts.agents.application.tools import file_tool
from contexts.agents.infrastructure.sandbox import docker_runsc

_DEPLOY = Path(__file__).resolve().parents[3] / "deploy" / "sandbox"
_GUEST_FILES = {
    "protocol": _DEPLOY / "driver" / "protocol.py",
    "kernel": _DEPLOY / "code-exec" / "kernel" / "kernel.py",
}

_MISSING = sorted(name for name, path in _GUEST_FILES.items() if not path.is_file())
if _MISSING:
    # deploy/ is not mounted in the wiring tier's container (only backend/ is).
    # Both files are checked, not just the first, so a partial checkout yields a
    # clean skip rather than a FileNotFoundError at collection time.
    pytest.skip(
        f"guest sources not present ({', '.join(_MISSING)}) -- deploy/ not mounted in this tier",
        allow_module_level=True,
    )


def _load_by_path(name: str) -> Any:
    """Import a guest-side module from deploy/ , which is not on sys.path.

    Registered in ``sys.modules`` before execution: ``protocol.py`` declares a
    ``slots=True`` dataclass, and that decorator rebuilds the class and looks its
    own module up by name, which fails on an unregistered module.
    """
    mod_name = f"_guest_{name}"
    spec = importlib.util.spec_from_file_location(mod_name, _GUEST_FILES[name])
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return module


def test_the_workspace_root_agrees_across_host_api_and_guest() -> None:
    """Host mount point, API-side guard, and in-image guard name the same path."""
    guest_root = _load_by_path("protocol").WORKSPACE_ROOT

    assert docker_runsc._VOLUME_ROOT == file_tool._ROOT, (
        "the host mounts the agent volume at _VOLUME_ROOT while the file tool resolves "
        "arguments against _ROOT; if they differ, staged files land where neither the "
        "driver nor the agent will look and every read succeeds empty"
    )
    assert guest_root == docker_runsc._VOLUME_ROOT, (
        "deploy/sandbox/driver/protocol.py resolves paths against WORKSPACE_ROOT inside "
        "the image, on the far side of the container boundary from the host's mount"
    )


def test_the_session_root_agrees_between_the_host_and_the_kernel() -> None:
    """The kernel's default session dir is the path the host actually binds."""
    # as_posix(), not str(): _SESSION_DIR is a Path, and str() renders it with
    # backslashes when the suite runs on Windows while the value is a container path.
    kernel_default = _load_by_path("kernel")._SESSION_DIR.as_posix()

    assert kernel_default == docker_runsc._SESSION_ROOT, (
        "the host binds the per-room volume at _SESSION_ROOT and passes no override, so "
        "the kernel's default is the only thing selecting it; a mismatch leaves the "
        "kernel writing artifacts to the container layer, which is discarded on exit"
    )


def test_the_session_root_is_not_nested_under_the_workspace_root() -> None:
    """[R12.03b]: the room boundary is the separation of the two mounts.

    Nesting would put per-room state inside the agent-wide volume, which is the
    exact arrangement 2026-07-19-session-dir-room-isolation removed.
    """
    session = docker_runsc._SESSION_ROOT
    volume = docker_runsc._VOLUME_ROOT

    assert session != volume
    assert not session.startswith(volume.rstrip("/") + "/"), (
        f"{session} sits under {volume}; the per-room mount would then be part of the "
        "agent-wide volume and the room boundary would depend on a path check inside a "
        "container running arbitrary code"
    )
