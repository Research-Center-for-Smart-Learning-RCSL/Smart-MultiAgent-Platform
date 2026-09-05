---
type: feature
status: in-progress
created: 2026-09-05
requirements: [R7.01, R7.02, R7.03, R9.03a]
depends_on: []
---

# OpenAI-Compatible Generic Provider

## 1. Summary

Add `openai_compat` to `ApiKeyProvider` so users can connect any
OpenAI-Chat-Completions-compatible gateway (RCSL AI Nexus, Ollama, vLLM, LiteLLM,
Azure OpenAI, etc.) without a per-vendor enum value or adapter. A new `config` JSONB
column on `api_keys` carries per-key settings (base URL, timeout, display label,
declared capabilities). One adapter handles all such endpoints using the
`/v1/chat/completions` wire protocol.

## 2. Goals and Non-goals

**Goals**

- Any endpoint serving OpenAI Chat Completions can be connected without code changes.
- Per-key configuration: base URL (required), timeout, display label, capability set.
- Full LLM chat support: streaming, tool calling, effort/sampling/seed forwarding
  (gated by per-model capability flags, same as the existing three chat providers).
- Embedding support for gateways that offer the `/v1/embeddings` endpoint.
- Existing five providers are entirely unaffected; the config column defaults to `'{}'`
  and no existing code path reads it.

**Non-goals**

- No `/v1/responses` wire support. Chat Completions is the universally supported
  protocol; the existing OpenAI adapter already covers Responses for `api.openai.com`.
- No rerank support. There is no standard OpenAI-compatible rerank API.
- No vendor-specific extensions (Nexus `use_knowledge`/`think`/`prompt_template`,
  Ollama `keep_alive`, etc.). These are pass-through only if the gateway ignores
  unknown fields; the adapter never sends them.
- No automatic model discovery or catalog integration. Model names are free-text in
  the agent config form. A future follow-up could add discovery via
  `GET {base_url}/models`.
- No config migration from existing providers. A key uploaded as `openai` stays
  `openai`; the user creates a new `openai_compat` key to use a different gateway.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Wire protocol: Chat Completions or Responses API? | Chat Completions (`/v1/chat/completions`). | Responses is OpenAI-specific. Every other compatible gateway (Ollama, vLLM, LiteLLM, Nexus, Azure) serves Chat Completions. The existing `OpenAIAdapter` already covers Responses for `api.openai.com`, so there is no coverage gap. |
| Q-2 | Config storage: per-key column or separate table? | `config JSONB` column on `api_keys`, defaulting to `'{}'::jsonb`. | Simpler join-free reads. `search_keys` already uses this pattern (`tables.py:206`). Existing providers always store `{}`, so no migration data fixup is needed. |
| Q-3 | Capability narrowing: static or dynamic? | Default `{LLM_CHAT, EMBEDDING}` in the static `_CAPABILITIES` table. Dynamic narrowing at attach time: `assert_capability` for `OPENAI_COMPAT` additionally checks `config.capabilities` when present. | Static-only would let a user attach a chat-only key to an embedding slot, discovering the error only at call time. Dynamic narrowing is a two-line change in `assert_capability`. |
| Q-4 | Probe signature: widen for all providers, or openai_compat-only? | The openai_compat probe takes `(secret, base_url)`. The dispatch function `probe()` gains an optional `config` parameter; for `OPENAI_COMPAT` it extracts `base_url` and passes it, for all others it is ignored. | Minimal change to existing probes. No existing probe's signature changes. The dispatch function changes from `probe(provider, secret)` to `probe(provider, secret, config=None)`. |
| Q-5 | Model catalog: catalog entries, auto-discovery, or free-text? | Free-text only. No `ChatModelSpec` rows for `openai_compat`. The agent config form shows a text input instead of a dropdown when `model_hint` is `openai_compat`. | The adapter cannot know what models an arbitrary gateway offers. Auto-discovery (via `GET /v1/models`) is deferred as FU-1. A free-text field already exists in the agent form (the "Custom" option at `AgentDetailView.vue:333-342`). |
| Q-6 | SSRF protection on base_url? | Validate at upload time: must be `https://` (or `http://` only when `SMAP_ALLOW_HTTP_PROVIDERS=true` for local dev). Reject URLs resolving to private/link-local/loopback IPs. | The probe and adapter make outbound HTTP calls to user-supplied URLs. Without validation, a user could probe internal services. The DNS resolution check mirrors `egress_proxy`'s existing SSRF guard. |
| Q-7 | Provider config threading to the adapter? | `ProviderRequest` gains `provider_config: dict[str, Any] | None = None`. The router injects `em.key.config` via `dataclasses.replace()` in all three call paths (`_call_member`, `_stream_member`, `call_single_key`/`call_single_key_stream`). | The adapter protocol stays `(secret, request)`. Only the openai_compat adapter reads `provider_config`; others ignore it. No change to the protocol itself. |
| Q-8 | `model_hint` DB enum widening? | The Alembic migration also adds `'openai_compat'` to `agent_model_hint`. | `model_hint` is a PG ENUM (`agents/infrastructure/tables.py:26-27`), not a string. Without widening, an agent cannot be configured for this provider. |
| Q-9 | `tool_choice` handling? | Send `"auto"` only. Never send `"required"` or a named function, even if the payload requests it. Log a warning if downgraded. | Many compatible gateways (Nexus included) refuse `tool_choice: required`. Silently downgrading is safer than failing the call. The three existing adapters also send only `"auto"` or `"none"`. |
| Q-10 | Config schema validation? | Pydantic model `OpenAICompatConfig` validated at upload time. Fields: `base_url: HttpUrl` (required), `label: str` (optional, max 100, default `"OpenAI Compatible"`), `timeout_s: int` (optional, 10..3600, default 120), `capabilities: list[ProviderCapability]` (optional, default `["llm_chat", "embedding"]`). | Catches invalid config at upload rather than at first call. Follows the project's "validate at API boundary" rule. |
| Q-11 | Dependencies on active dossiers? | None. The board scan found two active dossiers (`graphrag-two-axis-redesign`, `large-artifacts-silently-dropped`), neither touching keys, adapters, probes, or model_specs. | Verified by grepping all active dossiers' touched files against this feature's file list. |

## 4. Current State

### 4.1 Provider enum and capabilities

`ApiKeyProvider` (`contexts/keys/domain/providers.py:28-35`) is a five-member `str, Enum`:
`CLAUDE`, `OPENAI`, `GEMINI`, `VOYAGE`, `COHERE`. The capability matrix
(`providers.py:47-53`) maps each to a frozen set of `ProviderCapability` values.
`assert_capability()` (`:77-84`) checks `supports(provider, requirement.capability)`,
which reads only the static table -- no per-key data.

### 4.2 Key upload flow

`POST /api/keys` accepts `{provider, name, secret}` (`app/api/v1/keys.py:38-43`).
`KeyService.upload()` (`key_service.py:75-161`) calls `probe(provider, secret)`,
envelope-encrypts the secret, persists the row via `ApiKeyRepository.insert()`, and
emits audit events. The flow is fully provider-agnostic -- no switch/case anywhere. The
`api_keys` table (`tables.py:17-61`) has no `config` column.

### 4.3 Probes

Each probe is `async def probe_<name>(secret: str) -> ProbeResult`
(`probes/<name>.py`). The dispatch table (`probes/__init__.py:25-31`) maps
`ApiKeyProvider` to a callable. Base URLs come from `ProviderProbeSection` in
`settings.py:386-410` (one `<name>_base_url` field per provider, overridable via env
var). The probes use `probe_url()` (`probes/base.py:77-91`) to resolve the URL from
settings.

### 4.4 Adapter registry

Five adapters in `contexts/keys/infrastructure/adapters/`, one per provider.
`build_adapters()` (`__init__.py:36-44`) returns the map. Each adapter implements the
`ProviderAdapter` protocol and (for chat providers) `StreamingAdapter`
(`provider_router.py:102-161`). The adapter receives `(secret, request)` and returns
`ProviderCallResult`; streaming yields `TokenDelta` events followed by `StreamComplete`.

The OpenAI adapter (`openai.py`) uses `/v1/responses` (not Chat Completions). It builds
Responses-specific `input_items`, handles encrypted reasoning item passthrough
(`provider_items`), and parses `response.output` items. The wire format is incompatible
with Chat Completions.

### 4.5 Router provider_config threading

`ProviderRequest` (`provider_router.py:76-91`) has no `provider_config` field.
The router's `_call_member()` (`:852-895`) and `_stream_member()` (`:504-599`) call the
adapter with the request as received. `call_single_key()` (`:640-703`) and
`call_single_key_stream()` (`:705-786`) do the same.

### 4.6 Model catalog and agent form

`AgentModelHint` (`agents/domain/models.py:18-21`) is a three-member `str, Enum`:
`CLAUDE`, `OPENAI`, `GEMINI`. Stored in PG ENUM `agent_model_hint`
(`agents/infrastructure/tables.py:26-27`).

`CHAT_MODEL_SPECS` (`agents/domain/model_specs.py:101-273`) is a static tuple of 11
`ChatModelSpec` rows across three providers. `DEFAULT_MODEL_IDS` (`:275-279`) maps
provider strings to default model IDs. The frontend fetches `GET /api/model-catalog`
and renders a per-provider dropdown with a "Custom" free-text fallback
(`AgentDetailView.vue:333-342`).

### 4.7 Frontend key management

`ApiKeyProvider` in `keys/api/keys.ts:4` is a five-member union type. `CAPABILITIES`
(`:34-40`) mirrors the backend. `KeyUploadForm.vue` renders a provider dropdown
(`:26-31`), a name field, and a secret field -- no provider-specific conditional
rendering. The Zod schema hardcodes the enum (`:37-43`).

`CapabilityChip.vue:6-12` has a static `DISPLAY_NAMES` map.

## 5. Design

### Options considered

**Option A -- Per-vendor enum**: Add `NEXUS = "nexus"` (or `OLLAMA`, `VLLM`, etc.) as
dedicated providers, each with its own adapter, probe, and model specs.

Trade-offs: Tight control per vendor. But every new gateway requires a code change,
enum migration, adapter, probe, and frontend additions. This scales poorly; the user
explicitly rejected it.

**Option B -- Generic `openai_compat` with per-key config**: One new enum value, one
new adapter, per-key `config` JSONB for base URL and other settings.

Trade-offs: One-time work covers all compatible gateways. Per-key config is flexible.
The adapter cannot optimize for vendor-specific features, but the common denominator
(Chat Completions with tools and streaming) covers the vast majority of use cases.

### Decision

Option B. The user chose this direction before analysis began (pre-conversation design
review). The codebase analysis confirms it is feasible: the upload flow, router, and
adapter registry are all provider-agnostic, and the only per-provider coupling points
(enum, capability table, probe dispatch, adapter map, model specs) are all extensible.

The Chat Completions wire protocol is the right choice because:
1. It is the de facto standard for compatible gateways.
2. The Responses API is OpenAI-specific and already covered by the existing adapter.
3. The old OpenAI adapter code (pre-migration to Responses) was deleted, but Chat
   Completions is simpler than Responses (no input_items, no response.output, no
   encrypted reasoning items, direct message-based conversations).

## 6. Detailed Changes

### Backend -- Domain (`contexts/keys/domain/`)

- **`providers.py`**: Add `OPENAI_COMPAT = "openai_compat"` to `ApiKeyProvider` (after
  `COHERE`). Add `_CAPABILITIES[ApiKeyProvider.OPENAI_COMPAT] = frozenset({LLM_CHAT,
  EMBEDDING})`. Modify `assert_capability()` to check `config.capabilities` when
  `provider is OPENAI_COMPAT` and config is provided.

- **`models.py`**: Add `config: dict[str, Any]` field to `ApiKey` dataclass (default
  `{}`). Import `Any` from `typing`.

- **`errors.py`**: Add `InvalidProviderConfig` error class for config validation
  failures.

### Backend -- Application (`contexts/keys/application/`)

- **`key_service.py`**: `upload()` gains optional `config: dict[str, Any] | None`
  parameter. For `OPENAI_COMPAT`, validate config against `OpenAICompatConfig` Pydantic
  model; raise `InvalidProviderConfig` on failure. Pass `config` to `probe()` and to
  `self._repo.insert()`. For other providers, `config` stays `{}`.

- **`provider_router.py`**: Add `provider_config: dict[str, Any] | None = None` to
  `ProviderRequest`. In `_call_member()`, `_stream_member()`, `call_single_key()`, and
  `call_single_key_stream()`, inject `em.key.config` (or `key.config`) into the request
  via `dataclasses.replace()` before calling the adapter.

### Backend -- Infrastructure (`contexts/keys/infrastructure/`)

- **`tables.py`**: Add `config` column to `api_keys`:
  `sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))`.
  Add `"openai_compat"` to the `pg.ENUM(...)` list for the table definition.

- **`repositories.py`**: Thread `config` through `insert()` and `_row_to_api_key()`.

- **`adapters/openai_compat.py`** (new): `OpenAICompatAdapter` implementing both
  `ProviderAdapter` and `StreamingAdapter`. Uses Chat Completions wire protocol
  (`/v1/chat/completions`). Reads `base_url` and `timeout_s` from
  `request.provider_config`. Key implementation points:
  - Message format: standard `messages` array with `role`/`content`/`tool_calls`
  - Tool calls: OpenAI Chat Completions tool format (arguments as JSON string)
  - Streaming: SSE with `data:` lines, `delta.content`/`delta.tool_calls`,
    `[DONE]` sentinel -- reuse `base.iter_sse_lines()`
  - Token counting: from `usage.prompt_tokens`/`usage.completion_tokens`
  - Capability flags: honor `accepts_effort`, `accepts_sampling`, `accepts_seed`,
    `accepts_vision` from payload (same `base.capability_flags()` as other adapters)
  - Error scrubbing: reuse `base.scrub_error()` / `base.scrub_stream_error()`
  - Model resolution: reuse `base.resolve_model()`
  - No `provider_items` passthrough (no encrypted reasoning items on Chat Completions)

- **`adapters/__init__.py`**: Import `OpenAICompatAdapter`, add to `build_adapters()`.

- **`probes/openai_compat.py`** (new): `async def probe_openai_compat(secret: str, *,
  base_url: str) -> ProbeResult`. Calls `GET {base_url}/models` with bearer auth.
  Validates `base_url` against SSRF rules before connecting.

- **`probes/__init__.py`**: Change `probe()` signature to
  `probe(provider, secret, config=None)`. For `OPENAI_COMPAT`, extract `base_url` from
  config and call `probe_openai_compat(secret, base_url=base_url)`. For others, ignore
  config and dispatch as before.

- **`probes/base.py`**: Add `validate_base_url(url: str) -> str` that rejects
  private/loopback/link-local IPs and non-HTTPS schemes (unless
  `SMAP_ALLOW_HTTP_PROVIDERS` is set). DNS resolution check at validation time.

### Backend -- Agents context

- **`agents/domain/models.py`**: Add `OPENAI_COMPAT = "openai_compat"` to
  `AgentModelHint`.

- **`agents/domain/model_specs.py`**: No `ChatModelSpec` rows for `openai_compat`. Add
  `"openai_compat"` to `DEFAULT_MODEL_IDS` with value `""` (empty string -- no
  default). `resolve_spec("openai_compat", model_id)` returns the conservative Q-2
  floor (all optional capabilities off, 8192 context limit).

- **`agents/application/agent_service.py`**: No changes needed.
  `_assert_key_group_has_provider()` already takes `model_hint: str` and calls
  `has_carried_provider_in_group()`.

### Backend -- API layer

- **`app/api/v1/keys.py`**: Add optional `config: dict[str, Any] | None = None` to
  `KeyUploadIn`. Pass it through to `KeyService.upload()`. Add `config: dict[str, Any]`
  to `KeyOut`.

- **`app/api/v1/model_catalog.py`**: No changes. `chat_model_catalog()` iterates
  `specs_for_provider()` per provider; `openai_compat` has no specs, so it returns no
  catalog entry. The frontend handles this by showing free-text only.

### Backend -- Migration

New Alembic migration:
1. `ALTER TYPE api_key_provider ADD VALUE IF NOT EXISTS 'openai_compat'` (outside
   transaction -- PG ENUM ADD VALUE is non-transactional).
2. `ALTER TYPE agent_model_hint ADD VALUE IF NOT EXISTS 'openai_compat'` (same).
3. `ALTER TABLE api_keys ADD COLUMN config jsonb NOT NULL DEFAULT '{}'::jsonb`.
4. Downgrade: drop the `config` column. ENUM values cannot be removed in PostgreSQL;
   leave them in place (standard practice in this project).

### API contract

`POST /api/keys` request body gains optional `config` field.
`KeyOut` response gains `config` field.
`gen:api` rerun required: yes.

### Frontend -- `slices/keys/`

- **`api/keys.ts`**: Add `'openai_compat'` to `ApiKeyProvider` union. Add
  `openai_compat: ['llm_chat', 'embedding']` to `CAPABILITIES`. Add `OpenAICompatConfig`
  TypeScript interface.

- **`components/KeyUploadForm.vue`**: Add `'openai_compat'` to Zod enum. When
  `provider === 'openai_compat'`, render additional config fields: base URL (required
  text input), label (optional text input), timeout (optional number input), capability
  checkboxes. Follow the pattern in `SearchKeyUploadForm.vue:99-119` (conditional
  fields per provider).

- **`components/CapabilityChip.vue`**: Add `openai_compat: 'Custom'` (or read from key
  config label) to `DISPLAY_NAMES`.

- **`__tests__/capabilities.test.ts`**: Add `openai_compat` to expected table.

- **`locales/en.json` + `zh-TW.json`**: Add translation keys for
  `keys.form.baseUrl`, `keys.form.baseUrlPlaceholder`, `keys.form.label`,
  `keys.form.timeout`, `keys.form.capabilities`, `keys.providers.openai_compat`.

### Frontend -- `slices/agents/`

- **`types/schemas.ts`**: Add `'openai_compat'` to `model_hint` Zod enum.

- **`views/AgentDetailView.vue`**: Add `openai_compat` to `modelHintOptions`
  (`:755-759`). When `modelHint === 'openai_compat'` and the catalog has no entry for
  this provider, skip the model dropdown and show the free-text custom model input
  directly (`customModel` field, already implemented at `:943-950`).

- **`locales/en.json` + `zh-TW.json`**: Add
  `agents.form.modelHints.openai_compat`.

### Deploy/config

- New optional env var: `SMAP_ALLOW_HTTP_PROVIDERS` (boolean, default false). When
  true, the SSRF validator accepts `http://` base URLs for local development
  (Ollama at `http://localhost:11434`).
- No Vault changes, no compose changes, no nginx changes.

## 7. NFR Checklist

- [x] i18n -- all new UI strings through `$t()`: provider labels, config field labels,
  error messages, capability names.
- [x] Audit log -- `key.uploaded` already emits `provider.value` in metadata
  (`key_service.py:123-153`). No new audit events needed; `openai_compat` flows
  through as a string value.
- [x] Tenant isolation -- key ownership enforced by `owner_user_id` FK + session auth.
  No new endpoints; the existing upload/list/delete routes handle the new provider
  transparently.
- [x] Error handling UX -- invalid config returns 422 with field-level errors (Pydantic
  validation). Probe failure shows the same `test_status: failed` + `test_error`
  message as other providers. Frontend shows config validation inline.
- [x] Performance -- no additional queries. The `config` column is read with the key
  row (single SELECT). No N+1 risk. JSONB default `'{}'` adds negligible storage for
  existing keys.

## 8. Security Considerations

This feature touches provider keys and user-input processing; both dimensions require
attention.

**SSRF via base_url.** The user supplies a URL the backend makes HTTP requests to (probe
at upload time, adapter at every LLM call). Without validation, an attacker could probe
internal services (`http://169.254.169.254/latest/meta-data/`, internal Vault endpoints,
Redis, etc.). Mitigation:

1. URL scheme validation: HTTPS required by default; HTTP allowed only with explicit env
   var (`SMAP_ALLOW_HTTP_PROVIDERS`), intended for local dev with Ollama.
2. DNS resolution check at upload time: resolve the hostname and reject if any A/AAAA
   record points to a private (RFC 1918), link-local (169.254.x.x), or loopback
   (127.x.x.x) address. This mirrors the SSRF guard in `services/egress_proxy/`.
3. Runtime re-validation: the adapter re-validates the resolved IP before connecting,
   not only at upload time, to prevent DNS rebinding attacks.
4. The egress proxy (`services/egress_proxy/`) already applies its own SSRF filter to
   outbound traffic from MCP sandboxes, but the provider adapters run in the arq worker
   process, not in the sandbox, so they bypass it. This feature's validation is the
   equivalent guard for the adapter path.

**Key material handling.** Unchanged. The secret is still envelope-encrypted via Vault
Transit (`key_service.py:96-98`). The `config` JSONB is not encrypted because it
contains no secrets (just a URL, label, timeout, and capability list). The base URL may
reveal internal infrastructure topology; this is acceptable because only the key owner
and project owners with usage-view permission can read it, and the key owner supplied it.

**Config injection.** The `config` dict is validated against a strict Pydantic model at
upload time. The adapter reads only the specific fields it needs (`base_url`, `timeout_s`)
and constructs the HTTP request from them. No string interpolation of config values into
URLs, headers, or bodies beyond the base URL itself (which is the whole point). Tool
definitions, messages, and model names come from the request payload, not from config.

**Tenant isolation.** Unchanged. Key ownership, key group membership, and project carry
scope are enforced by the existing authorization chain in the router
(`_load_eligible()` at `provider_router.py:790-814`, `call_single_key()` at `:666`).
The new `config` field does not affect authorization.

## 9. Quality Notes

**Existing debt in touched files:**

- `probes/__init__.py`: the dispatch table is a plain dict with no exhaustiveness check.
  Adding `OPENAI_COMPAT` without a probe entry would be a silent `KeyError` at runtime.
  Do not imitate -- but also do not fix here (FU-2).
- `AgentDetailView.vue:755-759`: `modelHintOptions` is a hardcoded array, not derived
  from the backend enum or catalog. Adding `openai_compat` requires updating it
  manually. Do not fix the derivation problem here (FU-3).

**Patterns to follow:**

- Adapter structure: `anthropic.py` and `openai.py` are the exemplars. Both implement
  `ProviderAdapter` + `StreamingAdapter`, dispatch on `request.capability`, resolve the
  model via `base.resolve_model()`, read capability flags via `base.capability_flags()`,
  and return `ProviderCallResult` with a normalised body shape.
- Probe structure: `openai.py` probe is the closest match (it hits `GET /v1/models`).
- Config conditional rendering: `SearchKeyUploadForm.vue:99-119` shows per-provider
  fields (CX for google_cse, search depth for tavily).
- Error scrubbing: `base.scrub_error()` and `base.scrub_stream_error()` -- never return
  raw provider error text to the caller.

**Reuse inventory:**

| What | Where | Use for |
|---|---|---|
| `base.new_client()` | `adapters/base.py:131` | HTTP client factory (adjust timeout) |
| `base.iter_sse_lines()` | `adapters/base.py:235-252` | SSE stream parsing |
| `base.resolve_model()` | `adapters/base.py:164-178` | Model ID from payload |
| `base.capability_flags()` | `adapters/base.py:216-232` | Capability flags from payload |
| `base.scrub_error()` | `adapters/base.py:134-141` | Secret-free error body |
| `base.scrub_stream_error()` | `adapters/base.py:143-162` | In-stream error body |
| `ProbeResult.ok()` / `.failed()` | `probes/base.py:28-34` | Probe return values |
| `summarise_http_failure()` | `probes/base.py:113-145` | Safe error string builder |
| `new_http_client()` | `probes/base.py:67-74` | Probe HTTP client (5s timeout) |

## 10. Risks and Rollback

**Risk 1: Compatibility variance across gateways.** Different gateways have different
behaviors (Nexus refuses `tool_choice: required`, Ollama returns tool arguments as
objects not strings, some gateways ignore `response_format`). Mitigation: the adapter
uses only the universally supported subset of Chat Completions. Known limitations are
documented. Users who hit incompatibilities can file issues, and the adapter can grow
gateway-specific workarounds behind config flags (FU-4).

**Risk 2: SSRF.** Mitigated by the three-layer validation described in section 8. The
DNS rebinding guard (runtime re-validation) is the strongest layer; without it, an
attacker could pass upload validation and then change the DNS record.

**Risk 3: Timeout mismatch.** Some gateways (Nexus: up to 35 minutes) need much longer
timeouts than the default 120s. Users who do not set `timeout_s` will see the adapter
time out on long prompts. Mitigation: the default 120s matches the existing adapter
default (`base.py:127`), and the config field is documented. The maximum is capped at
3600s to prevent accidentally holding connections open indefinitely.

**Migration rollback:** The `config JSONB` column addition is fully reversible (drop
column). The ENUM value additions (`openai_compat` in both `api_key_provider` and
`agent_model_hint`) are not reversible in PostgreSQL without type recreation, which is
the standard behavior for every ENUM addition in this project. If rollback is needed,
the values remain in the type but no code path produces them.

## 11. Acceptance Criteria

- [ ] AC-1: `POST /api/keys` with `provider: "openai_compat"` and
  `config: {"base_url": "https://..."}` creates a key. The probe hits
  `GET {base_url}/models` and sets `test_status` accordingly.
- [ ] AC-2: `POST /api/keys` with `provider: "openai_compat"` and missing or invalid
  `config` (no `base_url`, non-URL value, private IP) returns 422 with field-level
  errors.
- [ ] AC-3: An existing key upload (`provider: "claude"`, no `config`) works exactly as
  before. The response includes `config: {}`.
- [ ] AC-4: An agent with `model_hint: "openai_compat"` and a free-text `model_id` can
  make a non-streaming chat request through the adapter. The response is normalised to
  `{text, tool_calls, finish_reason}`.
- [ ] AC-5: Streaming works: `TokenDelta` events arrive per token, followed by one
  `StreamComplete` with usage counts.
- [ ] AC-6: Tool calling works: the adapter sends `tools` in Chat Completions format,
  parses `tool_calls` from the response, and normalises `arguments` to parsed JSON.
- [ ] AC-7: Embedding works when `config.capabilities` includes `"embedding"`: the
  adapter sends `POST {base_url}/embeddings` and normalises the response to
  `{embeddings: [[float, ...], ...]}`.
- [ ] AC-8: A key with `config.capabilities: ["llm_chat"]` is rejected (422) when
  attached to an embedding slot (RAG config).
- [ ] AC-9: The adapter reads `timeout_s` from config and uses it as the HTTP timeout.
  Default 120s when absent.
- [ ] AC-10: The probe rejects a `base_url` resolving to `127.0.0.1`, `10.x.x.x`,
  `172.16.x.x`, `192.168.x.x`, or `169.254.x.x` with a clear error message.
- [ ] AC-11: The frontend key upload form shows config fields (base URL, label, timeout,
  capabilities) when `openai_compat` is selected, and hides them for other providers.
- [ ] AC-12: The agent config form shows `openai_compat` in the provider dropdown and
  renders a free-text model input (no model dropdown) when selected.
- [ ] AC-13: DB migration applies cleanly (`alembic upgrade head`) and is
  forward-compatible: old code ignores the `config` column on existing keys.
- [ ] AC-14: The `ProviderRequest.provider_config` field is populated by the router for
  all three call paths (group rotation, single-key, single-key-stream). Unit test
  verifies injection.
- [ ] AC-15: `KeyOut` response includes the `config` field. Existing keys show
  `config: {}`. `openai_compat` keys show their stored config.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1 | Unit | `tests/unit/contexts/keys/` -- mock httpx, verify probe dispatch with config |
| AC-2 | Unit | `tests/unit/contexts/keys/` -- Pydantic validation: missing base_url, bad URL, private IP |
| AC-3 | Unit | `tests/unit/contexts/keys/` -- existing provider upload unchanged |
| AC-4 | Unit | `tests/unit/contexts/keys/adapters/` -- mock httpx, verify request shape and response normalisation |
| AC-5 | Unit | `tests/unit/contexts/keys/adapters/` -- mock SSE stream, verify TokenDelta + StreamComplete |
| AC-6 | Unit | `tests/unit/contexts/keys/adapters/` -- tool call round-trip |
| AC-7 | Unit | `tests/unit/contexts/keys/adapters/` -- embedding request and normalisation |
| AC-8 | Unit | `tests/unit/contexts/keys/` -- `assert_capability` with narrowed config |
| AC-9 | Unit | `tests/unit/contexts/keys/adapters/` -- httpx timeout matches config |
| AC-10 | Unit | `tests/unit/contexts/keys/probes/` -- SSRF rejection for each private range |
| AC-11 | Manual / e2e | Frontend key upload form renders config fields conditionally |
| AC-12 | Manual / e2e | Agent config form renders free-text model input for openai_compat |
| AC-13 | DB | `pytest.mark.db` -- `alembic upgrade head` + insert with and without config |
| AC-14 | Unit | `tests/unit/contexts/keys/` -- router injects provider_config from key |
| AC-15 | Unit | `tests/unit/app/api/v1/` -- KeyOut serialisation includes config |

## 13. SRS Delta

Amend `[R7.01]` -- add row to the provider table:

> | `openai_compat` | configurable | configurable | -- |
>
> `openai_compat` is a generic provider for any endpoint serving the OpenAI Chat
> Completions wire protocol. Its capabilities (`llm_chat`, `embedding`) are declared
> per key in a `config` object at upload time; the defaults are `llm_chat` and
> `embedding`. The `config` object carries `base_url` (required), `label` (optional
> display name), `timeout_s` (optional HTTP timeout in seconds, default 120, max 3600),
> and `capabilities` (optional subset of `["llm_chat", "embedding"]`).

Amend `[R7.02]` -- add validation endpoint:

> - OpenAI-compatible (`openai_compat`): `GET {config.base_url}/models`. The base URL
>   is user-supplied and validated at upload time: must be HTTPS (HTTP allowed only with
>   `SMAP_ALLOW_HTTP_PROVIDERS=true`), must not resolve to a private, link-local, or
>   loopback IP address. DNS resolution is re-checked at call time to prevent rebinding.

Add `[R7.16]`:

> **[R7.16]** The `api_keys` table carries a `config` JSONB column (default `'{}'`).
> For `openai_compat` keys, this column stores the validated provider configuration
> (`base_url`, `label`, `timeout_s`, `capabilities`). For all other providers, it is
> empty (`{}`). The config is validated at upload time against a strict schema; invalid
> config is rejected with 422. The config is not encrypted (it contains no secrets).

## 14. Open Questions

None blocking. All design questions resolved in section 3.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: Model discovery. Call `GET {base_url}/models` at upload time (or on demand) and
  store the returned model list in the key's config. Populate the agent config dropdown
  from discovered models instead of free-text only.
- FU-2: Exhaustiveness guard on `PROBES` dict. A missing probe entry is a silent
  `KeyError` at runtime. Add a startup check or derive the dict from the enum.
- FU-3: Derive `modelHintOptions` from the backend catalog/enum instead of hardcoding
  the three-member array in `AgentDetailView.vue`.
- FU-4: Gateway-specific workarounds. If specific gateways need adapter-level
  workarounds (Ollama's object-typed tool arguments, Nexus's `think` extension), add
  optional config flags (e.g. `config.quirks: ["ollama_tool_args"]`) rather than
  per-vendor adapters.
