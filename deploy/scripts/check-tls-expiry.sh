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

# Parse a certificate's expiry into epoch seconds.
# Returns via the PARSED_EPOCH variable; sets to empty string on failure.
parse_cert_expiry() {
  local cert="$1"
  PARSED_EPOCH=""

  local expiry_date
  expiry_date=$(echo "$cert" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  if [ -z "$expiry_date" ]; then return; fi

  # Force C locale — openssl always emits English month abbreviations but
  # the system date command may parse %b according to LC_TIME.
  local epoch
  epoch=$(LC_ALL=C date -d "$expiry_date" +%s 2>/dev/null) || \
  epoch=$(LC_ALL=C date -j -f "%b %d %T %Y %Z" "$expiry_date" +%s 2>/dev/null) || \
  epoch=""

  if [ -n "$epoch" ] && [ "$epoch" -gt 0 ] 2>/dev/null; then
    PARSED_EPOCH="$epoch"
  fi
}

# --- Collect certificates ---

# Nginx external TLS cert — read from volume or probe live
NGINX_CERT=""
if docker volume inspect smap_nginx_certs &>/dev/null; then
  NGINX_CERT=$(docker run --rm -v smap_nginx_certs:/certs:ro alpine cat /certs/smap.crt 2>/dev/null || echo "")
fi

if [ -n "$NGINX_CERT" ]; then
  CERTS+=("$NGINX_CERT")
  LABELS+=("nginx_external")
else
  # Probe live nginx with a 5-second connect timeout
  LIVE_CERT=$(echo | timeout 5 openssl s_client -connect localhost:10443 -servername localhost 2>/dev/null | openssl x509 2>/dev/null || echo "")
  if [ -n "$LIVE_CERT" ]; then
    CERTS+=("$LIVE_CERT")
    LABELS+=("nginx_external")
  fi
fi

# Vault internal TLS cert
VAULT_CERT_PATH="$REPO_ROOT/deploy/vault/certs/vault-internal.crt"
if [ -f "$VAULT_CERT_PATH" ]; then
  CERTS+=("$(cat "$VAULT_CERT_PATH")")
  LABELS+=("vault_internal")
fi

# Vault internal CA cert
VAULT_CA_PATH="$REPO_ROOT/deploy/vault/certs/vault-internal-ca.pem"
if [ -f "$VAULT_CA_PATH" ]; then
  CERTS+=("$(cat "$VAULT_CA_PATH")")
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

    parse_cert_expiry "$CERT"
    if [ -z "$PARSED_EPOCH" ]; then continue; fi

    REMAINING=$((PARSED_EPOCH - NOW))

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

    parse_cert_expiry "$CERT"
    if [ -z "$PARSED_EPOCH" ]; then
      printf "  %-20s %-24s %-12s %b\n" "$LABEL" "PARSE ERROR" "-" "${RED}ERROR${NC}"
      continue
    fi

    REMAINING=$((PARSED_EPOCH - NOW))
    DAYS=$((REMAINING / 86400))

    # Recover the human-readable date for display
    EXPIRY_DATE=$(echo "$CERT" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)

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
