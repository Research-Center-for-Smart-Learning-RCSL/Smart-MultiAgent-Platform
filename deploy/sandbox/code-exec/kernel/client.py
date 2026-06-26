"""Ephemeral messenger between the host and the in-container persistent kernel.

The host ``docker exec``s this for every ``code_exec`` call. It reads the
request from env vars (set on the exec, never on the long-lived container so no
snippet leaks into the kernel's own environment), connects to the kernel's
``AF_UNIX`` socket, sends a length-framed JSON request, and prints the JSON
reply to stdout for the host to parse. The kernel process keeps the namespace;
this messenger is stateless and exits immediately.

Dependency-free and tiny on purpose -- it must import instantly under gVisor.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time

_SOCK = os.environ.get("SMAP_KERNEL_SOCK", "/tmp/smap-kernel.sock")  # noqa: S108 — in-container tmpfs


def _recv_n(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise SystemExit("kernel closed connection")
        buf += chunk
    return buf


def _connect(deadline: float) -> socket.socket:
    """Connect to the kernel socket, retrying until the kernel has bound it."""
    last: Exception | None = None
    while time.time() < deadline:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(_SOCK)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
            time.sleep(0.1)
    raise SystemExit(f"kernel socket unavailable: {last}")


def main() -> None:
    req = {
        "code": os.environ.get("SMAP_KERNEL_CODE", ""),
        "stdin": os.environ.get("SMAP_KERNEL_STDIN", ""),
        "timeout_s": float(os.environ.get("SMAP_KERNEL_TIMEOUT", "30")),
    }
    payload = json.dumps(req).encode("utf-8")
    conn = _connect(time.time() + 10.0)
    try:
        conn.sendall(struct.pack(">I", len(payload)) + payload)
        (length,) = struct.unpack(">I", _recv_n(conn, 4))
        sys.stdout.write(_recv_n(conn, length).decode("utf-8"))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
