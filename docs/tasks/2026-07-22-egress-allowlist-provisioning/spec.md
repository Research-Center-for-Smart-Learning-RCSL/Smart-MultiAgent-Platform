---
type: bugfix
status: in-progress
created: 2026-07-22
requirements: [R12.16]
depends_on: []
---

# `web_search` is enabled by default and denied by the egress proxy in every new project

## 1. Summary

`[R12.16]` states the proxy's allowlist "is seeded with the four providers' documented
hostnames". Nothing seeds it. The table is created empty by migration `0014_mcp.py` and is
written only by the four MCP allowlist endpoints; project creation provisions nothing.
Meanwhile every agent is created with `hosted_web_search` **enabled**, and the search adapters
egress only through the proxy, which denies on an empty table. So the documented out-of-box
experience — configure a search key, create an agent, ask a question — fails on first use in
every new project.

Two aggravating factors surfaced during analysis that were not in the original finding, and
both make this worse than "a missing seed":

- **The search-key probe bypasses the proxy entirely.** `search_probes.py:23-92` uses raw
  `httpx` against the provider, and its own module docstring at `:3-5` claims "a green probe
  actually guarantees the downstream tool call will authenticate." That guarantee is false.
  The one pre-flight signal a user would trust as "search is configured" is the signal that
  lies.
- **The existing warning channel structurally excludes this tool.** `_function_warnings`
  (`backend/app/api/v1/agents.py:509-528`) already knows how to say "host X is not on the
  project egress allowlist", but early-returns at `:512` unless the tool type is
  `LOCAL_FUNCTION` — so the tool that is enabled by default gets no warning at all.

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-9 (major, confirmed).

## 2. Observed vs Expected

- **Observed.**
  - `backend/alembic/versions/0014_mcp.py:24-44` is `create_table` + `create_index` only, and
    is the only migration in 0000–0056 naming `mcp_egress_allowlist`.
  - `backend/contexts/tenancy/application/project_service.py:69-97` creates the project row,
    adds the owner, emits the audit event, returns. No allowlist call.
  - `backend/contexts/agents/application/agent_service.py:449-452` calls
    `provision_tool_singletons`, which at `:1051-1057` passes `web_search=True` into
    `backend/contexts/agents/infrastructure/repositories.py:621-651`, inserting the
    `HOSTED_WEB_SEARCH` row with `enabled=True`.
  - The adapters egress only via the proxy —
    `backend/contexts/agents/infrastructure/search_adapters/tavily.py:68-74` and siblings.
  - The proxy denies on an empty table —
    `backend/services/egress_proxy/app.py:287-302` →
    `backend/services/egress_proxy/main.py:27-39`, a bare `SELECT ... WHERE project_id AND
    hostname`. No constant, no fallback, no env baseline.
  - The 403 reaches the model as `web_search failed: tavily returned HTTP 403: ...`
    (`tavily.py:75-78` → `backend/contexts/agents/application/runtime/builtin_tools.py:153-154`).
  - The rate-limit token is consumed at
    `backend/contexts/agents/application/tools/web_search.py:133-140` **before** the doomed
    egress, so failed searches still exhaust the project's quota.

- **Expected.** `[R12.16]` (`REQUIREMENTS.md:658`), restated in
  `docs/implement/E-agents-knowledge.md:283`, promises that `web_search` works without a
  manual operator step. That intent is correct; §3 Q-1 argues its literal mechanism is not,
  and §11 amends it.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should the four provider hostnames be seeded into every project, as `[R12.16]` literally says? | **No.** Seed **one** hostname, at search-key activation. | `web_search.py:91-100` enforces exactly one active search key per project (partial-unique-active index, honoured by `search_service.py:142-173`), so a project can reach at most **one** of the four at any instant. Seeding four grants three hostnames of standing, unused, unaudited egress to every project on the platform — including the many that never use `web_search` at all, since the tool is enabled by default. `www.googleapis.com` in particular fronts a very large API surface and the allowlist is hostname-only with no path scoping. Where the literal text of R12.16 and least privilege disagree, amend the requirement (§11). |
| Q-2 | Where does the seed belong — project creation, or key activation? | **Key activation** (`backend/contexts/keys/application/search_service.py:142-173`). | It already knows `project_id` and `sk.provider` (`:150-151`), already runs in the caller's session, and already pairs an audit emit with a cache-invalidation publish (`:159-173`) — it is the established home for activation's cross-cutting consequences. Crucially it is **one writer**: seeding at project creation would require a second copy of the provider→hostname list in a migration, and migrations are frozen while services are not, so the two would drift the first time a fifth provider is added. |
| Q-3 | Should the proxy carry a built-in constant baseline, with the DB holding only additions? | **No. Rejected.** | It breaks the remove endpoint fatally: `DELETE .../egress-allowlist/{hostname}` (`backend/app/api/v1/mcp.py:181-201` → `mcp_repositories.py:79-88`) deletes a *row*, and a baseline host has no row, so the operator gets a silent no-op while the host stays reachable. Making removal work needs a tombstone concept — a schema change and a semantics change to an SSRF control. It also makes `PUT []` claim to clear an allowlist that still permits four hosts, falsifies two shipped UI strings (`frontend/src/slices/agents/locales/en.json:539,542`), and makes the default ungovernable without a redeploy. |
| Q-4 | Should the probe be routed through the proxy so a green probe proves the tool path? | **No — amend the docstring instead.** | Attractive but blocked by ordering: `upload()` probes *before* activation (`search_service.py:41`), so at probe time the host is legitimately not yet allowlisted. Routing it through the proxy would require either exempting the upload probe or moving the seed to upload, both of which weaken the "seed on demonstrated intent" property Q-1 is buying. Correct the false guarantee at `search_probes.py:3-5` and rely on the activation seed plus the §7 warning. |
| Q-5 | If the allowlist write fails, should the activation fail with it? | **Yes — fail closed.** | The upsert shares the caller's session. A half-activated key that cannot actually search is exactly the defect being fixed. Note `activate()` already has a non-transactional side effect at `:173` (a Redis publish); do **not** copy that pattern for the allowlist write. |
| Q-6 | Does this depend on any open dossier, or overlap the same-day a2a orchestration audit? | No. `depends_on: []`. | Checked against `BOARD.md`. The a2a audit's dossiers cover orchestration and turn locking, not egress or key activation. |

## 4. Reproduction

Brand-new project on current `main`:

1. Create a project (`POST /api/projects` → `project_service.py:52-97`).
   `mcp_egress_allowlist` has zero rows for it.
2. `GET /api/projects/{pid}/mcp/egress-allowlist` (`mcp.py:129-137`) → `[]`; the UI shows
   "No hostnames allowlisted" (`en.json:542`).
3. Upload a Tavily search key. `search_service.py:41` probes via `search_probes.py:59-75` —
   **direct httpx, no proxy** — and returns `ProbeStatus.OK`. The UI shows the key as
   tested-good.
4. Activate it (`search_service.py:142-173`). No allowlist write.
5. Create an agent (`agent_service.py:449`). The `HOSTED_WEB_SEARCH` row is inserted enabled
   (`repositories.py:632`, `agent_service.py:1053`). `GET /agents/{id}/tools` reports
   `config_warnings: []`, because `_function_warnings` early-returned at `agents.py:512`.
6. Ask the agent something requiring a live search. `web_search.py:115-154` finds the key,
   resolves the adapter, misses cache, **consumes a rate token at `:133-140`**, and calls
   `proxy.request` (`tavily.py:68-74`).
7. The proxy denies (`app.py:288-302`) and logs `egress_blocked_allowlist`.
8. The model reads
   `web_search failed: tavily returned HTTP 403: b'...mcp-egress-denied...'`.

## 5. Root Cause Analysis

**Root cause: `[R12.16]` asserts an invariant that has no writer.** The allowlist is a pure
write-on-demand table with no initialization path, while the tool depending on it is
provisioned enabled by default. Every link in §2 is a correct consequence of that single
absence.

**Aggravating factors, both newly identified:**

- The probe bypasses the proxy (`search_probes.py:23-92`), so the pre-flight check that would
  have caught this is structurally incapable of catching it, and its docstring claims
  otherwise.
- `_function_warnings` (`agents.py:509-528`) covers only `LOCAL_FUNCTION`, so the mechanism
  that already knows how to report an allowlist gap is excluded from the affected tool.
- The rate token is consumed before the denied egress (`web_search.py:133-140`), so the
  failure is not free.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every project that has not manually discovered the MCP egress-allowlist
screen — i.e. every new project. Nothing is persisted incorrectly; the defect is an absence.

| Claim | Location | Verdict |
|---|---|---|
| Allowlist seeded with four hostnames | `REQUIREMENTS.md:658` `[R12.16]` | **Confirmed defect** |
| Same claim restated in the design note | `docs/implement/E-agents-knowledge.md:283` | **Confirmed** — must be amended with the SRS |
| Admin seeded via bootstrap CLI or a `seed_admins` env list | `REQUIREMENTS.md:168` `[R5.01]` | **Cleared (partial).** No `seed_admins` env var exists anywhere in the repo, but the requirement is disjunctive and the CLI branch ships (`backend/smap/bootstrap/create_admin.py`). Not a defect; the wording could be tightened. See FU-1. |
| Every workspace has at least one chat room | `[R13.02]` | **Cleared** — genuinely provisioned (`backend/contexts/conversation/application/workspace_service.py:27,72,94`), and the delete path re-creates it (`chatroom_service.py:199`). |
| The E2E fixture project is seeded | `REQUIREMENTS.md:1936` `[R24.40]` | **Cleared** — `backend/app/bootstrap/seed.py:144-288` does exactly this, idempotently, gated to `app_env == "test"` (`:87-91`). Note it seeds **no** allowlist row, so the E2E fixture reproduces this bug rather than masking it. |
| `hosted_mcp` has the same first-use failure | — | **Confirmed, but not seedable.** The host comes from user-supplied `config.reference` (`agent_service.py:177-180`), so no seed can help. Worse, `_probe_mcp` (`agent_service.py:1010-1042`) — unlike `_probe_function` (`:976-978`) — does **not** pre-check the allowlist before calling the runner. That asymmetry is a small defect worth folding into this fix (§7). |
| `local_function` | `agents.py:509-528`, `agent_service.py:976-978`, `builtin_tools.py:649-651` | **Cleared — and this is the pattern to copy.** Warning on list, pre-check on probe, clean runtime message. |
| The search probe bypasses the proxy | `search_probes.py:23-92` vs its docstring at `:3-5` | **Confirmed (new)** — false-positive pre-flight |

## 7. Fix Design

Three coordinated parts.

**1. Seed one hostname on activation.** In `SearchKeyService.activate`
(`backend/contexts/keys/application/search_service.py:142-173`), after a successful
activation and inside the existing audit/side-effect block, upsert the single hostname for
`sk.provider` with `added_by_user_id=actor_user_id` and a note identifying it as
auto-added, emitting the existing `mcp.egress_allowlist_added` audit action
(`egress_allowlist_service.py:89`) with a `reason` discriminator.

- Go through `EgressAllowlistService.add` rather than the repository, so the seed is
  hostname-validated and audited exactly like a manual addition.
- **Never call `replace`** (`mcp_repositories.py:90-117`): it deletes every row for the
  project first and would destroy operator-added hosts. `upsert` (`:42-77`) is already
  `ON CONFLICT DO UPDATE ... RETURNING` and is safe to call repeatedly.
- **SoC**: this is a `contexts/keys` → `contexts/agents` write and must go through
  `contexts/agents/interfaces/facade.py`, not the repository. A new facade method is needed;
  the tenancy→agents edge that option (a) would have required is avoided entirely.

**2. Collapse the provider→hostname mapping to one authoritative map.** It is currently
triplicated — adapter endpoints (`tavily.py:19`, `brave.py:18`, `serper.py:18`,
`google_cse.py:19`), probe URLs (`search_probes.py:24,42,60,82`), and prose in
`REQUIREMENTS.md:658` / `E-agents-knowledge.md:283`. Adding a fourth copy in a migration
would be the worst possible outcome. Put the map beside `SearchProvider` in
`backend/contexts/keys/domain/search.py` and have adapters, probes and the seed all read it,
with a test asserting each adapter's endpoint host equals its map entry.

**3. Extend the warning channel** — `_function_warnings` (`agents.py:509-528`) becomes
`_tool_warnings` and gains a `HOSTED_WEB_SEARCH` branch (resolve the project's active search
key, map provider→hostname, check the allowlist) and a `HOSTED_MCP` branch (parse
`config.reference`'s host). Both must call `function_egress_allowed`
(`builtin_tools.py:490-502`), the single source of truth for host normalisation and lookup,
never re-parse URLs. Add the same `function_egress_allowed` pre-check to `_probe_mcp` so it
matches `_probe_function`. This is the safety net for the cases a seed cannot cover: an
operator who later removes the host, and every `hosted_mcp` binding.

**4. Correct the false guarantee** at `search_probes.py:3-5`, per Q-4.

**Backfill for existing deployments.** Derived, not a duplicated constant list:
`INSERT ... SELECT project_id, <host for provider> FROM search_keys WHERE is_active AND
deleted_at IS NULL`, with `ON CONFLICT (project_id, hostname) DO NOTHING` against
`uq_mcp_egress_allowlist_project_hostname` (`0014_mcp.py:37-38`). Same shape as the one
precedent in the tree, `0052_project_embedding_pins.py:112-116`.

- **Insert-only. Never `DELETE`.** Operator-added rows must survive byte-identical.
- Set `added_by_user_id = NULL` (nullable, `0014_mcp.py:32-33`) and
  `note = 'backfilled by migration NNNN (R12.16)'` so an auditing operator can distinguish
  platform rows from their own.
- `downgrade()` deletes **only** rows matching that exact note marker **and**
  `added_by_user_id IS NULL`. Any looser predicate risks operator rows. Because the marker is
  unique to the migration, downgrade stays safe even if the service-side seed has since added
  rows for the same hosts — those carry a different note and a non-null actor.
- Data-only, no schema change, so forward-compatibility is satisfied trivially.
- A project with no active search key gets nothing, which is correct: it cannot search anyway.

**Why this corrects rather than masks.** The absent state transition is given exactly one
owner, at the moment the project demonstrates intent to use the provider. The grant is one
host instead of four, attributable to a named actor, audited, inspectable in the UI, and
removable — and it stays removed, because nothing re-seeds outside the activation event.

## 8. Regression Test Plan

**The failing test comes first** —
`test_activate_adds_provider_host_to_egress_allowlist`: activate a Tavily key, assert the
allowlist facade received `api.tavily.com`. **Fails today**: `search_service.py:142-173` makes
no such call. (Confirm whether a `SearchKeyService` unit test file exists before creating
one.)

Then:

- `test_activate_is_idempotent_when_host_already_present` — pre-seed, activate, assert no
  duplicate and no error. Guards the `ON CONFLICT` path at `mcp_repositories.py:62-77`.
- `test_activate_does_not_add_other_providers_hosts` — **this is the test that encodes the
  security decision in Q-1**; it would fail under a naive four-host seed.
- `test_activate_fails_closed_when_allowlist_write_fails` — pins Q-5.
- Warnings (`backend/tests/unit/`, wherever `_function_warnings` is covered):
  `test_web_search_tool_warns_when_provider_host_not_allowlisted` and
  `test_hosted_mcp_tool_warns_when_reference_host_not_allowlisted` — both **fail today**
  because `agents.py:512-513` early-returns for non-`LOCAL_FUNCTION`; plus a negative control
  asserting no warning when the host is present.
- `test_probe_mcp_reports_allowlist_miss_without_calling_runner` — **fails today**:
  `_probe_mcp` (`agent_service.py:1010-1030`) calls `runner.probe` unconditionally.
- `backend/tests/unit/test_egress_allowlist.py` (exists, `_FakeRepo`-based at `:37-96`) — add
  a clobber guard asserting the seeding path routes through `upsert` and never `replace`. The
  fake already records `replaced_with` at `:40`, so this is a one-line assertion.
- Migration test: (i) a project with an active Tavily key gains `api.tavily.com`; (ii) a
  pre-existing operator row with a custom note survives byte-identical; (iii) running
  `upgrade()` twice yields the same row count.
- Map consistency: each adapter's endpoint host equals the map entry for its provider (part 2
  of §7).

## 9. Risks and Rollback

| Risk | Severity | Mitigation |
|---|---|---|
| Backfill clobbers operator-added rows on staging | **High** | Insert-only, `ON CONFLICT DO NOTHING`, never `replace`. Pinned by migration test (ii) and the `_FakeRepo` clobber guard. |
| New `contexts/keys` → `contexts/agents` dependency violates SoC | Medium | Route through `AgentsFacade`. No lint catches this; the reviewer must. |
| Allowlist write failure rolls back the activation | Medium | Deliberate per Q-5. Do not copy the non-transactional publish at `search_service.py:173`. |
| Provider→hostname map drifts from adapter endpoints | Medium | Part 2 of §7 collapses the copies; the consistency test pins it. |
| Seeding perceived as widening the SSRF surface | Medium | One host, on demonstrated intent, audited, operator-removable. Argued in §10. |
| `hosted_mcp` warning is noisy | Low | Warning only, non-blocking; mirrors the accepted `local_function` behaviour (`agents.py:526-527`). |
| Migration slow on a large deployment | Low | At most one row per project, derived from `search_keys WHERE is_active`. |

**Rollback.** Code is additive with no schema dependency; reverting restores current
behaviour, and already-seeded rows remain harmlessly (they are valid entries, individually
removable). The migration's `downgrade()` is scoped to its own note marker plus a null actor.
For staging, capture
`SELECT project_id, hostname, note, added_by_user_id FROM mcp_egress_allowlist ORDER BY project_id, hostname`
before and after; the diff must contain only insertions, and only of migration-marked rows.

## 10. Security Considerations

This is an SSRF control. The proxy's defence stack, in order
(`backend/services/egress_proxy/app.py`): URL/scheme validation (`:250-263`), DNS resolution
plus per-IP blocklist screening with connect-time pinning to a pre-screened literal to close
rebinding (`:265-285`), **allowlist** (`:287-302`), inbound header stripping including
`Authorization` (`:304-315`), HMAC-verified upstream-auth injection (`:317-329`).

Must not weaken:

1. **Per-project.** Every seeded row carries a concrete `project_id`. No global rows.
2. **Exact hostname.** `is_allowed` compares `hostname == host.lower()` (`main.py:34`,
   `mcp_repositories.py:125`). The seed must not introduce a pattern-matching path.
3. **Data, not code.** The allowlist must stay fully inspectable and mutable by a Project
   Owner via `GET`/`DELETE` (`mcp.py:129-201`). This is the strongest argument against Q-3.
4. **Ordering.** The IP blocklist runs *before* the allowlist (`:274` before `:288`). A
   hostile DNS answer for a seeded host is still blocked. Do not move or short-circuit the
   allowlist check.
5. **Owner-only mutation.** `_require_owner` (`mcp.py:99-116`) gates mutations. Auto-seeding
   inherits its authorization from "this user activated a search key", itself a
   project-scoped privileged action — record `added_by_user_id = actor_user_id` so the trail
   attributes it.
6. **Every seed is audited**, so an allowlist entry never appears without a log line.

**Is seeding four hostnames an acceptable default grant?** In isolation, defensibly yes —
they are fixed, well-known, TLS-only third-party APIs, not attacker-controlled, still screened
by the IP blocklist, and reachable only from an authenticated agent turn in a project holding
a valid activated key. But it is still the wrong grant on least-privilege grounds, for the
reason in Q-1: a project can use exactly one at a time, so three of the four are standing
unused capability in every project on the platform — the kind of latent reach that becomes a
pivot when combined with a future bug elsewhere. This dossier grants a quarter of the egress
for the same user-visible outcome.

## 11. SRS Delta

**Not empty** — unusually for a bugfix, and deliberately. This dossier does not restore the
literal behaviour `[R12.16]` describes; it implements a narrower mechanism for the same
intent, and the requirement must say so before the code does. Per the contract, this delta is
applied to `REQUIREMENTS.md` at approval, not before.

Replace `REQUIREMENTS.md:658`:

> **[R12.16]** The Proxy's allowlist receives a provider's documented hostname automatically
> when a search key for that provider is activated for the Project — `api.search.brave.com`,
> `google.serper.dev`, `api.tavily.com`, or `www.googleapis.com` respectively. The entry is
> attributed to the activating user and audited as any manual addition is, and a Project Owner
> may remove it. Hostnames for `hosted_mcp` bindings are not added automatically, because they
> are operator-supplied; the agent tool list warns when a bound host is absent from the
> allowlist.

`docs/implement/E-agents-knowledge.md:283` restates the old text and must be amended to match
in the same change.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — `[R5.01]` (`REQUIREMENTS.md:168`) offers admin seeding "via bootstrap CLI or a
  `seed_admins` env list". No such env var exists anywhere in the repo. The requirement is
  disjunctive and the CLI branch ships, so this is not a defect, but the wording promises a
  mechanism that was never built and should be tightened.
- **FU-2** — The rate-limit token is consumed at `web_search.py:133-140` before the egress
  call, so denied searches still exhaust the project's quota. Independent of this fix and
  arguably correct (the limiter throttles attempts, not successes), but worth a deliberate
  decision.
- **FU-3** — `_probe_function` and `_probe_mcp` are asymmetric: only the former pre-checks the
  allowlist. Folded into this fix (§7 part 3), recorded here so the asymmetry is not
  reintroduced.
- **FU-4** — `backend/app/bootstrap/seed.py` creates the E2E fixture project and agent with no
  allowlist row and no search key, so the fixture is a standing reproduction of this bug. If
  an E2E ever asserts `web_search` works, it must seed a key; otherwise leave it as-is.
</content>
