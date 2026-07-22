---
type: audit
status: reviewed
created: 2026-07-22
requirements: [R12.15]
---

# Audit: Web-search cache adversarial verification

## 1. Scope

- **Area**: The `web_search` cache-key path, active search-key lifecycle, Redis cache decode path, rate-limit ordering, and search-call auditing.
- **Intent sources**: `docs/tasks/2026-07-22-web-search-cache-project-scoping/spec.md`; `[R12.13]`, `[R12.14]`, and `[R12.15]` in `REQUIREMENTS.md`; the search-key audit-event inventory in `REQUIREMENTS.md:841`.
- **Depth**: Thorough. Three independent read-only lenses covered isolation/key equivalence, input and cache-corruption boundaries, and lifecycle/error paths. Each candidate received an explicit refutation pass; an executable cache-key matrix checked nested-config canonicalisation and all isolation dimensions.

## 2. Coverage

Read in full: `web_search.py`, `search_cache.py`, `search_rate_limiter.py`, search-key API/service/repository/domain types, `BoundedConfig` validation, Google CSE adapter, built-in-tool runtime wiring, relevant requirements, and `test_web_search_tool.py`.

Executed: the focused unit module (`10 passed`) and a read-only cache-key matrix for nested dictionary ordering, project, key ID, config, namespace, and plaintext absence.

Not covered: a live Redis/PostgreSQL fault-injection deployment, all provider adapters, or the entire backend suite. The configured integration database host (`postgres:5432`) was unavailable, and the non-integration suite exceeded the 120-second runner limit.

## 3. Findings

## F-1: Corrupt Redis cache data aborts search instead of degrading to a cache miss

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/infrastructure/search_cache.py:63-71` decodes Redis bytes before the fail-soft `try` block, so invalid UTF-8 raises `UnicodeDecodeError`. `_decode` at `:29-48` assumes each decoded top-level item has `.get`; a non-empty JSON object raises `AttributeError`, which `get` does not catch. `WebSearchTool.search` awaits this cache read at `backend/contexts/agents/application/tools/web_search.py:146-151` before the rate limiter and provider fallback.
- **Failure scenario**: An operator, stale deployment, or Redis corruption leaves `b"\xff"` or `{"bad": 1}` under a search cache key. The next otherwise-valid search raises before egress rather than treating the value as a cache miss and continuing live. Both failure modes were reproduced with a fake Redis response and direct `_decode` call.
- **Blast radius**: Any affected project/key/query tuple loses web search availability until the invalid key expires or is manually removed; it can also prevent the expected live provider call.
- **Intent source**: `docs/tasks/2026-07-22-web-search-cache-project-scoping/spec.md` §9 describes `RedisSearchCache._decode` as fail-soft on unparseable payloads. The implementation does not meet that stated degradation behavior.

## F-2: Web-search audit events omit required HTTP status and disappear on provider failures

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/tools/web_search.py:201-223` emits `mcp.tool_invoked` without an `http_status` field. A live Google CSE response is received at `backend/contexts/agents/infrastructure/search_adapters/google_cse.py:73-82`, but the adapter returns only results and raises on an HTTP error before `WebSearchTool` reaches its success-only audit call at `web_search.py:180-182`. The runtime merely converts that exception to a tool error at `backend/contexts/agents/application/runtime/builtin_tools.py:147-155`.
- **Failure scenario**: A configured Google CSE key receives HTTP 429 or 500. The proxy call leaves the platform, the adapter raises, and no `mcp.tool_invoked` event records that search call. A successful live call is recorded but lacks the required HTTP status.
- **Blast radius**: Search-call observability and incident forensics are incomplete; administrators cannot distinguish cache hits, successful egress, and provider failures using the required audit fields.
- **Intent source**: `[R12.15]` (`REQUIREMENTS.md:627`) requires every search call's `mcp.tool_invoked` audit event to include provider name, HTTP status, and result count.

## F-3: Search-key rotation omits the deactivation audit event for the previous active key

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/keys/infrastructure/search_repository.py:163-183` deactivates all active siblings while activating the requested key. `backend/contexts/keys/application/search_service.py:159-169` emits only `search_key.activated` for the replacement. A repository-wide search finds no `search_key.deactivated` emitter.
- **Failure scenario**: A project activates K2 while K1 is active. The database correctly marks K1 inactive and K2 active, but the audit history contains only K2's activation; there is no event recording K1's state transition.
- **Blast radius**: Audit consumers cannot reconstruct search-key lifecycle transitions or reliably identify when a particular key stopped being active.
- **Intent source**: `REQUIREMENTS.md:841` lists both `search_key.activated` and `search_key.deactivated` as required search-key audit events.

## 4. Refuted Candidates

- Cross-project cache bleed is prevented: `_cache_key` emits `search:{project_id}:{digest}` and includes key ID, canonical config, provider, and request shape (`backend/contexts/agents/application/tools/web_search.py:50-73`); active-key lookup and decrypt queries independently enforce the same project (`:112-121`, `backend/contexts/keys/infrastructure/search_repository.py:115-135`).
- Cache collisions with rate-limit counters are impossible for UUID project IDs: cache keys start `search:{uuid}:`, while counters start `search:rl:{uuid}:` (`backend/contexts/agents/infrastructure/search_rate_limiter.py:23-30`).
- Key rotation and in-place config differences cannot reuse old entries: `key.id` and `key.config` both feed the digest, and `json.dumps(..., sort_keys=True)` canonicalises nested mapping order (`web_search.py:59-73`).
- A cache hit cannot consume quota: the early return follows `cache.get` and precedes `try_acquire` (`web_search.py:143-158`); a miss passes the same tool `project_id` to the limiter.
- A forged mismatch between `WebSearchTool.project_id` and `SearchKey.project_id` is possible only in a test fake; production lookup selects keys by the tool project and decrypt repeats the project predicate (`web_search.py:112-121,194-199`).
- Concurrent identical misses are not single-flight and can duplicate egress, but neither `[R12.13]` nor the task dossier promises request coalescing; it is not a defect against the audited intent.
- A request overlapping key activation can use the key resolved before activation, but the requirements and task dossier do not define linearizability for in-flight searches. Sequential post-activation searches use the new key ID and are isolated.

## 5. Hand-off

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | Fix | `docs/tasks/2026-07-22-web-search-cache-project-scoping/` |
| F-2 | Fix | `docs/tasks/2026-07-22-web-search-cache-project-scoping/` |
| F-3 | Fix | `docs/tasks/2026-07-22-web-search-cache-project-scoping/` |

## 5.1 Review decision

The user selected every confirmed finding for repair on 2026-07-22. The active web-search
cache task owns the agreed scope expansion and its deviation log records the decision.

## 6. Out-of-scope Observations

- `backend/contexts/agents/application/runtime/builtin_tools.py:148-152` does not forward locale, making the cache's locale dimension constant in production. This is already recorded as FU-1 in `docs/tasks/2026-07-22-web-search-cache-project-scoping/spec.md`.
- The cache-scoping fix introduces no confirmed cross-tenant or plaintext-secret regression. Those security properties were considered in the isolation lens but are not findings in this functional audit.
