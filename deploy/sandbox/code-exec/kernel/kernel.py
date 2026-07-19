"""In-container persistent Python kernel for the ``code_exec`` built-in.

Runs as the container's main process under gVisor. Holds ONE long-lived module
namespace (:data:`_NS`) so variables and loaded dataframes persist across calls
within a single chat session -- this is what turns ``code_exec`` from a stateless
``python -c`` into a Code-Interpreter-style kernel.

The host drives each call by ``docker exec``-ing :mod:`client`, which relays a
length-framed JSON request over an ``AF_UNIX`` socket. The socket lives on the
writable ``/tmp`` tmpfs because the container root FS is read-only. There is no
network (the container runs ``network_mode=none``); matplotlib is forced to the
headless ``Agg`` backend and open figures are captured as PNG artifacts after
each call.

The session directory is env-overridable (``SMAP_KERNEL_SESSION``) so the pure
``_run`` helper is unit-testable off a real container. It is a mount of its own,
not a path under the agent volume: this module executes arbitrary code, so a room
boundary expressed as a subdirectory of a shared mount could not hold ([R12.03b]).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import mimetypes
import os
import pathlib
import signal
import socket
import struct
import sys
import traceback
import uuid
from typing import Any

_SOCK = os.environ.get("SMAP_KERNEL_SOCK", "/tmp/smap-kernel.sock")  # noqa: S108 — in-container tmpfs
# The session dir is its own mount, not a subdirectory of the agent volume: room
# isolation is established by what the container mounts, since this kernel runs
# arbitrary code and cannot be confined by a path check ([R12.03b]).
_SESSION_DIR = pathlib.Path(os.environ.get("SMAP_KERNEL_SESSION", "/session"))
_OUTPUTS = _SESSION_DIR / "outputs"
_INPUTS = _SESSION_DIR / "inputs"

# Bumped whenever the host/kernel contract changes shape. The host refuses a
# kernel whose stamp it does not know, because both directions of a mismatched
# deploy are otherwise silent: a host writing inputs where this kernel does not
# look, or this kernel looking at a mount the host did not bind.
PROTOCOL_VERSION = 2

# Inline small artifacts as base64 in the reply, which is what keeps the reply
# bounded: this container has 512 MB, and an inlined file is resident as raw
# bytes, as base64, in the serialised JSON and again in the host's buffers.
# Above the cap the descriptor carries `rel_path` and `size_bytes` with no
# payload, and the HOST fetches the file over the Docker API
# (`docker_runsc.fetch_kernel_artifact`). Until 2026-07-19 that fetch did not
# exist and this comment claimed it did, so every artifact over the cap was
# destroyed without a trace.
_ARTIFACT_B64_CAP = 8 * 1024 * 1024

# The one persistent namespace. Survives across calls for the kernel's lifetime.
_NS: dict[str, Any] = {"__name__": "__main__"}


class _Timeout(Exception):
    pass


def _on_alarm(_signum: int, _frame: Any) -> None:
    raise _Timeout()


def _guess_mime(path: pathlib.Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def _snapshot() -> dict[str, float]:
    if not _OUTPUTS.exists():
        return {}
    return {p.name: p.stat().st_mtime for p in _OUTPUTS.iterdir() if p.is_file()}


def _collect_artifacts(before: dict[str, float]) -> list[dict[str, Any]]:
    """Descriptors for files created or modified in the outputs dir this call."""
    arts: list[dict[str, Any]] = []
    if not _OUTPUTS.exists():
        return arts
    for path in sorted(_OUTPUTS.iterdir()):
        if not path.is_file():
            continue
        mtime = path.stat().st_mtime
        if before.get(path.name) == mtime:
            continue  # untouched by this call
        size = path.stat().st_size
        b64: str | None = None
        if size <= _ARTIFACT_B64_CAP:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        arts.append(
            {
                "filename": path.name,
                "mime": _guess_mime(path),
                "size_bytes": size,
                "rel_path": str(path),
                "b64": b64,
            }
        )
    return arts


def _capture_figures() -> None:
    """Save any open matplotlib figures to the outputs dir, then close them."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    try:
        for num in plt.get_fignums():
            fig = plt.figure(num)
            fig.savefig(_OUTPUTS / f"figure-{num}-{uuid.uuid4().hex}.png", bbox_inches="tight")
        plt.close("all")
    except Exception:
        pass


def _run(code: str, stdin: str, timeout_s: float) -> dict[str, Any]:
    """Execute *code* in the persistent namespace; capture output + artifacts."""
    _OUTPUTS.mkdir(parents=True, exist_ok=True)
    _INPUTS.mkdir(parents=True, exist_ok=True)
    # Run from the session dir so the relative paths the agent is told about
    # (``inputs/<file>`` for staged uploads, ``outputs/<file>`` for results)
    # resolve here rather than at the volume root.
    with contextlib.suppress(Exception):
        os.chdir(_SESSION_DIR)
    before = _snapshot()
    out, err = io.StringIO(), io.StringIO()
    real_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    ok = True
    has_alarm = hasattr(signal, "SIGALRM")
    if has_alarm:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, max(1.0, float(timeout_s)))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                exec(compile(code, "<code_exec>", "exec"), _NS)  # noqa: S102 — gVisor-isolated
            except _Timeout:
                ok = False
                err.write(f"\n[timeout after {timeout_s:.0f}s]")
            except SystemExit:
                pass
            except BaseException:  # noqa: BLE001 — surface any user error as stderr
                ok = False
                traceback.print_exc(file=err)
    finally:
        if has_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
        sys.stdin = real_stdin
    _capture_figures()
    return {
        "protocol": PROTOCOL_VERSION,
        "ok": ok,
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
        "artifacts": _collect_artifacts(before),
    }


def _recv_n(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv_framed(conn: socket.socket) -> bytes | None:
    header = _recv_n(conn, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    return _recv_n(conn, length)


def _send_framed(conn: socket.socket, data: bytes) -> None:
    conn.sendall(struct.pack(">I", len(data)) + data)


def main() -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(_SOCK)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(_SOCK)
    server.listen(8)
    while True:
        conn, _ = server.accept()
        try:
            raw = _recv_framed(conn)
            if raw is None:
                continue
            req = json.loads(raw)
            reply = _run(
                str(req.get("code", "")),
                str(req.get("stdin", "")),
                float(req.get("timeout_s", 30.0)),
            )
            _send_framed(conn, json.dumps(reply).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — never let one bad request kill the kernel
            with contextlib.suppress(Exception):
                _send_framed(
                    conn,
                    json.dumps(
                        {
                            "ok": False,
                            "stdout": "",
                            "stderr": f"kernel error: {exc}",
                            "artifacts": [],
                        }
                    ).encode("utf-8"),
                )
        finally:
            with contextlib.suppress(Exception):
                conn.close()


if __name__ == "__main__":
    main()
