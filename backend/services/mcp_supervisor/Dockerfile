# MCP Sandbox Supervisor — verifies gVisor (runsc) runtime availability.
# Build context: backend/ directory.
#
# Uses docker:27-cli as the base (Alpine + docker CLI binary) and adds
# Python 3 for the health-check HTTP server. No pip packages needed — only
# stdlib + the docker CLI binary that is already in the image.
#
# NOTE: runs as root intentionally — the Docker CLI needs access to
# /var/run/docker.sock (owned by root:docker). The supervisor only reads
# `docker info`; it does not execute containers itself (that is done by
# backend-web via the socket proxy in production).

FROM docker:29.6.1-cli

RUN apk add --no-cache python3

COPY services/mcp_supervisor/main.py /app/main.py

WORKDIR /app

EXPOSE 9090

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=4)"

CMD ["python3", "main.py"]
