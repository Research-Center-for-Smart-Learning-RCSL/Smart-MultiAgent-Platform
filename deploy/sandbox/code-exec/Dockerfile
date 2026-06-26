# smap/code-exec — the `code_exec` built-in tool runs here under gVisor (R12.05).
#
# Unlike mcp-runtime, the host runs THIS image with command=["python","-c",src]
# directly (see docker_runsc.run_code_exec) — no driver indirection — so the
# image must NOT override ENTRYPOINT. The driver is copied in only for the
# optional `exec` subcommand / parity; the default path is plain `python -c`.
#
# Egress: the sandbox network is gateway-less and the egress proxy is a custom
# HMAC forwarder (not a transparent HTTP_PROXY), so HTTP_PROXY is deliberately
# NOT set — arbitrary outbound from user code has no route, which is the safe
# default for code_exec. Allowlisted egress is exposed via MCP/web_search, not
# raw sockets from this image.
FROM python:3.14-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/.mpl

# Curated scientific set (R12.05). Pinned-major to keep the image reproducible;
# CI records the resolved digest. Kept lean — no compilers in the final layer.
RUN pip install --no-cache-dir \
        "numpy>=1.26,<3" \
        "pandas>=2.1,<3" \
        "scipy>=1.11,<2" \
        "sympy>=1.12,<2" \
        "matplotlib>=3.8,<4"

# Driver present for the `exec` subcommand / file parity; httpx for any URL work
# the operator wires. Not on the default path.
RUN pip install --no-cache-dir "httpx>=0.27,<0.29"
WORKDIR /opt/driver
COPY driver/ /opt/driver/
ENV PYTHONPATH=/opt/driver

# Persistent kernel for the Code-Interpreter session path: when the host starts
# this image with command=["python","/opt/kernel/kernel.py"] it serves a live
# namespace over a /tmp AF_UNIX socket, driven per-call by client.py via
# `docker exec` (see docker_runsc.py kernel registry). The default `python -c`
# path is unaffected — these files are inert unless the kernel command is used.
COPY code-exec/kernel/ /opt/kernel/

# Non-root (uid matches docker_runsc._SANDBOX_UID); read-only-rootfs compatible
# — writes land in /workspace (tmpfs) or /tmp only.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace && chown 10001:10001 /workspace
USER 10001

# Run user code from the writable tmpfs so relative file writes land somewhere
# the read-only root FS allows (the rest of the FS is mounted read-only).
WORKDIR /workspace

# No ENTRYPOINT: the host supplies `python -c <source>` as the full command.
