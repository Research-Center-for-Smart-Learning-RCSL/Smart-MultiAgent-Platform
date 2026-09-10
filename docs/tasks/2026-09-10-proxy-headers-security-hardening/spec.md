---
type: bugfix
status: draft
created: 2026-09-10
requirements: [R7.16]
depends_on: []
---

# proxy_headers bypasses Vault encryption and allows header injection

## 1. Summary

The `proxy_headers` field added to `OpenAICompatConfig` has three security defects:
(a) it can silently overwrite the Vault-decrypted `Authorization` header, letting a
user smuggle arbitrary credentials past the envelope-encryption audit trail; (b) it
accepts arbitrary header names and values with no blocklist, size limit or CRLF check,
violating the project's API-boundary validation rule; and (c) it stores potentially
secret values (proxy auth tokens) in plaintext JSONB, contradicting `[R7.16]`'s
assertion that the config column "contains no secrets."

## 2. Observed vs Expected

- **Observed** -- `_headers()` at `adapters/openai_compat.py:34-38` sets the real
  `Authorization` bearer from the Vault-decrypted secret, then calls
  `h.update(proxy_headers)`, which overwrites `Authorization` if the user supplied it.
  The same pattern appears in `probes/openai_compat.py:33-35`. The Pydantic model at
  `openai_compat_config.py:20-23` accepts `dict[str, str] | None` with no field
  validators -- every other field in the model has explicit constraints. The value
  lands in `api_keys.config` JSONB in cleartext while `api_keys.secret` is
  envelope-encrypted via Vault Transit.

- **Expected** -- `[R7.16]` (`REQUIREMENTS.md:361`) states the config column "contains
  no secrets." The CLAUDE.md security constraints require: "All user input must be
  validated at the API boundary (Pydantic models)" and "Provider API keys are
  envelope-encrypted via Vault Transit -- never stored in plaintext." A proxy auth
  header is functionally equivalent to a credential and should be treated as one.
  The `Authorization` and other protocol headers should be protected from override.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should proxy_headers values be Vault-encrypted like the API key secret? | **Yes** -- encrypt the entire proxy_headers dict via the same Vault Transit envelope as the key secret, or store it in a dedicated encrypted column. | A `Proxy-Authorization` bearer token is equivalent to a credential. Storing it in cleartext next to an encrypted secret is inconsistent and violates the stated invariant. |
| Q-2 | Which headers should be blocklisted? | **Authorization, Content-Type, Content-Length, Host, Transfer-Encoding, Connection, Upgrade, Proxy-Authorization** (case-insensitive). | `Authorization` and `Content-Type` are set by the adapter. The rest are HTTP/1.1 hop-by-hop or framing headers whose override enables request smuggling. `Proxy-Authorization` is blocked because the adapter is the one deciding how to authenticate to the proxy -- user-supplied proxy auth should go through the same encrypted path as the key secret. |
| Q-3 | What size/count limits should proxy_headers have? | **Max 20 entries, header name max 128 chars, header value max 4096 chars.** | Prevents JSONB bloat. Real-world proxy auth needs 1-3 headers; 20 is generous. |

## 4. Reproduction

1. Upload an `openai_compat` key with config:
   ```json
   {
     "base_url": "https://proxy.example.com",
     "proxy_headers": {"Authorization": "Bearer attacker-token"}
   }
   ```
2. The probe at `probes/openai_compat.py:33-35` builds
   `headers = {"Authorization": f"Bearer {secret}"}` then `headers.update(proxy_headers)`.
   The Vault-decrypted secret is overwritten. The probe authenticates with
   `attacker-token`.
3. If the probe succeeds (HTTP 200), the key is stored as healthy. All subsequent LLM
   calls via `adapters/openai_compat.py:34-38` use the overridden `Authorization`.
4. The audit trail records usage against the original encrypted key, but the actual
   credential used is the unencrypted one from `proxy_headers`.

## 5. Root Cause Analysis

**Root cause**: `proxy_headers` was added to `OpenAICompatConfig`
(`openai_compat_config.py:20-23`) as a bare `dict[str, str] | None` with no field
validators, no encryption path, and no blocklist. The `_headers()` helper
(`adapters/openai_compat.py:34-38`) uses `dict.update()` unconditionally, which
overwrites existing keys.

**Aggravating factors**:
- `[R7.16]` (`REQUIREMENTS.md:361`) was not updated when `proxy_headers` was added,
  leaving the "contains no secrets" assertion false.
- The SRS field list for `openai_compat` config (`REQUIREMENTS.md:285`) does not
  mention `proxy_headers`, so the feature shipped without SRS coverage.

## 6. Blast Radius and Sibling Suspects

**Blast radius:**
- Any `openai_compat` key with `proxy_headers` containing `Authorization` silently
  uses the wrong credential for all LLM calls and probes.
- Proxy auth tokens in `proxy_headers` are readable by anyone with database access
  (backup, SQL injection, admin queries).
- No existing keys are affected unless a user has already stored `Authorization` in
  `proxy_headers` (unlikely but unverifiable without a DB scan).

**Sibling suspects:**
- `adapters/openai.py`, `adapters/anthropic.py`, `adapters/gemini.py` -- **cleared**;
  none accept `proxy_headers` or user-controlled headers.
- `probes/openai.py`, `probes/anthropic.py`, `probes/gemini.py` -- **cleared**; same
  reason.
- Frontend `buildProxyHeaders()` (`KeyUploadForm.vue:92-96`) -- **confirmed** related
  defect: silently drops headers with empty values (valid HTTP), uses array index as
  `v-for` key (`:key="idx"` at line 304), and has triplicated reset logic.

## 7. Fix Design

### Backend

1. **Blocklist in Pydantic validator** (`openai_compat_config.py`): Add a
   `field_validator("proxy_headers")` that rejects any header name (case-insensitive)
   in the blocklist: `authorization`, `content-type`, `content-length`, `host`,
   `transfer-encoding`, `connection`, `upgrade`, `proxy-authorization`. Also enforce:
   max 20 entries, name max 128 chars, value max 4096 chars, no CRLF in names or
   values.

2. **Encryption**: Move `proxy_headers` from the `config` JSONB column to the
   encrypted envelope. Two options:
   - **(a)** Serialize `proxy_headers` alongside the key secret in the encrypted
     payload (extend the envelope to carry structured data, not just a string).
   - **(b)** Add a dedicated `encrypted_config` column using the same Vault Transit
     envelope, storing the entire `proxy_headers` dict there.

   Option (b) is less invasive -- it does not change the secret envelope schema, and
   `proxy_headers` can be decrypted independently when building request headers.

3. **Header merge order** (`adapters/openai_compat.py`, `probes/openai_compat.py`):
   Even with the blocklist, defensively set `Authorization` and `Content-Type` **after**
   `proxy_headers`, not before. This makes the invariant structural rather than relying
   on the blocklist alone.

4. **Migration**: Encrypt existing `proxy_headers` values from `config` JSONB into the
   new encrypted column, then remove the key from `config`. Reversible: the migration
   down path copies from encrypted to plaintext (acceptable for a rollback window).

### Frontend

5. **v-for key** (`KeyUploadForm.vue:304`): Replace `:key="idx"` with a stable unique
   id per entry (e.g., `crypto.randomUUID()` assigned at creation).

6. **Empty-value handling** (`KeyUploadForm.vue:93`): Change the filter to only require
   `e.name.trim()`, allowing empty values (valid HTTP).

7. **Reset deduplication**: Extract the seven-ref reset into a single `resetConfigRefs()`
   function called from all three sites.

## 8. Regression Test Plan

**Failing tests (before fix):**

- `tests/contexts/keys/test_proxy_header_blocklist.py`:
  - `test_authorization_override_rejected` -- upload a key with
    `proxy_headers: {"Authorization": "..."}`, expect 422.
  - `test_crlf_in_header_name_rejected` -- upload with `"X-Foo\r\nEvil: bar"`, expect
    422.
  - `test_max_entries_exceeded` -- upload with 21 entries, expect 422.

- `tests/contexts/keys/test_proxy_header_encryption.py`:
  - `test_proxy_headers_not_in_config_json` -- after upload, query `api_keys.config`
    directly and assert `proxy_headers` key is absent.
  - `test_proxy_headers_decrypted_for_request` -- mock the adapter call and assert the
    decrypted headers appear in the outbound request.

- `tests/contexts/keys/test_proxy_header_merge_order.py`:
  - `test_authorization_survives_proxy_headers` -- adapter `_headers()` with
    `proxy_headers: {"Authorization": "wrong"}` (should this pass the blocklist, the
    merge order still protects the real token). Belt-and-suspenders test.

## 9. Risks and Rollback

- **Migration risk**: Encrypting existing `proxy_headers` requires Vault to be
  unsealed. If Vault is sealed during migration, the migration should skip encryption
  and log a warning; a subsequent `smap maintenance` command retries.
- **Breaking change**: Existing keys with blocklisted header names in `proxy_headers`
  will fail validation on next edit. Since `Authorization` override is itself a defect,
  this is the desired outcome.
- **Rollback**: Downgrade migration copies encrypted headers back to plaintext `config`
  JSONB. The blocklist validator is removed by the code rollback.

## 10. Acceptance Criteria

- [ ] AC-1: Regression tests from S8 fail before the fix and pass after.
- [ ] AC-2: Uploading or editing a key with `proxy_headers` containing a blocklisted
  header name (case-insensitive) returns HTTP 422.
- [ ] AC-3: `proxy_headers` values are stored encrypted (not in the `config` JSONB
  column in plaintext). Verified by direct DB query.
- [ ] AC-4: The adapter's outbound `Authorization` header always uses the
  Vault-decrypted secret, regardless of `proxy_headers` content.
- [ ] AC-5: `proxy_headers` entries are limited to 20, with name max 128 chars and
  value max 4096 chars. CRLF in names or values is rejected with 422.
- [ ] AC-6: The frontend allows empty header values (does not silently drop them).
- [ ] AC-7: The frontend `v-for` uses a stable unique key per entry, not the array
  index.
- [ ] AC-8: The frontend reset logic is a single function called from all reset sites.
- [ ] AC-9: `[R7.16]` in `REQUIREMENTS.md` is updated to list `proxy_headers` and
  state that it is encrypted.

## 11. SRS Delta

Amend `[R7.16]` (`REQUIREMENTS.md:361`):

Current:
```
**[R7.16]** The `api_keys` table carries a `config` JSONB column (default `'{}'`).
For `openai_compat` keys, this column stores the validated provider configuration
(`base_url`, `label`, `timeout_s`, `capabilities`). For all other providers, it is
empty (`{}`). The config is validated at upload time against a strict schema; invalid
config is rejected with 422. The config is not encrypted (it contains no secrets).
```

Amended:
```
**[R7.16]** The `api_keys` table carries a `config` JSONB column (default `'{}'`).
For `openai_compat` keys, this column stores the validated provider configuration
(`base_url`, `label`, `timeout_s`, `capabilities`). For all other providers, it is
empty (`{}`). The config is validated at upload time against a strict schema; invalid
config is rejected with 422. The config is not encrypted (it contains no secrets).
`openai_compat` keys may additionally carry `proxy_headers` (extra HTTP headers merged
into every outbound request): these are stored encrypted via the same Vault Transit
envelope as the key secret, not in the plaintext `config` column. Header names are
validated against a blocklist (Authorization, Content-Type, Content-Length, Host,
Transfer-Encoding, Connection, Upgrade, Proxy-Authorization) and constrained to 20
entries with name max 128 and value max 4096 characters; CRLF in names or values is
rejected.
```

Also amend `openai_compat` description (`REQUIREMENTS.md:285`) to add `proxy_headers`
to the config field list:

Current (relevant excerpt):
```
The `config` object carries `base_url` (required), `label` (optional display name),
`timeout_s` (optional HTTP timeout in seconds, default 120, max 3600), and
`capabilities` (optional subset of `["llm_chat", "embedding"]`).
```

Amended:
```
The `config` object carries `base_url` (required), `label` (optional display name),
`timeout_s` (optional HTTP timeout in seconds, default 120, max 3600), and
`capabilities` (optional subset of `["llm_chat", "embedding"]`). An optional
`proxy_headers` dict supplies extra HTTP headers merged into every outbound request
(e.g. proxy authentication); these are stored encrypted and validated against a
blocklist of protocol-sensitive header names.
```

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- FU-1: Audit existing `openai_compat` keys in production for any `proxy_headers`
  containing blocklisted header names. Requires a one-off DB scan.
- FU-2: Consider adding a `proxy_headers` UI to the key edit form (currently only
  available on upload).
