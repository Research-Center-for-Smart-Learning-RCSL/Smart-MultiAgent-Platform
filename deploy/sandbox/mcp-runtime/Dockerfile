# smap/mcp-runtime — stdio + URL MCP servers run here under gVisor (R12.03 / K.5).
#
# The host (contexts/agents/infrastructure/sandbox/docker_runsc.py) runs this
# image with command=[probe|invoke|file] and SMAP_* env, reads JSON/text on
# stdout, and treats exit 42 as egress-denied. The ENTRYPOINT is the in-image
# driver, so the host's bare command IS the driver subcommand.
#
# Network reality: the sandbox lands on the gateway-less `egress_net`, so there
# is no runtime route to npm/PyPI — stdio MCP servers must be BAKED IN here.
# URL servers are reached through the egress proxy by the driver (custom HMAC
# forwarder, pre-signed per project by the host — no secret in the image).
FROM node:22-slim

# --- system Python + uv (provides uvx) for Python-packaged MCP servers -------
# node:22-slim is Debian bookworm. We add the distro Python 3 plus pipx/uv so
# both `npx` (npm ecosystem) and `uvx` (PyPI ecosystem) stdio servers launch.
#
# KNOWN DEVIATION from the K.5 spec's "Python 3.12": bookworm's distro python3
# is 3.11 and Debian ships no 3.12 apt package — getting 3.12 here means a
# source build or a third-party repo, bloating and destabilising the sandbox
# image for no functional gain (the driver + mcp SDK are 3.11-compatible, and
# uvx-launched servers resolve their own interpreters via uv). Revisit when
# the base moves to a trixie-based node image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1
# The driver's runtime deps + uv (uvx). `mcp` is the official Python SDK the
# driver speaks; httpx backs URL-source egress through the proxy.
RUN pip3 install --no-cache-dir "mcp>=1.2,<2" "httpx>=0.27,<0.29" "uv>=0.4"

# --- bake the reference stdio MCP server so probe works offline --------------
# CI's smoke test probes this server; operators extend this list for the
# servers their agents bind. Installed globally so `npx <name>` resolves from
# the on-image cache with no network.
RUN npm install -g @modelcontextprotocol/server-everything \
    && npm cache clean --force

# --- the driver --------------------------------------------------------------
WORKDIR /opt/driver
COPY driver/ /opt/driver/
# HOME/caches point at /tmp, which the host mounts as a writable tmpfs (the root
# FS is read-only) so npx/uvx can write their caches; see docker_runsc._sandbox_tmpfs.
ENV PYTHONPATH=/opt/driver \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NPM_CONFIG_CACHE=/tmp/.npm \
    UV_CACHE_DIR=/tmp/.uv \
    HOME=/tmp

# --- non-root, read-only-rootfs compatible -----------------------------------
# uid matches docker_runsc._SANDBOX_UID (10001). All writes go to /workspace
# (tmpfs or the per-agent volume) or /tmp; the rest of the FS is mounted
# read-only by the host.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace && chown 10001:10001 /workspace
USER 10001

ENTRYPOINT ["python3", "/opt/driver/driver.py"]
