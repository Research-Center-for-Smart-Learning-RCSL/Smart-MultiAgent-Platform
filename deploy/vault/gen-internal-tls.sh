#!/usr/bin/env bash
# Generate a self-signed TLS certificate pair for Vault's internal listener.
#
# The cert is used ONLY for intra-Docker-network traffic (backend → vault).
# External users never hit Vault directly — nginx terminates their TLS.
#
# Usage:
#   ./gen-internal-tls.sh [output-dir]
#
# Output:
#   <output-dir>/vault-internal-ca.pem   — CA certificate (mount into clients)
#   <output-dir>/vault-internal.crt      — server cert
#   <output-dir>/vault-internal.key      — server private key
#
# The cert's SAN covers both the compose service name ("vault") and localhost,
# so it works for both container-to-container and local `vault` CLI calls.

set -euo pipefail

OUTDIR="${1:-$(cd "$(dirname "$0")" && pwd)/certs}"
mkdir -p "$OUTDIR"

# 1. Internal CA (self-signed root)
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -nodes -days 3650 \
  -keyout "$OUTDIR/vault-internal-ca.key" \
  -out    "$OUTDIR/vault-internal-ca.pem" \
  -subj   "/CN=smap-vault-internal-ca"

# 2. Server key + CSR
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -nodes \
  -keyout "$OUTDIR/vault-internal.key" \
  -out    "$OUTDIR/vault-internal.csr" \
  -subj   "/CN=vault"

# 3. Sign with the CA; add SANs
openssl x509 -req -in "$OUTDIR/vault-internal.csr" \
  -CA "$OUTDIR/vault-internal-ca.pem" \
  -CAkey "$OUTDIR/vault-internal-ca.key" \
  -CAcreateserial \
  -days 3650 \
  -extfile <(printf "subjectAltName=DNS:vault,DNS:localhost,IP:127.0.0.1") \
  -out "$OUTDIR/vault-internal.crt"

# 4. Clean up intermediate files
rm -f "$OUTDIR/vault-internal.csr" "$OUTDIR/vault-internal-ca.key" "$OUTDIR/vault-internal-ca.srl"

chmod 600 "$OUTDIR/vault-internal.key"
chmod 644 "$OUTDIR/vault-internal.crt" "$OUTDIR/vault-internal-ca.pem"

echo "Vault internal TLS certs written to $OUTDIR"
echo "  CA:   $OUTDIR/vault-internal-ca.pem"
echo "  Cert: $OUTDIR/vault-internal.crt"
echo "  Key:  $OUTDIR/vault-internal.key"
