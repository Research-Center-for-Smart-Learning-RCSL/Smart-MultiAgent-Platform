# ============================================================================
# SMAP Rotation Operator Policy
# ----------------------------------------------------------------------------
# Attached to: a dedicated human operator or scheduled CI job that rotates
#              Transit keys and optionally re-wraps old ciphertexts.
# NEVER attached to: the backend AppRole.
#
# This role CAN rotate the master key and rewrap existing wrapped DEKs to the
# latest version, but still CANNOT export or decrypt provider secrets via the
# transit/decrypt endpoint (because it does not need to — rewrap is server-side
# inside Vault and never releases plaintext).
#
# Referenced by REQUIREMENTS.md §7.6 and by deploy/vault/README.md §Rotation.
# ============================================================================

# Read key metadata (version, creation time, deletion_allowed flag).
path "transit/keys/smap-provider-secret" {
  capabilities = ["read"]
}

# Rotate the master key: creates a new version. Old versions remain decryptable
# until min_decryption_version is advanced.
path "transit/keys/smap-provider-secret/rotate" {
  capabilities = ["update"]
}

# Advance the minimum decryption version AFTER all old DEKs have been rewrapped.
# This is the final step that deprecates older key versions.
path "transit/keys/smap-provider-secret/config" {
  capabilities = ["update"]
}

# Rewrap an existing wrapped DEK to the current key version. This is a
# Vault-server-side operation: the operator supplies the old wrapped DEK and
# receives a new wrapped DEK; plaintext never leaves Vault.
path "transit/rewrap/smap-provider-secret" {
  capabilities = ["update"]
}

# Same three for the guest-link signing key, if rotated on the same schedule.
path "transit/keys/smap-guest-link" {
  capabilities = ["read"]
}

path "transit/keys/smap-guest-link/rotate" {
  capabilities = ["update"]
}

path "transit/keys/smap-guest-link/config" {
  capabilities = ["update"]
}

# JWT signing key — quarterly rotation with 7-day verify-overlap (R6.03).
# The rotation operator advances min_decryption_version (for the overlap
# guarantee, `min_decryption_version` is what Vault gates decrypt/verify on)
# only after the overlap window has expired.
path "transit/keys/smap-jwt-sign" {
  capabilities = ["read"]
}

path "transit/keys/smap-jwt-sign/rotate" {
  capabilities = ["update"]
}

path "transit/keys/smap-jwt-sign/config" {
  capabilities = ["update"]
}

# Token self-management.
path "auth/token/lookup-self" {
  capabilities = ["read"]
}

path "auth/token/renew-self" {
  capabilities = ["update"]
}

# Explicit denies to guarantee that even a compromised rotation token cannot
# decrypt production data or mint new auth roles.
path "transit/decrypt/*" {
  capabilities = ["deny"]
}

path "transit/datakey/*" {
  capabilities = ["deny"]
}

path "transit/export/*" {
  capabilities = ["deny"]
}

path "secret/data/smap/*" {
  capabilities = ["deny"]
}

path "sys/*" {
  capabilities = ["deny"]
}

path "auth/*/create*" {
  capabilities = ["deny"]
}

path "identity/*" {
  capabilities = ["deny"]
}
