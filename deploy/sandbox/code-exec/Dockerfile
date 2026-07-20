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
FROM python:3.12-slim

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

# Non-root (uid matches docker_runsc._SANDBOX_UID); read-only-rootfs compatible.
# Where writes land depends on how the host invokes this image:
#   - run-and-burn (`python -c`): /workspace is a tmpfs, plus /tmp.
#   - live kernel: /session is this chatroom's own volume and is the only
#     writable mount; /workspace is the agent volume, bound READ-ONLY ([R12.03]),
#     and /tmp.
# Both mount points must exist here owned by the sandbox uid. Docker seeds a
# fresh named volume from the image directory at that path, ownership included;
# a path absent from the image gets a mountpoint created root-owned, which the
# kernel (uid 10001) then cannot mkdir into. /session is created empty for every
# new chatroom, so it hits that path on the first code_exec of every room.
#
# The MODE below also leaks into the run-and-burn path, invisibly: a tmpfs
# inherits the covered directory's mode (but NOT its ownership), so 0755 here
# means a 0755 tmpfs at /workspace there. That is why docker_runsc._sandbox_tmpfs
# has to name uid/gid explicitly. Change this mode and read that function first.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace /session && chown 10001:10001 /workspace /session
USER 10001

# Only the run-and-burn path uses this: the kernel chdirs to its session dir at
# startup (kernel.py), where /workspace is read-only and would be the wrong cwd.
WORKDIR /workspace

# No ENTRYPOINT: the host supplies `python -c <source>` as the full command.

# Build stamp: a content hash of deploy/sandbox/**, injected by CI (ci.yml
# sandbox-images) and asserted to round-trip there. Answers "was this image
# built from these sources", which is otherwise unanswerable -- an image carries
# no record of its context. NOT a security control: anyone who can build the
# image can set the label, so it detects drift, not tampering.
#
# Kept LAST on purpose. An ARG invalidates every layer below it, so placing this
# higher would rebuild the whole image on any source change.
ARG SMAP_SANDBOX_STAMP=dev
LABEL smap.sandbox.stamp="${SMAP_SANDBOX_STAMP}"
