---
type: bugfix
status: draft
created: 2026-07-22
requirements: []
depends_on: []
---

# `web_search` results are cached globally and served across projects

## 1. Summary

The `web_search` tool's result cache is keyed on the shape of the query alone — provider,
normalised text, `top_k`, `locale`, `freshness` — with no tenant or key identity. Redis is a
single flat keyspace, so within the 600-second TTL an agent in project B receives, verbatim,
the results an agent in project A obtained from A's own search key. With Google CSE the `cx`
selects the entire searchable corpus, so this serves one tenant's private, site-restricted
corpus to another. B's key is never unwrapped, B's own corpus is never queried, and B's
search quota is never charged, because the cache is consulted before the rate limiter. The
audit record for B's search says `source: "cache"` and carries B's own `project_id`, so
nothing in the trail marks the payload as foreign.

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-1 (critical, confirmed —
independently surfaced by two investigation lenses and upheld against a refutation pass that
found no namespace at any layer).

## 2. Observed vs Expected

- **Observed.**
  - `_cache_key` hashes only the five call-shape arguments:
    `backend/contexts/agents/application/tools/web_search.py:49-52`. The tenant identity is
    available on the tool (`self.project_id`, set at `:82`, supplied by
    `backend/contexts/agents/application/runtime/builtin_tools.py:138-146`) and the resolved
    `SearchKey` is in hand (`:115`) — both are deliberately absent from the key.
  - The key is used verbatim to read (`:125-126`) and to write (`:160`).
  - `RedisSearchCache` applies no prefix of its own — the caller's string is the literal Redis
    key (`backend/contexts/agents/infrastructure/search_cache.py:63,76`), and `get_redis()` is
    one process-wide client with no namespace, no per-tenant logical DB
    (`backend/shared_kernel/auth/clients.py:35-50`).
  - The cache is consulted before the rate limiter, by documented design
    (`web_search.py:122-131`, the DOM-12 comment), so a cross-tenant hit also bypasses the
    reading project's quota.
  - Cache invalidation is announced but unimplemented:
    `backend/contexts/keys/application/search_service.py:170-173` publishes
    `search_key.activated` on the assumption that "the cache owner can choose the strategy". A
    repo-wide search finds no subscriber, so today a key rotation or config edit invalidates
    nothing either.

- **Expected.** A cached search result is returned only to the project, key, and key
  configuration that produced it. A project's agent must never observe data derived from
  another project's provider key.

  **Intent source.** No `[Rxx.yy]` entry governs the cache key — `requirements: []` above is a
  positive claim, not an unfilled field, and it bounds what this dossier can appeal to. The
  expected behavior rests on two internal sources instead: the multi-tenant isolation
  constraint in `CLAUDE.md` ("every API endpoint must verify org/project membership before
  returning data" — the same boundary, one layer down), and the codebase's own dominant
  convention, stated below in §6 and exemplified by the file next door,
  `backend/contexts/agents/infrastructure/search_rate_limiter.py:24`, which scopes its Redis
  key by `project_id` for the same feature. No user decision is needed on what "expected"
  means here; cross-tenant data return is not a behavior anyone has to ratify as wrong.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which identifiers belong in the cache key: `project_id`, `key.id`, a hash of `key.config`, or some subset? | All three. `project_id` as a literal leading segment; `key.id` and a canonical digest of `key.config` inside the hash. | Each closes a distinct case and none subsumes the others. `project_id` alone leaves intra-project key rotation and in-place `cx` edits broken. `key.id` alone technically implies the project but yields an opaque keyspace with no operational handle. `config` hash alone covers neither. Decided without asking: this is a correctness question with one defensible answer, not a preference. |
| Q-2 | Must existing cache entries be invalidated on deploy? | No. Let them age out. | Old entries are unreachable under the new key shape and expire within `_CACHE_TTL_S = 600` (`web_search.py:45`). No migration, no repair step. An optional `SCAN`-based purge is recorded in §7 as operator convenience, deliberately not as an AC. |
| Q-3 | Should the cache-before-limiter ordering change, since it is what converts the defect into quota misattribution? | No. Leave it. | The ordering is deliberate and documented (`web_search.py:122-124`): the limiter throttles real egress and a cache hit makes none. Once the key is project-scoped, a hit can only be the reading project's own earlier result — exactly the case the ordering was designed for. The billing aggravator disappears as a consequence of the key fix. Moving the read would silently change quota semantics. |
| Q-4 | Should `SearchCache` (the port) learn about `project_id`? | No. The protocol is unchanged. | The cache key is a policy decision — what counts as "the same search" — and belongs in `application/`, where it already lives. Widening the port to `get(project_id, key)` would push domain knowledge into `infrastructure/` and force every fake to change. Mirrors the explicit separation note at `backend/shared_kernel/infra/redis_buckets.py:36-40`. |
| Q-5 | Should a shared "tenant-scoped Redis key" helper be introduced in `shared_kernel/`? | No. | Every one of the ~25 correctly-scoped sites surveyed in §6 builds its key inline. Introducing the abstraction here would touch all of them for no behavioural gain. The proportionate mechanism for the invariant is a comment on `_cache_key` plus the keyspace-shape test in §8. |
| Q-6 | Does this depend on either open dossier — `2026-07-07-graphrag-two-axis-redesign` or `2026-07-19-large-artifacts-silently-dropped`? | No. `depends_on: []`. | Neither touches `contexts/agents/application/tools/` or the search path. Checked against `BOARD.md` (both open entries) rather than assumed. |

## 4. Reproduction

Deterministic; no race.

**Preconditions.** Two projects A (`pid_A`) and B (`pid_B`) — they may be in different orgs
entirely, since the Redis keyspace is flat and global. One actor per project holding
`Capability.KEY_CONFIGURE` for that project
(`backend/app/api/v1/search_keys.py:86,138`; plain membership at `:68` only lists keys). One
agent per project with `web_search` enabled and a chat-capable key group. A single shared
Redis — the normal deployment.

**Steps.**

1. As A's key admin: `POST /api/projects/{pid_A}/search-keys` with provider `google_cse` and
   `config.cx` set to a Programmable Search Engine restricted to `internal-wiki.acme.com`,
   then `POST .../{key_id}/activate` (`search_keys.py:81-105,133-153`).
2. As B's key admin: the same two calls against `pid_B`, with a different `cx` that searches
   the open web.
3. In a chatroom under `pid_A`, drive A's agent to call `web_search` with the query
   `Q4 roadmap`. It goes live (`web_search.py:145-154`) and writes
   `search:sha256("google_cse|q4 roadmap|5|en-US|any")` (`:160`).
4. Within 600 seconds, in a chatroom under `pid_B`, drive B's agent to call `web_search` with
   the identical query. Defaults make the remaining dimensions match automatically:
   `top_k=5`, `freshness="any"`, and `locale` is always `"en-US"` because
   `builtin_tools.py:148-152` never forwards it.

**Observe.**

- B's agent returns A's `internal-wiki.acme.com` URLs.
- No request is made to `googleapis.com/customsearch/v1` on B's behalf — verify at the egress
  proxy access log.
- B's limiter counter `search:rl:{pid_B}:{window}` is not incremented
  (`search_rate_limiter.py:24`), because the hit returns at `web_search.py:130`, before
  `:133`.
- B's audit row reads `"source": "cache"`, `"provider": "google_cse"`,
  `"project_id": str(pid_B)` (`:188-202`) — no marker that the payload originated elsewhere.

**Single-project variant** (isolates the key/config half, no second tenant needed): within one
project, upload and activate a second `google_cse` key with a different `cx`. For up to 600
seconds the retired key's results are still served.

## 5. Root Cause Analysis

| Link | Evidence |
|---|---|
| L0 — the tool is constructed per turn with the agent's `project_id`, so the tenant identity is in scope at the call site | `backend/contexts/agents/application/runtime/builtin_tools.py:138-146` |
| L1 — the active key, including `id` and `config`, is resolved project-scoped | `backend/contexts/agents/application/tools/web_search.py:91-100` (`_active_key`) |
| L2 — **the cache key discards every tenant- and key-identifying input** | `web_search.py:49-52` |
| L3 — that key is used verbatim for the read | `web_search.py:125-126` |
| L4 — and verbatim for the write | `web_search.py:160` |
| L5 — the cache applies no namespace of its own | `backend/contexts/agents/infrastructure/search_cache.py:63,76` |
| L6 — Redis is one flat global keyspace | `backend/shared_kernel/auth/clients.py:35-50` |
| L7 — symptom: B reads A's entry; B's key is never unwrapped (`:143`), B's config never reaches the adapter (`:153`), B's quota is never charged | `web_search.py:122-131` |

**Root cause: L2**, `_cache_key` at `web_search.py:49-52`. It is the only point where the
tenant identity is available and deliberately dropped; L3–L7 are faithful consumers of
whatever string it returns.

**Aggravating factors, explicitly not the root cause:**

- L6, the absence of global Redis namespacing, is a codebase-wide design property (§6) and is
  not worth changing for this fix.
- `_CACHE_TTL_S = 600` (`:45`) sets the width of the exposure window.
- Cache-before-limiter (`:122-131`) converts a correctness defect into a billing defect as
  well.
- `google_cse`'s `cx` selecting the whole corpus
  (`backend/contexts/agents/infrastructure/search_adapters/google_cse.py:59-67`) is what makes
  the bleed a private-corpus disclosure rather than merely stale public results.
- The unimplemented invalidation subscriber (`search_service.py:170-173`) means key rotation
  does not clear the cache today either.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every pair of projects sharing a search provider — the common case, since
only four providers exist (`backend/contexts/keys/domain/search.py:14-18`). Three distinct
breakages: the wrong corpus is queried; quota and cost are misattributed; and results are
non-deterministic, depending on which tenant warmed the cache first. No privileged role is
needed on the reading side — any chat participant in a project with an active search key can
elicit a `web_search` call. Nothing has been persisted incorrectly: the only bad data is
transient Redis entries, so there is no data repair obligation.

**Sibling suspects — systematic sweep.** Method: every `get_redis()` call site (44 files),
every f-string containing a `:`-separated key literal under `backend/contexts/**`,
`backend/shared_kernel/**` and `backend/app/workers/**`, plus every `lru_cache`, module-level
mutable dict, and content-hash-derived key.

- **Confirmed vulnerable — one site only**: `web_search.py:49-52`, this finding.
- **Cleared, scoped by an unguessable tenant-owned UUID** (cross-tenant collision impossible
  by construction): `search_rate_limiter.py:24`; `agents/application/egress.py:92`;
  `agents/infrastructure/turn_lock.py:27`; `runtime/turn_engine.py:2383`;
  `knowledge/infrastructure/redis_lock.py:45,49,53,57`;
  `shared_kernel/infra/redis_buckets.py:85`;
  `orchestration/infrastructure/wakeup_state.py:27,31,35,39,43`;
  `orchestration/infrastructure/a2a_rendezvous.py:36,40,44`;
  `orchestration/infrastructure/a2a_streams.py:34,38`;
  `orchestration/infrastructure/pending_notify.py:29`; the `wf:*` family across
  `workers/tasks/workflow_signals.py:51,263`, `workflow_approvals.py:50,150`,
  `workflow_cron.py:64`, `executors/wait_for_event.py:54`, `executors/subagent_spawn.py:82`,
  `orchestration/application/subagent_service.py:234`, `run_engine.py:756`;
  `tenancy/application/advisory_service.py:18,76,85`;
  `keys/application/threshold_worker.py:145`; `conversation/infrastructure/tus_store.py:88-89`;
  `conversation/infrastructure/presence.py:40,44,48,182,187`;
  `shared_kernel/realtime/connection.py:91`; the `channels.py` pub/sub families across five
  contexts; `shared_kernel/realtime/ws_auth.py:50`;
  `prompt_studio/infrastructure/session_store.py:29-34` (with ownership additionally
  re-verified at `session_service.py:79-81`); `workers/tasks/knowmap.py:126`;
  `keys/infrastructure/key_revocation_events.py:38`; `shared_kernel/db/advisory_lock.py:36`;
  the in-process `_KERNELS` dict at
  `agents/infrastructure/sandbox/docker_runsc.py:528,1310,1394`.
- **Cleared, global by explicit design** (no tenant dimension exists, and a single shared
  value is the intent): `shared_kernel/auth/ratelimit.py:108,153` (operator-wide policy
  mirror, documented at `:100-124`) and `:193` (`rl:{bucket}:{ident}`, where `ident` is a user
  id or IP per `:169-182`); `identity/infrastructure/lockouts.py:36-37` and the
  `rl:*:e:sha256(email)` family at `identity/application/auth_service.py:156,480` and
  `tenancy/application/invite_service.py:208` (login and invite precede any tenant context);
  `identity/infrastructure/email_domain_policy.py:15-17` (instance-wide registration policy);
  `workers/tasks/knowmap.py:82` (singleton sweep cursor).
- **Cleared, not a shared keyspace**:
  `notification/application/notification_service.py:50-56` builds
  `dedup_key = f"auto:{kind}:{sha256(title)[:32]}:{bucket}"`, which looks like the identical
  mistake — content-derived, no owner — but is enforced by a Postgres unique index on
  `(user_id, dedup_key)`, documented at `:40-48`. The user id is the scoping column. Cleared.
  Module-level dicts under `backend/contexts/**` are static immutable configuration, not
  caches, and no `functools.lru_cache` is applied to any tenant-parameterised function.

**Conclusion.** This is not a systemic omission. The codebase's dominant and correct pattern is
that a Redis key begins with an owning UUID, and `web_search.py:49-52` is the single deviation
— precisely because it is the only key derived from *content* rather than from an entity. The
fix is genuinely local. The invariant worth recording so it is not re-broken: **any Redis key
derived from user content must carry the owning `project_id` as its first variable segment.**

## 7. Fix Design

Make `_cache_key` take the tenant and key identity, and call it after `_active_key()` has
resolved — which it already is by `:125`, so only the argument list changes, not the flow.

Target shape: `f"search:{project_id}:{digest}"`, where `digest` covers `key.id`, a canonical
serialisation of `key.config`, `provider`, the normalised query, `top_k`, `locale` and
`freshness`.

- `project_id` is a **literal** segment, not merely hashed, so the keyspace stays greppable,
  scannable and purgeable per project — matching `search_rate_limiter.py:24` and every cleared
  site in §6. A UUID cannot contain the delimiter, so two distinct projects can never produce
  the same string: the collision becomes unrepresentable rather than merely unlikely.
- `key.id` covers rotation within a project. Without it, activating a replacement key serves
  the retired key's results for up to 600 seconds.
- The `key.config` digest covers an in-place edit on the same key id (changing `cx`, or
  `search_depth` once F-19 lands). `key.config` is a `BoundedConfig` capped at 16 KB / depth 12
  / 500 nodes (`backend/shared_kernel/validation.py:90`), so hashing it per call is cheap and
  bounded.
- `key.config` must be canonicalised with `json.dumps(cfg, sort_keys=True, separators=(",", ":"))`
  before hashing: Python dict order follows insertion order and a JSONB round-trip does not
  guarantee stability. Precedent at `backend/contexts/agents/application/tool_auth.py:56`.
- Choose `search:{project_id}:{digest}` and not a shape like `search:{digest}:{project_id}`,
  which could collide with the existing `search:rl:{project_id}:{window}` family.

**Why this corrects rather than masks.** The symptom is one tenant reading another's entry.
Shortening the TTL narrows the window without closing it; a per-request nonce disables caching
outright; per-process Redis prefixing is accidental and breaks under multiple workers. Putting
the owning identity in the key makes the collision impossible to express, independent of TTL,
query text, or deployment topology.

**Data repair: none.** Old entries are unreachable under the new shape and expire within 600
seconds. Optionally an operator may purge immediately with `SCAN MATCH search:*` excluding
`search:rl:*` and `search:{uuid}:*`; this is convenience, not a required step, and is
deliberately absent from the ACs.

**Also in scope, same commit:** update the module docstring at `web_search.py:8`, which
documents the key as `hash(provider,query_norm,top_k,locale,freshness)`. That docstring is the
intent-of-record a future audit will compare against; leaving it stale invites the next audit
to re-find a phantom. Update the misleading comment at `search_service.py:170-172`, which
describes an invalidation mechanism that does not exist — once `key.id` is in the key,
activation invalidates by construction.

## 8. Regression Test Plan

**File:** extend `backend/tests/unit/test_web_search_tool.py`. No new file and no Redis are
needed — the existing `_DictCache` (`:54-62`) stores whatever key it is handed, so asserting
isolation only requires sharing one instance between two tools.

**Fixture prerequisite.** `_sk()` (`:98-113`) currently hardcodes `project_id=uuid.uuid4()` and
`config={}`, so no existing test can construct two keys differing only in tenant. Add
`project_id` and `config` parameters with defaults; the four existing call sites
(`:161,179,217,249`) are unaffected.

**The failing test comes first** — `test_cache_is_not_shared_across_projects`: one `_DictCache`
shared by two `_StubWebSearchTool`s with distinct `project_id` and distinct `SearchKey` ids,
each with its own `_FakeAdapter` returning distinguishable results; both search the identical
`("hi", top_k=5, locale="en-US", freshness="any")`. Asserts `len(adapter_b.calls) == 1` and
that B's titles are B's. **Fails against current code** because `_cache_key`
(`web_search.py:49-52`) is a pure function of the five call-shape arguments and is byte-identical
for both tools, so B hits the cache at `:126` and `adapter_b.calls == []`.

Then:

- `test_cache_is_not_shared_across_key_ids_in_one_project` — same project, two key ids. Pins
  the rotation case. Fails today for the same reason.
- `test_cache_is_not_shared_across_differing_key_config` — same project, same key id,
  `config={"cx": "corpus-a"}` vs `{"cx": "corpus-b"}`. Pins the in-place edit case. Fails today
  for the same reason.
- `test_cache_key_is_stable_under_config_dict_ordering` — two configs with the same pairs in
  different insertion order must produce a cache *hit*. Pins the `sort_keys=True`
  canonicalisation; without it this fails intermittently by construction.
- `test_cache_key_is_project_prefixed` — a direct assertion that `_cache_key` returns a string
  starting `f"search:{project_id}:"`. Locks the operational keyspace shape and the
  non-collision with `search:rl:`.
- **Guard against over-correction:** the existing `test_live_call_then_cache_hit` (`:177-199`)
  must keep passing unchanged — same project, same key, same query still hits. This is the test
  that stops a "just add a nonce" fix from being accepted.

`RedisSearchCache` needs no test for this change: it is a pure pass-through of the caller's key
string and is not being modified.

## 9. Risks and Rollback

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cache hit rate drops, since the keyspace is now partitioned per project x key x config | Certain, by design | Slightly more provider egress per project | This is the intended cost of correctness, not a regression. Magnitude is bounded: a project's own repeated queries still hit. |
| Orphaned old-shape entries after deploy | Certain | Bounded memory, at most 600s | Ages out via `_CACHE_TTL_S`. Optional manual purge (§7). |
| Rolling deploy with mixed old and new pods | Certain in staging and production | Old pods keep using the old shape; the two keyspaces are disjoint | Not a correctness problem — the old shape is the buggy one and simply stops being written once rollout completes. Exposure ends at the last old pod plus 600s. No coordination needed. |
| Unstable config hash from dict ordering causes permanent misses | Low | Cost, not correctness | `sort_keys=True` plus the ordering test in §8. |
| Key-shape collision with `search:rl:*` | Low | Corrupt limiter counters or cache reads | Use `search:{project_id}:{digest}`; assert the prefix in §8. `RedisSearchCache._decode` fails soft on unparseable payloads (`search_cache.py:68-71`), degrading a stray collision to a miss — but do not rely on that. |

**Rollback.** Revert the single commit. The change is confined to `web_search.py` and
`test_web_search_tool.py`, with no migration, no schema change, no config flag and no
cross-service contract (`SearchCache` protocol and `RedisSearchCache` both unchanged). Old
entries resume being written with no residue. Because reverting restores the cross-tenant
exposure, a revert should be paired with disabling `web_search` on affected agents.

## 10. Acceptance Criteria

- [ ] AC-1: `test_cache_is_not_shared_across_projects` (§8) fails against current code and
      passes after the fix.
- [ ] AC-2: two agents in different projects issuing an identical query each reach their own
      provider adapter; neither observes the other's results.
- [ ] AC-3: within one project, activating a replacement search key causes the next identical
      query to go live rather than serve the retired key's cached results.
- [ ] AC-4: within one project, editing `key.config` in place (e.g. changing `cx`) causes the
      next identical query to go live.
- [ ] AC-5: two `key.config` dicts holding the same pairs in different insertion order produce
      a cache hit, not a miss.
- [ ] AC-6: `_cache_key` returns a string beginning `search:{project_id}:` and does not collide
      with the `search:rl:` namespace.
- [ ] AC-7: the pre-existing same-project cache-hit behaviour is unchanged —
      `test_live_call_then_cache_hit` passes without modification.
- [ ] AC-8: no provider key plaintext appears in any cache key, log line, or audit payload; the
      key-shape computation runs before the unwrap at `web_search.py:143` and the audit payload
      (`:188-202`) gains no new fields.
- [ ] AC-9: the module docstring at `web_search.py:8` and the stale invalidation comment at
      `backend/contexts/keys/application/search_service.py:170-172` describe the implemented
      behaviour.
- [ ] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .` and `mypy .` pass in
      `backend/`.

## 11. SRS Delta

None. This restores the tenant-isolation behaviour the platform already claims; it does not
define new behaviour. Noted in §2: no `[Rxx.yy]` entry governs the search cache key at all,
which is itself worth recording — see FU-2.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — `backend/contexts/agents/application/runtime/builtin_tools.py:148-152` never
  forwards `locale` to the tool, so the `locale` dimension of the cache key is dead in
  production (always `"en-US"`). Behavioural change to the tool surface; belongs to its own
  finding, not this fix.
- **FU-2** — No SRS entry governs search-result caching or its tenancy. Given that the cache
  sits on a tenant boundary, an `[Rxx.yy]` entry stating the isolation guarantee would give
  future audits something to judge against; today they can only appeal to internal
  consistency.
- **FU-3** — `web_search.py:164-178` (`_unwrap_search_key`) performs function-body imports and
  re-instantiates a `SearchKeyRepository` that `_active_key` (`:92`) built moments earlier, and
  its docstring at `:165-167` concedes it duplicates `KeysFacade.unwrap_api_key_plaintext`. Two
  DB round-trips per live search plus a knowingly duplicated code path. Cleared-but-fragile;
  worth consolidating.
- **FU-4** — `web_search.py:128` re-caps results that were already capped before the write at
  `:159`. Harmless redundancy; do not add a third capping site.
- **FU-5** — Post-fix, run `check-security` over the agents context's tenant-isolation surface.
  The audit flagged F-1 as warranting a parallel security pass
  (`docs/audits/2026-07-22-agent-config-runtime/findings.md`, F-1 Note); the user triaged it as
  an ordinary bugfix, which this dossier honours, but the referral should not be lost.
</content>
