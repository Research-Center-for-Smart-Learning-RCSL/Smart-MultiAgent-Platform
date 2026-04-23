# ============================================================================
# SMAP Backend Vault Policy
# ----------------------------------------------------------------------------
# Attached to: AppRole role `smap-backend`, mounted at auth/approle.
# Used by:     FastAPI `backend-web` and `backend-worker` processes.
#
# Principle: the backend MUST NOT be able to read, export, list, or otherwise
# extract the Transit master key material. It can only create wrapped DEKs
# and unwrap them. It can read its own configuration secrets from KV v2.
# It can renew and look up its own token. It can do NOTHING else.
#
# Referenced by REQUIREMENTS.md §7.6 (envelope encryption) and §22.x (REST).
# ============================================================================

# ---------------------------------------------------------------------------
# Transit engine — provider-secret key
# ---------------------------------------------------------------------------
# Create a plaintext DEK (datakey) wrapped by the named transit key.
# Response returns {plaintext, ciphertext} where ciphertext is the wrapped DEK
# stored alongside the ciphertext of the provider secret in Postgres.
path "transit/datakey/plaintext/smap-provider-secret" {
  capabilities = ["update"]
}

# Unwrap a previously wrapped DEK at outbound-call time, in order to decrypt
# the provider secret once. Plaintext DEK is zeroized after use.
path "transit/decrypt/smap-provider-secret" {
  capabilities = ["update"]
}

# NOTE: we deliberately do NOT grant:
#   - transit/encrypt/*           (we encrypt locally with the DEK; Vault only
#                                   wraps the DEK, which is done by datakey/plaintext)
#   - transit/keys/*              (read / list / rotate / config / export)
#   - transit/hmac/*              (not used by backend)
#   - transit/rewrap/*            (operator-only; see smap-rotation.hcl)
#
# Denying these ensures that a compromised backend token cannot dump the
# master key, cannot enumerate provider secrets, and cannot pivot to other
# Transit keys that may be added later.

# ---------------------------------------------------------------------------
# Transit engine — guest-link signing key (optional; used if we sign guest tokens
# server-side rather than using HMAC with a static secret)
# ---------------------------------------------------------------------------
path "transit/sign/smap-guest-link" {
  capabilities = ["update"]
}

path "transit/sign/smap-guest-link/*" {
  capabilities = ["update"]
}

path "transit/verify/smap-guest-link" {
  capabilities = ["update"]
}

path "transit/verify/smap-guest-link/*" {
  capabilities = ["update"]
}

# ---------------------------------------------------------------------------
# Transit engine — JWT signing key (RS256, quarterly rotation w/ 7-day verify
# overlap, R6.03). Backend signs access tokens at login/refresh and verifies
# them on every protected request. Public-key material is fetched via
# transit/keys/smap-jwt-sign (read) and cached in-process, keyed by `kid`.
# ---------------------------------------------------------------------------
path "transit/sign/smap-jwt-sign" {
  capabilities = ["update"]
}

path "transit/sign/smap-jwt-sign/*" {
  capabilities = ["update"]
}

path "transit/verify/smap-jwt-sign" {
  capabilities = ["update"]
}

path "transit/verify/smap-jwt-sign/*" {
  capabilities = ["update"]
}

# Public-key readout for JWKS. Vault returns public keys only for non-exportable
# asymmetric keys; private material never leaves Vault.
path "transit/keys/smap-jwt-sign" {
  capabilities = ["read"]
}

# ---------------------------------------------------------------------------
# KV v2 — non-secret application configuration that we still want auditable
# and rotatable without a redeploy: SMTP creds, search provider key, MinIO
# root creds (if not passed via env), Neo4j password, Qdrant API key.
# ---------------------------------------------------------------------------
path "secret/data/smap/config/*" {
  capabilities = ["read"]
}

path "secret/metadata/smap/config/*" {
  capabilities = ["read"]
}

# ---------------------------------------------------------------------------
# Token self-management
# ---------------------------------------------------------------------------
path "auth/token/lookup-self" {
  capabilities = ["read"]
}

path "auth/token/renew-self" {
  capabilities = ["update"]
}

# Tokens issued by this policy should be periodic/renewable; the
# renewal cadence is configured at the AppRole level (see README.md).

# ---------------------------------------------------------------------------
# Explicit denies (defense-in-depth; Vault default-denies, but calling these
# out documents intent and survives policy merges if any operator later grants
# broader permissions by mistake).
# ---------------------------------------------------------------------------
path "sys/*" {
  capabilities = ["deny"]
}

path "auth/token/create*" {
  capabilities = ["deny"]
}

path "auth/token/revoke*" {
  capabilities = ["deny"]
}

# Broad deny on key management; the exact-path `transit/keys/smap-jwt-sign`
# above wins by specificity and grants the read needed for JWKS.
path "transit/keys/*" {
  capabilities = ["deny"]
}

path "transit/export/*" {
  capabilities = ["deny"]
}

path "transit/random" {
  capabilities = ["deny"]
}

path "identity/*" {
  capabilities = ["deny"]
}
