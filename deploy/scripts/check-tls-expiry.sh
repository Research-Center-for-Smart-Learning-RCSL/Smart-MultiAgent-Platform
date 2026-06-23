#!/usr/bin/env bash
# TLS Certificate Expiry Checker
# Outputs Prometheus-compatible text metrics for certificate expiry.
#
# Usage:
#   bash deploy/scripts/check-tls-expiry.sh [--metrics|--human]
#
# --metrics: outputs Prometheus exposition format (for textfile collector)
# --human:   outputs human-readable table (default)
#
# For automated monitoring, run this via cron and write to the Prometheus
# node-exporter textfile directory:
#   */6 * * * * bash /opt/smap/deploy/scripts/check-tls-expiry.sh --metrics \
#     > /var/lib/prometheus/node-exporter/smap_tls.prom

set -euo pipefail

MODE="${1:---human}"
NOW=$(date +%s)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
NC='\033[0m'

declare -a CERTS=()
declare -a LABELS=()

# --- Nginx external TLS cert ---
# Check inside the Docker volume if accessible, or fall back to openssl s_client
NGINX_CERT=""
if docker volume inspect smap_nginx_certs &>/dev/null; then
  NGINX_CERT=$(docker run --rm -v smap_nginx_certs:/certs:ro alpine cat /certs/smap.crt 2>/dev/null || echo "")
fi

if [ -n "$NGINX_CERT" ]; then
  CERTS+=("$NGINX_CERT")
  LABELS+=("nginx_external")
else
  # Try connecting to the local nginx to get the cert
  LIVE_CERT=$(echo | openssl s_client -connect localhost:10443 -servername localhost 2>/dev/null | openssl x509 2>/dev/null || echo "")
  if [ -n "$LIVE_CERT" ]; then
    CERTS+=("$LIVE_CERT")
    LABELS+=("nginx_external")
  fi
fi

# --- Vault internal TLS cert ---
VAULT_CERT_PATH="$REPO_ROOT/deploy/vault/certs/vault-internal.crt"
if [ -f "$VAULT_CERT_PATH" ]; then
  VAULT_CERT=$(cat "$VAULT_CERT_PATH")
  CERTS+=("$VAULT_CERT")
  LABELS+=("vault_internal")
fi

# --- Vault internal CA cert ---
VAULT_CA_PATH="$REPO_ROOT/deploy/vault/certs/vault-internal-ca.pem"
if [ -f "$VAULT_CA_PATH" ]; then
  VAULT_CA=$(cat "$VAULT_CA_PATH")
  CERTS+=("$VAULT_CA")
  LABELS+=("vault_ca")
fi

# --- Output ---

if [ "$MODE" = "--metrics" ]; then
  echo "# HELP smap_tls_cert_expiry_seconds Seconds until TLS certificate expires"
  echo "# TYPE smap_tls_cert_expiry_seconds gauge"
  echo "# HELP smap_vault_tls_cert_expiry_seconds Seconds until Vault TLS certificate expires"
  echo "# TYPE smap_vault_tls_cert_expiry_seconds gauge"

  for i in "${!CERTS[@]}"; do
    LABEL="${LABELS[$i]}"
    CERT="${CERTS[$i]}"

    EXPIRY_DATE=$(echo "$CERT" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    if [ -z "$EXPIRY_DATE" ]; then continue; fi

    EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$EXPIRY_DATE" +%s 2>/dev/null || echo "0")
    REMAINING=$((EXPIRY_EPOCH - NOW))

    if [[ "$LABEL" == vault_* ]]; then
      echo "smap_vault_tls_cert_expiry_seconds{domain=\"$LABEL\"} $REMAINING"
    else
      echo "smap_tls_cert_expiry_seconds{domain=\"$LABEL\"} $REMAINING"
    fi
  done

elif [ "$MODE" = "--human" ]; then
  echo "═══════════════════════════════════════════════════════"
  echo " SMAP TLS Certificate Expiry Report"
  echo "═══════════════════════════════════════════════════════"
  echo ""

  if [ ${#CERTS[@]} -eq 0 ]; then
    echo "  No certificates found. Ensure:"
    echo "    - nginx_certs volume exists with smap.crt"
    echo "    - deploy/vault/certs/ contains vault-internal.crt"
    exit 0
  fi

  printf "  %-20s %-24s %-12s %s\n" "CERTIFICATE" "EXPIRES" "REMAINING" "STATUS"
  printf "  %-20s %-24s %-12s %s\n" "───────────" "───────" "─────────" "──────"

  for i in "${!CERTS[@]}"; do
    LABEL="${LABELS[$i]}"
    CERT="${CERTS[$i]}"

    EXPIRY_DATE=$(echo "$CERT" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    if [ -z "$EXPIRY_DATE" ]; then
      printf "  %-20s %-24s %-12s %b\n" "$LABEL" "PARSE ERROR" "-" "${RED}ERROR${NC}"
      continue
    fi

    EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$EXPIRY_DATE" +%s 2>/dev/null || echo "0")
    REMAINING=$((EXPIRY_EPOCH - NOW))
    DAYS=$((REMAINING / 86400))

    if [ "$DAYS" -lt 7 ]; then
      STATUS="${RED}CRITICAL${NC}"
    elif [ "$DAYS" -lt 30 ]; then
      STATUS="${YELLOW}WARNING${NC}"
    else
      STATUS="${GREEN}OK${NC}"
    fi

    printf "  %-20s %-24s %-12s %b\n" "$LABEL" "$EXPIRY_DATE" "${DAYS}d" "$STATUS"
  done

  echo ""
  echo "  Thresholds: CRITICAL <7d, WARNING <30d, OK ≥30d"
  echo "═══════════════════════════════════════════════════════"
fi
