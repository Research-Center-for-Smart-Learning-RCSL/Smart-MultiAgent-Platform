# SMAP Vault Bootstrap & Operations

This directory contains the policy files and operational procedures for the
HashiCorp Vault instance that guards SMAP's provider-secret envelope
encryption (see `REQUIREMENTS.md` §7.6).

Files:

| Path | Attached to | Purpose |
|---|---|---|
| `policies/smap-backend.hcl`  | AppRole `smap-backend`  | Used by backend processes at runtime to wrap/unwrap DEKs and read config. |
| `policies/smap-rotation.hcl` | AppRole `smap-rotation` | Used by an operator or a periodic CI job to rotate Transit keys and rewrap DEKs. |

---

## 1. Prerequisites

- Vault 1.15+ running in the `vault` Docker service.
- Host-side working directory with the unseal keys stored **outside** the
  Postgres volume (losing both would still be catastrophic; keeping them
  separate limits single-volume-compromise blast radius).
- `vault` CLI reachable from an operator workstation: `export VAULT_ADDR=https://vault.smap.internal`.

---

## 2. First-time bootstrap

> Run **once** at cluster build. The resulting unseal keys + initial root token
> must be archived by the platform operator (e.g., in a sealed Keepass DB
> kept offline). The root token is revoked at the end of bootstrap.

```bash
# 1. Initialize (Shamir 3-of-5).
vault operator init -key-shares=5 -key-threshold=3 \
  > /path/to/safe/vault-init.txt

# 2. Unseal (quorum).
vault operator unseal <key-share-1>
vault operator unseal <key-share-2>
vault operator unseal <key-share-3>

# 3. Login as the initial root token (from step 1's output).
export VAULT_TOKEN=<initial-root-token>

# 4. Enable engines.
vault secrets enable -path=secret -version=2 kv
vault secrets enable transit

# 5. Create Transit keys. Important: deletion_allowed stays false.
vault write -f transit/keys/smap-provider-secret       type=aes256-gcm96
vault write    transit/keys/smap-provider-secret/config \
                                                        deletion_allowed=false \
                                                        exportable=false \
                                                        allow_plaintext_backup=false

vault write -f transit/keys/smap-guest-link             type=ed25519
vault write    transit/keys/smap-guest-link/config \
                                                        deletion_allowed=false \
                                                        exportable=false

vault write -f transit/keys/smap-jwt-sign               type=rsa-2048
vault write    transit/keys/smap-jwt-sign/config \
                                                        deletion_allowed=false \
                                                        exportable=false \
                                                        allow_plaintext_backup=false

# 6. Write policies.
vault policy write smap-backend  policies/smap-backend.hcl
vault policy write smap-rotation policies/smap-rotation.hcl

# 7. Enable AppRole auth and create the two roles.
vault auth enable approle

vault write auth/approle/role/smap-backend \
    token_policies="smap-backend" \
    token_ttl=1h                    \
    token_max_ttl=24h               \
    token_num_uses=0                \
    secret_id_num_uses=0            \
    secret_id_ttl=0                 \
    bind_secret_id=true             \
    local_secret_ids=false

vault write auth/approle/role/smap-rotation \
    token_policies="smap-rotation"  \
    token_ttl=30m                   \
    token_max_ttl=2h                \
    bind_secret_id=true

# 8. Fetch role_id / secret_id for each AppRole. Provision these into the
#    respective service's environment (see §4 and §5 below).
vault read  auth/approle/role/smap-backend/role-id
vault write -f auth/approle/role/smap-backend/secret-id

vault read  auth/approle/role/smap-rotation/role-id
vault write -f auth/approle/role/smap-rotation/secret-id

# 9. Seed initial KV config (example — operators adapt to their env).
vault kv put secret/smap/config/smtp       host=mail.example.com user=smap password=…
vault kv put secret/smap/config/search     provider=brave        key=brv-live-…
vault kv put secret/smap/config/minio      access_key=…          secret_key=…
vault kv put secret/smap/config/neo4j      password=…
vault kv put secret/smap/config/qdrant     api_key=…

# 10. Revoke the initial root token. From here on, only the AppRoles and
#     human operators with quorum-generated tokens can interact with Vault.
vault token revoke $VAULT_TOKEN
```

After step 10, Vault is "production mode" for SMAP's purposes.

---

## 3. Backend service integration

### 3.1 Environment

Each `backend-web` / `backend-worker` container receives:

```
VAULT_ADDR=https://vault.smap.internal
VAULT_ROLE_ID=<role_id from step 8>
VAULT_SECRET_ID_FILE=/run/secrets/smap-backend-secret-id
```

The `secret_id` is rendered into the container via a Docker secret (not an
env var) to keep it out of `docker inspect` output and process lists.

### 3.2 Login flow (on process start)

1. Call `POST /v1/auth/approle/login` with `{role_id, secret_id}`.
2. Receive a short-lived token with `policy = smap-backend`.
3. Install a background renew-self loop at `token_ttl / 2`.
4. On 403 from any endpoint, attempt one silent re-login; if it fails, bubble
   up a fatal error so the orchestrator restarts the container.

### 3.3 Encryption flow (per provider-secret upload)

```
# 1. Ask Vault for a fresh 256-bit DEK, wrapped under the master key.
POST /v1/transit/datakey/plaintext/smap-provider-secret
  body: { "bits": 256 }
  resp: { "plaintext": "<b64 32-byte DEK>", "ciphertext": "vault:v1:..." }

# 2. Encrypt the provider secret locally with AES-256-GCM.
#    nonce = 96-bit random; aad = user_id || key_id || provider.
#    Zeroize the plaintext DEK from memory immediately after use.

# 3. Persist to Postgres:
#    (ciphertext_bytes, nonce_bytes, dek_wrapped_text, ciphertext_hmac)
```

### 3.4 Decryption flow (per outbound call to provider)

```
# 1. Load (ciphertext, nonce, dek_wrapped) from Postgres.

# 2. Unwrap the DEK:
POST /v1/transit/decrypt/smap-provider-secret
  body: { "ciphertext": "<dek_wrapped>" }
  resp: { "plaintext": "<b64 32-byte DEK>" }

# 3. AES-256-GCM decrypt ciphertext using DEK + nonce + AAD.
# 4. Use the plaintext provider secret exactly ONCE for the HTTPS call.
# 5. Zeroize both DEK and plaintext secret.
```

---

## 4. Rotation procedure (quarterly or on suspected compromise)

```bash
# 0. Log in with the rotation AppRole (from a hardened operator workstation).
export VAULT_ADDR=…
export VAULT_TOKEN=$(vault write -format=json auth/approle/login \
    role_id=$ROT_ROLE_ID secret_id=$ROT_SECRET_ID | jq -r '.auth.client_token')

# 1. Rotate the master key: creates version N+1; N remains valid for decrypt.
vault write -f transit/keys/smap-provider-secret/rotate

# 2. Rewrap all existing DEKs. This is done by the backend in a streaming
#    migration: for each api_keys row, call /transit/rewrap with the old
#    dek_wrapped and replace the column. Rewrap does NOT release plaintext.
#
#    In practice this is a Python management command:
#      $ python -m smap.manage rewrap-keys --batch-size=200 --workers=4
#    The command runs under the smap-rotation policy, not the smap-backend
#    policy (so it can call /rewrap without needing decrypt).

# 3. Once every row has been rewrapped, advance the minimum decryption
#    version to deprecate N and all earlier versions.
vault write transit/keys/smap-provider-secret/config \
    min_decryption_version=<N+1>

# 4. Log the rotation in SMAP's audit log (action = "admin.vault_rotation_completed").
```

### 4.1 Rotation cadence

| Item | Cadence | Trigger |
|---|---|---|
| Master Transit key (`smap-provider-secret`) | Quarterly | Scheduled |
| Master Transit key | Immediately | On confirmed Vault compromise, backend compromise, or personnel change with access |
| JWT signing key (`smap-jwt-sign`) | Quarterly | Scheduled; old key version kept valid for 7-day verify overlap |
| Guest-link signing key (`smap-guest-link`) | Annually | Scheduled |
| AppRole `secret_id` for backend | Monthly | Scheduled; backend is rolling-restarted after each change |
| AppRole `secret_id` for rotation | After every use | Manual |
| Vault unseal keys | Never rotated cryptographically; but personnel assignments are reviewed annually | — |

---

## 5. Dev / staging concessions

For local development (`docker compose` on a developer's laptop), `vault`
runs in `-dev` mode with a fixed root token. The backend uses the same
AppRole flow, but against a throwaway dev Vault that is reset on every
`docker compose down`. **This is acceptable only when the dev data set
contains no real provider secrets.** The default dev seed script generates
fake provider keys (e.g. `sk-dev-xxxx-…`) that are known-invalid with the
real providers so that accidental data leaks are harmless.

---

## 6. Disaster scenarios

| Scenario | Response |
|---|---|
| Vault pod is restarted | Operator with quorum of unseal keys re-unseals; backend re-logs-in via AppRole. No data loss. |
| Postgres restored from backup that is older than the last key rotation | Backup still decryptable because old key versions remain valid unless min_decryption_version was advanced past them. If it was, the older ciphertexts are unrecoverable by design — the rotation operator must communicate this SLA. |
| Vault volume is lost permanently | All provider secrets stored in Postgres become unrecoverable plaintext-less ciphertext. Operators must delete them and ask users to re-upload. This is the intended failure mode of envelope encryption — it trades recoverability for strong compromise containment. |
| Backend container leaks its `secret_id` | Revoke via `vault write -f auth/approle/role/smap-backend/secret-id-accessor/destroy accessor=…`, re-issue, redeploy. The leaked `secret_id` can only mint `smap-backend` tokens which cannot exfiltrate key material. |

---

## 7. Verification checklist

Run this on every deployment and after every rotation:

- [ ] `vault status` reports sealed=false, standby=false (or standby ok in HA).
- [ ] `vault policy read smap-backend` matches `policies/smap-backend.hcl`.
- [ ] `vault policy read smap-rotation` matches `policies/smap-rotation.hcl`.
- [ ] `vault read transit/keys/smap-provider-secret` shows `deletion_allowed = false, exportable = false`.
- [ ] Backend log contains `vault: authenticated as smap-backend, token ttl=…` on startup.
- [ ] A synthetic upload + outbound call on a throwaway project succeeds end-to-end.
- [ ] `vault token lookup` for the backend token shows `ttl` decreasing and `renewable = true`.
