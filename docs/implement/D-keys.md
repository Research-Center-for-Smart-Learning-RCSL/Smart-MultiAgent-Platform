# Phase D — API Key Management (BYO)

**Goal.** Deliver the full BYO-key subsystem per §7: envelope-encrypted `api_keys` (owned by Individuals), `key_projects` junction for "carried into project", Key Groups with **ordered priority** (1..N) + per-key rotation rules + per-key hourly limits, hourly sliding-window enforcement, 80% usage-threshold notifications, live validation probes on upload and retest, Vault Transit master rotation / rewrap operator tooling, and the BYO `search_keys` surface (one active per Project).

**Size.** L
**Depends on.** B (Vault Transit + HMAC + envelope lib), C (users, projects, audit).
**Unblocks.** E (LLM / embedding / rerank / search keys).
**Refs.** `REQUIREMENTS.md` §7 (all), §12.4 (search keys), §17, §18, §21.1 (keys / key_projects / key_groups / key_group_members / key_usage_events / search_keys), §22.4, §22.5, §22.9a.

## D.0 Scope summary

- An Individual uploads a provider key → backend live-validates via the provider's hard-coded probe → stores envelope-encrypted.
- A user "carries" a key into a Project; Project Owners build Key Groups from carried keys with explicit priority.
- Rotation per-group: `rotate_on_error_codes`, `rotate_on_token_quota`, `retry_on_error` with full backoff config.
- Hourly limits per key-in-group: input / output tokens + requests; 1-min Redis buckets × 60; 80% threshold → in-app notification.
- Exhaustion: quota-only queues up to `queue_wait_seconds` (default 60) then 503; errors-only exhausts retries then 503.
- `smap-rotation` operator rotates the Transit master; DEKs are `rewrap`-ed without the backend ever seeing plaintext DEK during the operation.
- Search keys: separate table, same envelope scheme; exactly one active per Project.

## D.1 Envelope encryption primitives — **CODE** — M

**Deliverables.**

- `smap/shared_kernel/security/envelope.py` (built on B.2 Vault client):
  - `encrypt(plaintext: bytes, aad: bytes) -> EnvelopeRecord(ciphertext, nonce, dek_wrapped, ciphertext_hmac, transit_key_version, hmac_key_version)`.
  - `decrypt(record, aad) -> bytes`.
- AES-256-GCM locally; fresh 96-bit nonce per write; HMAC-SHA256 over `(ciphertext || nonce || dek_wrapped || aad)` using KV-stored HMAC key (B.2).
- AAD bound to logical identity (e.g. `f"api_keys:{key_id}"` / `f"search_keys:{id}"`) to prevent cross-record DEK reuse.
- Plaintext DEK zeroised in memory immediately after use (R7.6 step 2).

**Key IDs.** `[R7.14]`–`[R7.15]`, §7.6.

**Exit criteria.** Round-trip; AAD-mismatch reject; HMAC tamper reject; unwrap after rotation without rewrap rejects cleanly.

## D.2 Provider enum + capability table — **CODE** — S

**Deliverables.**

- DB enum `api_key_provider = ('claude','openai','gemini','voyage','cohere')`.
- Python capability table (R7.01 — exact):

  | Provider | `llm_chat` | `embedding` | `rerank` |
  |---|:-:|:-:|:-:|
  | `claude` | ✓ | ✗ | ✗ |
  | `openai` | ✓ | ✓ | ✗ |
  | `gemini` | ✓ | ✓ | ✗ |
  | `voyage` | ✗ | ✓ | ✗ |
  | `cohere` | ✗ | ✗ | ✓ |

- Attach-time validation (app layer, 422 on mismatch):
  - Key Group accepts only `llm_chat`-capable keys (§7.4 note).
  - RAG embedding config accepts only `embedding`-capable.
  - RAG rerank config accepts only `rerank`-capable.

**Key IDs.** `[R7.01]`.

**Exit criteria.** Mismatch at Key Group add → `https://smap.local/problems/keys/capability-mismatch`.

## D.3 Validation probes — **CODE** — S

**Deliverables.** Per-provider hard-coded probe (R7.02):

| Provider | Probe |
|---|---|
| Anthropic | `POST /v1/messages` with a 1-token stub |
| OpenAI | `GET /v1/models` |
| Gemini | `GET /v1/models` |
| Voyage | `POST /v1/embeddings` with `input:["ping"]` |
| Cohere | `GET /v1/models` |

- Probe sets `test_status ∈ {ok, failed, untested}` + `test_error` on the row.
- Exposed via `POST /api/keys/{id}/retest` (§22.4).

**Key IDs.** `[R7.02]`, `[R7.03]`.

**Exit criteria.** Each provider's probe integration-tested with fixture responses.

## D.4 Individual keys schema & upload — **CODE** — M

**Deliverables.**

- Alembic revision `0005_api_keys`:
  ```
  api_keys (
    id uuid pk,
    owner_user_id fk users,                          -- always individual
    provider api_key_provider,
    name,                                            -- user label
    ciphertext bytea, nonce bytea, dek_wrapped bytea, ciphertext_hmac bytea,
    transit_key_version int, hmac_key_version int,
    masked_preview text,                             -- e.g. "sk-ant-...xE9a"
    test_status enum('ok','failed','untested'),
    test_error text null, last_test_at timestamptz null,
    created_at, deleted_at
  );
  CREATE INDEX ON api_keys (owner_user_id, provider);
  CREATE INDEX ON api_keys (provider);
  ```
- Endpoints (§22.4):
  - `GET /api/keys` (caller's; no secrets ever).
  - `POST /api/keys` `{provider, name, secret}` — runs probe immediately; on success stores enveloped + `test_status='ok'`; on failure stores with `test_status='failed'` + `test_error` (R7.3 §7.2 flow).
  - `POST /api/keys/{id}/retest`.
  - `DELETE /api/keys/{id}`.
- **Plaintext never returned** (R7.03 / R7.15). Response carries only `masked_preview` + `test_status`. There is **no** reveal endpoint.

**Key IDs.** `[R7.02]`–`[R7.03]`, `[R7.15]`, §22.4.

**Exit criteria.** Upload / retest / delete round-trip; no log line ever contains plaintext (grep-check CI).

## D.5 Keys carried into Project — **CODE** — M

**Deliverables.**

- Alembic revision `0006_key_projects`:
  ```
  key_projects (
    key_id fk api_keys, project_id fk projects,
    carried bool default true,
    added_by_user_id fk users, added_at,
    PRIMARY KEY (key_id, project_id)
  );
  ```
- Endpoints (§22.4):
  - `GET /api/projects/{pid}/keys` — carried keys (masked).
  - `POST /api/projects/{pid}/keys` `{key_id}` — carry a key.
  - `DELETE /api/projects/{pid}/keys/{key_id}` — withdraw.
  - `GET /api/projects/{pid}/keys/{key_id}/usage?window=1h|24h|7d|30d` — usage summary.
- **R7.04 cleanup**: when a user leaves / is removed from a Project, a worker revokes all `key_projects` rows with `key.owner_user_id = removed_user_id`; in-flight calls complete but no new calls issued (cache invalidation via pub/sub `key.carry_revoked`).
- Project Owner may view usage + rotation/limit settings on carried keys (R7.05) but **never** the secret.

**Key IDs.** `[R7.04]`–`[R7.05]`, §22.4.

**Exit criteria.** Membership-removal cascade verified; Project Owner cannot leak plaintext.

## D.6 Key Groups (ordered priority + per-key rotation + hourly limits) — **CODE** — L

**Deliverables.**

- Alembic revision `0007_key_groups`:
  ```
  key_groups (id uuid pk, project_id fk projects, name, created_at, deleted_at);
  key_group_members (
    group_id fk key_groups, key_id fk api_keys,
    priority int,                                  -- ordered, not round-robin (R7.06)
    rotate_on_error_codes int[],                   -- e.g. {429, 500, 502, 503}
    rotate_on_token_quota bool,
    retry_on_error bool,
    retry_initial_delay_ms int, retry_multiplier numeric,
    retry_max_delay_ms int, retry_max int, retry_jitter_pct int,
    max_input_tokens_per_hour bigint null,
    max_output_tokens_per_hour bigint null,
    max_requests_per_hour int null,                -- NULL = unlimited (R7.09)
    UNIQUE (group_id, key_id),
    UNIQUE (group_id, priority)
  );
  ```
- Endpoints (§22.5):
  - `GET /api/projects/{pid}/key-groups`, `POST /api/projects/{pid}/key-groups`.
  - `GET /api/key-groups/{id}` (masked members + priority order + limits + rotation).
  - `PATCH /api/key-groups/{id}` (group-level edits).
  - `DELETE /api/key-groups/{id}`.
  - `POST /api/key-groups/{id}/keys`, `PATCH /api/key-groups/{id}/keys/{key_id}`, `DELETE /api/key-groups/{id}/keys/{key_id}`.
  - `POST /api/key-groups/{id}/reorder` `{priorities:{key_id:int}}` — atomic bulk re-number.
- Defaults (R7.07): `retry_initial_delay_ms=500`, `retry_multiplier=2.0`, `retry_max_delay_ms=30000`, `retry_max=3`, `retry_jitter_pct=20`.
- Capability validation: Key Groups accept only `llm_chat`-capable keys (§7.4 rider).

**Key IDs.** `[R7.06]`–`[R7.07]`, §22.5.

**Exit criteria.** Reorder atomic; non-llm_chat key rejected at add time.

## D.7 Provider call router + rotation + exhaustion — **CODE** — L

**Deliverables.**

- `smap/contexts/keys/router.py::call(group_id, capability, request) -> response`:
  1. Acquire the next eligible member by **priority ascending** (R7.06 — ordered, not RR).
  2. Check hourly limits (D.8) via Redis bucket counters; if any `max_*_per_hour` exceeded, mark this member temporarily unavailable (reason `token_quota`).
  3. Unwrap DEK via envelope lib with AAD `api_keys:{key_id}`; inject into outbound request; HTTPS; zeroise plaintext.
  4. On provider error in `rotate_on_error_codes`: if `retry_on_error`, apply exp-backoff per config; on exhaust, advance to next priority. Accumulate error reason.
  5. Record `key_usage_events` (D.9) in all branches.
- Exhaustion rules (R7.08):
  - **All members unavailable due to quota only** → scheduler queues up to `queue_wait_seconds` (default 60) and retries at earliest refresh; still unavailable → 503 `key-group-exhausted`.
  - **All members unavailable due to API errors** → exp-backoff until `max_retries` on every member → 503 `key-group-exhausted`.
- Revocation fanout: on `api_keys` delete / `key_projects` withdraw, Redis pub/sub `key.revoked` / `key.carry_revoked` clears router in-memory DEK cache.

**Key IDs.** `[R7.06]`–`[R7.08]`, `[R7.15]`.

**Exit criteria.** Quota-queue path returns 503 after 60 s; error-exhaust path returns 503 with accurate audit `key.retry_exhausted`.

## D.8 Hourly sliding-window enforcement + 80% notification — **CODE** — M

**Deliverables.**

- Redis keys (R7.10): `keyuse:{key_id}:{minute_bucket}` TTL 61 min; consumer walks trailing 60 buckets for accurate hourly counters.
- Policy enforced pre-call (D.7 step 2).
- **80% threshold (R7.11)**: a worker samples active keys every 30 s; when any key exceeds 80% of any of its limits within the current hour, it emits an in-app notification (Phase I §I.3) to Project Members with usage-view permission (§5.2 capability 5). Audit event `key.usage_threshold_hit`. No email, no webhook.

**Key IDs.** `[R7.09]`–`[R7.11]`.

**Exit criteria.** Sub-bucket accuracy within ±2%; threshold event arrives within 30 s.

## D.9 Usage accounting — **CODE** — S

**Deliverables.**

- Alembic revision `0008_key_usage_events`:
  ```
  key_usage_events (
    id bigserial pk,
    key_id fk api_keys,
    agent_id uuid null,                              -- may reference agent_instances.id too
    parent_agent_id fk agents null,                  -- §15.23 sub-agent rollup
    chatroom_id fk chatrooms null,
    input_tokens int, output_tokens int,
    request_ms int, http_status int, error_code text null,
    at timestamptz
  );
  CREATE INDEX ON key_usage_events (key_id, at DESC);
  CREATE INDEX ON key_usage_events (agent_id, at DESC);
  -- monthly partitioning; > 13 months aggregated into key_usage_daily (retention in I4)
  ```
- `key_usage_daily` materialised view or table rebuilt nightly for older data (R7.13).
- Emitted by D.7 router on every outbound call; best-effort, never blocks user response.
- SMAP does **not** reconcile with provider bills (R7.14).

**Key IDs.** `[R7.12]`–`[R7.14]`, §21.1 keys usage.

**Exit criteria.** 100-call load test writes 100 rows; worker aggregates beyond 13 months correctly.

## D.10 Transit master rotation (operator tool) — **OPS + CODE** — M

**Deliverables.**

- `python -m smap.rotation rotate-transit` — uses **`smap-rotation`** AppRole (never backend):
  1. `vault write -f transit/keys/smap-provider-secret/rotate`.
  2. For every row in `api_keys`, `search_keys`, and any other envelope consumer: call `transit/rewrap/smap-provider-secret` to rewrap the `dek_wrapped` (plaintext DEK never leaves Vault).
  3. Update `transit_key_version`; checkpoint progress in a `rewrap_progress` table for resumability.
- HMAC key rotation: new version added; old retained during grace window; rows re-signed opportunistically.
- Cadence suggested quarterly (`deploy/vault/README.md`).

**Key IDs.** §7.6, `deploy/vault/README.md`.

**Exit criteria.** Rotate → normal D.7 calls continue without interruption; kill CLI mid-run → resume from checkpoint.

## D.11 Search keys (BYO) — **CODE** — M

**Deliverables.**

- Alembic revision `0009_search_keys`:
  ```
  search_keys (
    id uuid pk, project_id fk projects,
    provider enum('brave','serper','tavily','google_cse'),
    ciphertext bytea, nonce bytea, dek_wrapped bytea, ciphertext_hmac bytea,
    transit_key_version int, hmac_key_version int,
    masked_preview text,
    test_status enum('ok','failed','untested'), test_error text null,
    last_test_at timestamptz null,
    is_active bool,
    config jsonb,                                 -- google_cse.cx, tavily.search_depth, etc.
    created_at, deleted_at,
    UNIQUE (project_id) WHERE is_active AND deleted_at IS NULL
  );
  ```
- Endpoints (§22.9a):
  - `GET /api/projects/{pid}/search-keys` (masked).
  - `POST /api/projects/{pid}/search-keys` `{provider, secret, config}` — live test.
  - `POST /api/projects/{pid}/search-keys/{id}/retest`.
  - `POST /api/projects/{pid}/search-keys/{id}/activate` (atomically deactivates others).
  - `DELETE /api/projects/{pid}/search-keys/{id}`.
- Envelope encryption with AAD `search_keys:{id}`.
- Tests mirror D.3 probes per provider.
- Activation flip invalidates the Redis `search:{hash}` cache scope for the project (§12.4 / Phase E).
- Audit: `search_key.uploaded/test_success/test_failed/activated/deactivated/deleted` (§17.1 category).

**Key IDs.** §12.4 (`[R12.07]`–`[R12.10]`), §22.9a.

**Exit criteria.** Partial-unique active constraint enforced; activate/deactivate atomic.

## D.12 Frontend keys slice — **CODE** — M

**Objective.** `slices/keys/` (per §24.2) covers `api_keys`, `key_groups`, `search_keys`.

**Deliverables.**

- Views: `KeyListView`, `KeyDetailView`, `KeyGroupListView`, `KeyGroupDetailView`, `SearchKeyView`.
- Composables: `useMyKeys`, `useProjectKeys`, `useKeyGroups`, `useSearchKeys`.
- Forms with vee-validate + Zod; plaintext secret entered over HTTPS, never logged.
- Drag-reorder inside Key Group with optimistic update + server confirm.
- Capability chip shown next to provider (`llm_chat` / `embedding` / `rerank`).

**Key IDs.** §24.2, §22.4, §22.5, §22.9a.

**Exit criteria.** Playwright: upload → carry → group build → reorder → retest → withdraw → delete.

## D.∞ Phase gate

- [x] cohere capability row matches R7.01 exactly (rerank only).
- [x] Ordered priority only; no round-robin / least-loaded.
- [x] `rotate_on_error_codes / rotate_on_token_quota / retry_on_error` and all 5 backoff defaults.
- [x] Per-key hourly limits enforced via 1-min × 60 sliding window.
- [x] 80% threshold notification arrives within 30 s.
- [x] Queue-on-quota-only behaviour (default 60 s) vs error-exhaust behaviour correct.
- [x] `key_projects` + revocation fanout on membership loss.
- [x] Transit rotation via `smap-rotation` AppRole; backend never sees plaintext DEK during rewrap.
- [x] `search_keys` partial-unique-active enforced.
- [x] No plaintext secret visible in any log (CI grep gate).
- [ ] `00-overview.md` §0.8: D = done.

## Cross-cutting checklist

1. **AuthZ tap.** §5.2 capabilities 2–6 on keys; capability-mismatch path returns 422.
2. **Audit tap.** `key.uploaded/test_success/test_failed/deleted/carried_into_project/withdrawn_from_project/usage_threshold_hit/rotation_triggered/retry_exhausted`; `search_key.*`.
3. **Rate limit bucket.** `keys-write`, `keys-read` (within the `other` 300/min/user default); operator rotation runs off-cluster.
4. **Observability.** `provider_call_total{provider,status}`, `key_group_exhausted_total{reason}`, `envelope_decrypt_failures_total`, `usage_threshold_events_total`.
5. **RFC 7807.** `https://smap.local/problems/{keys/capability-mismatch, keys/group-exhausted, keys/provider-unauthorized, keys/revoked, keys/usage-quota-exceeded, search/activation-conflict}`.
6. **Migration policy.** `0005_api_keys`, `0006_key_projects`, `0007_key_groups`, `0008_key_usage_events`, `0009_search_keys`, all N-1 compatible.
7. **Secrets.** Every secret stored enveloped; Transit/HMAC versions tracked.

## Risks

- **Operator key rotation race.** `rewrap_progress` checkpoints + idempotent rewrap let the CLI safely resume.
- **Hourly bucket aggregator load.** Lua script aggregates 60 buckets in one Redis round trip.
- **Missing search key mid-agent-call.** Returns `tool_unavailable: search_key_not_configured` to the agent (§12.4 / E.11).
- **User leaves project with active outbound calls.** R7.04 lets in-flight calls finish; new ones blocked immediately.
