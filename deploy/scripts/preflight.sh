#!/usr/bin/env bash
# SMAP Pre-deployment Preflight Check
# Validates that the host environment is ready for staging/production deployment.
#
# Usage:
#   bash deploy/scripts/preflight.sh [--staging|--prod]
#
# Exit codes:
#   0 — all checks pass
#   1 — one or more FATAL checks failed (deployment will not succeed)
#   2 — warnings only (deployment may succeed but has risks)

set -euo pipefail

MODE="${1:---staging}"
WARNINGS=0
FATALS=0

RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
NC='\033[0m'

pass()  { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; WARNINGS=$((WARNINGS + 1)); }
fatal() { echo -e "  ${RED}✗${NC} $1"; FATALS=$((FATALS + 1)); }

echo "═══════════════════════════════════════════════════════"
echo " SMAP Preflight Check  (mode: ${MODE})"
echo "═══════════════════════════════════════════════════════"
echo ""

# ─── 1. Docker ────────────────────────────────────────────
echo "▸ Docker"

if ! command -v docker &>/dev/null; then
  fatal "docker CLI not found"
else
  DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "")
  if [ -z "$DOCKER_VER" ]; then
    fatal "Docker daemon not reachable (is it running?)"
  else
    MAJOR=$(echo "$DOCKER_VER" | cut -d. -f1)
    if [ "$MAJOR" -lt 25 ]; then
      warn "Docker $DOCKER_VER detected; 25+ recommended"
    else
      pass "Docker $DOCKER_VER"
    fi
  fi
fi

if ! docker compose version &>/dev/null; then
  fatal "docker compose plugin not available"
else
  pass "docker compose $(docker compose version --short 2>/dev/null)"
fi

# ─── 2. .env file ─────────────────────────────────────────
echo ""
echo "▸ Environment file"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [ ! -f "$ENV_FILE" ]; then
  fatal ".env file not found at $ENV_FILE (copy from .env.example)"
else
  pass ".env file exists"

  check_env_var() {
    local var="$1"
    local required="$2"
    if grep -qE "^\s*${var}=" "$ENV_FILE" && ! grep -qE "^\s*#\s*${var}=" "$ENV_FILE"; then
      local val
      val=$(grep -E "^\s*${var}=" "$ENV_FILE" | head -1 | cut -d= -f2-)
      # Strip surrounding quotes (single or double) that .env files often use
      val="${val#\"}" ; val="${val%\"}"
      val="${val#\'}" ; val="${val%\'}"
      if [ -z "$val" ] || [[ "$val" == *"changeme"* ]]; then
        if [ "$required" = "fatal" ]; then
          fatal "$var is empty or placeholder"
        else
          warn "$var is empty or placeholder"
        fi
      else
        pass "$var set"
      fi
    else
      if [ "$required" = "fatal" ]; then
        fatal "$var not found in .env"
      else
        warn "$var not found in .env"
      fi
    fi
  }

  check_env_var "SMAP_DB_PASSWORD" "fatal"
  check_env_var "SMAP_REDIS_PASSWORD" "fatal"
  check_env_var "SMAP_MINIO_ROOT_USER" "fatal"
  check_env_var "SMAP_MINIO_ROOT_PASSWORD" "fatal"
  check_env_var "EGRESS_PROXY_SHARED_SECRET" "fatal"

  # Verify egress secret is not the dev placeholder
  EGRESS_VAL=$(grep -E "^\s*EGRESS_PROXY_SHARED_SECRET=" "$ENV_FILE" | head -1 | cut -d= -f2- || echo "")
  EGRESS_VAL="${EGRESS_VAL#\"}" ; EGRESS_VAL="${EGRESS_VAL%\"}"
  EGRESS_VAL="${EGRESS_VAL#\'}" ; EGRESS_VAL="${EGRESS_VAL%\'}"
  if [[ "$EGRESS_VAL" == "0000000000000000000000000000000000000000000000000000000000000001" ]]; then
    fatal "EGRESS_PROXY_SHARED_SECRET is the dev placeholder — generate with: openssl rand -hex 32"
  fi

  if [ "$MODE" = "--prod" ]; then
    check_env_var "SMAP_VAULT_ROLE_ID" "fatal"
    check_env_var "SMAP_VAULT_SECRET_ID" "fatal"
    check_env_var "SMAP_SEC_CORS_ORIGINS" "fatal"
    check_env_var "SMTP_HOST" "warn"

    # Session cookie must be Secure in prod (HTTPS) to avoid silent logouts
    COOKIE_SECURE=$(grep -E "^\s*SMAP_SEC_SESSION_COOKIE_SECURE=" "$ENV_FILE" | head -1 | cut -d= -f2- || echo "")
    COOKIE_SECURE="${COOKIE_SECURE#\"}" ; COOKIE_SECURE="${COOKIE_SECURE%\"}"
    COOKIE_SECURE="${COOKIE_SECURE#\'}" ; COOKIE_SECURE="${COOKIE_SECURE%\'}"
    if [ "$COOKIE_SECURE" != "true" ] && [ "$COOKIE_SECURE" != "True" ] && [ "$COOKIE_SECURE" != "1" ]; then
      fatal "SMAP_SEC_SESSION_COOKIE_SECURE must be 'true' in prod (browsers drop refresh tokens without Secure flag over HTTPS)"
    else
      pass "SMAP_SEC_SESSION_COOKIE_SECURE=true"
    fi
  else
    check_env_var "SMAP_VAULT_ROLE_ID" "warn"
    check_env_var "SMAP_VAULT_SECRET_ID" "warn"
    check_env_var "SMTP_HOST" "warn"
  fi
fi

# ─── 3. TLS certificates ──────────────────────────────────
echo ""
echo "▸ TLS certificates"

VAULT_CERTS_DIR="$REPO_ROOT/deploy/vault/certs"

if [ -d "$VAULT_CERTS_DIR" ]; then
  if [ -f "$VAULT_CERTS_DIR/vault-internal-ca.pem" ] && \
     [ -f "$VAULT_CERTS_DIR/vault-internal.crt" ] && \
     [ -f "$VAULT_CERTS_DIR/vault-internal.key" ]; then
    pass "Vault internal TLS certs present"
  else
    fatal "Vault certs directory exists but missing files (run: bash deploy/vault/gen-internal-tls.sh)"
  fi
else
  fatal "Vault certs not generated (run: cd deploy/vault && bash gen-internal-tls.sh)"
fi

if docker volume inspect smap_nginx_certs &>/dev/null; then
  pass "nginx_certs Docker volume exists"
else
  warn "nginx_certs volume not found — TLS cert must be mounted before starting nginx"
fi

# ─── 4. Vault CLI ─────────────────────────────────────────
echo ""
echo "▸ Vault"

if ! command -v vault &>/dev/null; then
  warn "vault CLI not found (needed for bootstrap and unseal)"
else
  pass "vault CLI available ($(vault version | head -1))"
fi

# ─── 5. Host resources ────────────────────────────────────
echo ""
echo "▸ Host resources"

CPUS=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "0")
MEM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "0")
MEM_GB=$((MEM_KB / 1024 / 1024))

if [ "$CPUS" -gt 0 ]; then
  if [ "$MODE" = "--prod" ] && [ "$CPUS" -lt 16 ]; then
    warn "Host has $CPUS CPUs; prod recommends 16+"
  elif [ "$MODE" = "--staging" ] && [ "$CPUS" -lt 4 ]; then
    warn "Host has $CPUS CPUs; staging recommends 4+"
  else
    pass "$CPUS CPUs"
  fi
fi

if [ "$MEM_GB" -gt 0 ]; then
  if [ "$MODE" = "--prod" ] && [ "$MEM_GB" -lt 32 ]; then
    warn "Host has ${MEM_GB}GB RAM; prod recommends 32-64GB"
  elif [ "$MODE" = "--staging" ] && [ "$MEM_GB" -lt 16 ]; then
    warn "Host has ${MEM_GB}GB RAM; staging recommends 16GB+"
  else
    pass "${MEM_GB}GB RAM"
  fi
fi

# Use -P (POSIX) to prevent LVM device names from wrapping to a second line
DISK_AVAIL=$(df -PBG / 2>/dev/null | awk 'NR==2 {print $4}' | tr -d 'G' || echo "0")
if [ "$DISK_AVAIL" -gt 0 ] 2>/dev/null; then
  if [ "$DISK_AVAIL" -lt 40 ]; then
    warn "Only ${DISK_AVAIL}GB disk free; recommend 40GB+"
  else
    pass "${DISK_AVAIL}GB disk available"
  fi
fi

# ─── 6. gVisor / runsc ───────────────────────────────────
echo ""
echo "▸ Sandbox (gVisor)"

if command -v runsc &>/dev/null; then
  pass "runsc available"
elif docker info 2>/dev/null | grep -q runsc; then
  pass "runsc runtime registered in Docker"
else
  warn "runsc (gVisor) not detected — MCP sandbox will not work without it"
fi

# Check sandbox images
if docker image inspect smap/mcp-runtime:pinned &>/dev/null; then
  pass "smap/mcp-runtime:pinned image built"
else
  warn "smap/mcp-runtime:pinned image not found (build with: docker compose --profile sandbox-build build)"
fi

if docker image inspect smap/code-exec:pinned &>/dev/null; then
  pass "smap/code-exec:pinned image built"
else
  warn "smap/code-exec:pinned image not found (build with: docker compose --profile sandbox-build build)"
fi

# ─── 7. Compose file validation ──────────────────────────
echo ""
echo "▸ Compose validation"

COMPOSE_DIR="$REPO_ROOT/deploy/compose"

if [ "$MODE" = "--prod" ]; then
  OVERLAY="docker-compose.prod.yml"
else
  OVERLAY="docker-compose.staging.yml"
fi

if docker compose -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/$OVERLAY" config --quiet 2>/dev/null; then
  pass "Compose config valid (base + $OVERLAY)"
else
  fatal "Compose config invalid — run: docker compose -f ... config 2>&1 to see errors"
fi

# ─── 8. Network / port conflicts ─────────────────────────
echo ""
echo "▸ Port availability"

check_port() {
  local port="$1"
  local service="$2"
  if ss -tlnp 2>/dev/null | grep -q ":${port} " || \
     netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
    warn "Port $port ($service) already in use"
  else
    pass "Port $port ($service) available"
  fi
}

check_port 10080 "nginx HTTP"
check_port 10443 "nginx HTTPS"

# ─── Summary ─────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
if [ "$FATALS" -gt 0 ]; then
  echo -e " ${RED}FAILED${NC}: $FATALS fatal issue(s), $WARNINGS warning(s)"
  echo " Fix all fatal issues before deploying."
  echo "═══════════════════════════════════════════════════════"
  exit 1
elif [ "$WARNINGS" -gt 0 ]; then
  echo -e " ${YELLOW}PASS WITH WARNINGS${NC}: $WARNINGS warning(s)"
  echo " Deployment may succeed but review warnings above."
  echo "═══════════════════════════════════════════════════════"
  exit 2
else
  echo -e " ${GREEN}ALL CHECKS PASSED${NC}"
  echo " Ready to deploy. Next: docker compose -f ... up -d"
  echo "═══════════════════════════════════════════════════════"
  exit 0
fi
