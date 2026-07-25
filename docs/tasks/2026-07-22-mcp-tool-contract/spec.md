---
type: bugfix
status: implemented
created: 2026-07-22
requirements: []
depends_on: []
---

# MCP bindings store opaque strings, so tools are advertised without a schema and a bad name bricks the agent

## 1. Summary

Two confirmed defects with **one shared root cause**: the MCP binding stores a list of opaque
strings and never negotiates a tool contract at bind time, while its sibling `local_function`
negotiates a full one.

- **A (F-14)** — every MCP tool is advertised to the model with
  `{"type": "object", "additionalProperties": true}` and a description carrying no parameter
  hints, so the model must guess argument names. The upstream truth arrives in the same
  `tools/list` response and is discarded at the source.
- **B (F-12)** — `allowed_tools` entries are unvalidated for length and charset. The composed
  runtime name `mcp__{id[:8]}__{tool}` can exceed the 64-character function-name limit both
  OpenAI and Anthropic enforce, or contain `.` or `:` — both legal MCP and common in the wild.
  Because `tool_specs` is computed once and attached to **every round of every turn**, and a
  deterministic 400 is classified non-retryable, one bad entry disables the agent entirely.

The asymmetry is visible in a single file. `_validate_function_config`
(`backend/contexts/agents/application/agent_service.py:98-171`) requires a **name** matching
`_FUNCTION_NAME_RE = ^[a-z0-9_]{1,64}$` (`:76`, applied `:100`), a **description** (`:105-107`),
and a **parameters** JSON Schema with a 10 KB cap, a 50-property cap and a `$ref` ban
(`:109-121`). `_validate_mcp_config` (`:174-193`) requires `source`, `reference`, and that
`allowed_tools` is a list of ≤200 non-empty strings — and nothing else.

**"Capture the server's tool contract at bind time" is a single change that fixes both**, and
gives B a stronger fix than a regex: validating against the authoritative name set rejects
nonexistent tool names too, not merely illegal ones. They are not fully identical — capture
alone does not help a name that is legal MCP but illegal as a provider function name, so B also
needs a name-legality decision (§3 Q-3).

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-14 and F-12 (both major,
both confirmed).

## 2. Observed vs Expected

**The schema is discarded at the source, not lost in transit.**
`deploy/sandbox/driver/driver.py:181` keeps
`names = [str(t.get("name", "")) for t in result.get("tools", [])]`, and the stdio branch at
`:184-186` does the same. `frame_tools` (`deploy/sandbox/driver/protocol.py:150-152`) serialises
`{"tools": [name, ...]}`, and the host re-reads names only at
`backend/contexts/agents/infrastructure/sandbox/docker_runsc.py:917`. The stored config has
nowhere to put a schema (`agent_service.py:174-193`), so at turn time
`backend/contexts/agents/application/runtime/builtin_tools.py:590-595` hardcodes the permissive
schema and a fixed description. There is **no turn-time schema fetch** — the only `tools/list`
call site in the repo is the probe. Adapters emit it verbatim
(`backend/contexts/keys/infrastructure/adapters/openai.py:140`, `anthropic.py:160`,
`gemini.py:102`).

**The name flows unchecked.** `agent_service.py:174-193` checks only list-ness, ≤200 entries,
and non-empty `str`. The API boundary does not compensate:
`backend/app/api/v1/agents.py:451,473` type `config` as `BoundedConfig`
(`backend/shared_kernel/validation.py:90` — a size and shape bound only), so a 200-character
name with spaces passes. Composition at `builtin_tools.py:551-552` prepends a 14-character
`mcp__{8}__`, so any upstream name over 50 characters overflows. No sanitisation downstream:
`tool_registry.py:139-145` passes the name through, and the adapters emit it verbatim.

**Expected.** A bound MCP tool is advertised with the parameter schema its server declares, and
a binding that cannot produce a provider-legal tool name is rejected at bind time rather than
breaking every turn.

**Intent source.** `requirements: []` is a positive claim — no `[Rxx.yy]` governs the MCP
binding contract. The expectation rests on internal inconsistency with `local_function`, which
enforces exactly these constraints one function above in the same file.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where should the schema be captured — probe time, or turn time? | **Probe time, persisted server-side, refreshed explicitly.** | The probe already exists, already pays for the sandbox round trip, and already backs a user-facing "Test" action (`agent_service.py:1010-1042` → `docker_runsc.py:824-858` → `useToolTest.ts:36-47`). Turn-time `tools/list` would spin a gVisor container (`docker_runsc.py:876-909`) inside `_get_semaphore()` on the hot path of **every** turn, fully serial before the first provider call since `tool_specs` is computed once at `turn_engine.py:2649`. Prohibitive. A Redis-cached turn-time fetch is the same thing with an implicit refresh instead of an explicit one; defer it. |
| Q-2 | Where should the captured schema be stored? | **A new server-written column, not `config`.** | This is the load-bearing constraint. `BoundedConfig` caps at **16 KB, depth 12, 500 nodes**, counting every dict key and list element as a node. One modest tool schema is roughly 25-40 nodes, so ~12 tools sit at the ceiling *before* `source`/`reference`/`allowed_tools`/sealed `auth` are counted — and the allowlist permits 200 entries. `BoundedConfig` therefore does **not** accommodate a realistic multi-tool server. A server-written column is never parsed through `AgentToolCreateIn`/`AgentToolPatchIn`, so the cap correctly does not apply and the client cannot inject a schema. Apply an independent service-layer cap instead (§7). Next free migration number is **0062** (latest on disk is `0061_graphrag_owner_index_live_only.py`). |
| Q-3 | For B: reject at bind time, sanitise at composition time, or both? | **Both. Neither is sufficient alone.** | Reject-only makes legitimate MCP servers unbindable — `.` and `:` are legal MCP and common, and a 51-character upstream name is legal MCP but unusable after the prefix. Sanitise-only is silent: an operator who typed a nonexistent tool name gets no feedback, and two upstream names that sanitise identically collide, at which point `ToolRegistry.__init__` (`tool_registry.py:159-164`) drops one with **only a log line**, so a tool vanishes with no user-visible signal. |
| Q-4 | Does sanitising the advertised name break invocation? | **No — and this removes the largest apparent cost of Q-3.** No mapping table is needed. | `_build_mcp_tool_from_agent_tool` closes over the real upstream name in `_invoke` (`builtin_tools.py:563-588`) and passes it as `tool_name=mcp_tool` (`:576`), which becomes `SMAP_TOOL_NAME` (`docker_runsc.py:942`), read by the driver at `deploy/sandbox/driver/driver.py:198` and sent as the JSON-RPC `params.name` (`:203`). `Tool.name` (`tool_registry.py:134`) is used **only** as the registry dict key and in the provider spec. The only invariant to preserve is **uniqueness of the sanitised name within one turn's registry**. |
| Q-5 | How should retroactive validation treat existing rows? | **Grandfather: validate only entries the caller is adding.** | `patch_tool` re-validates the merged config on every edit (`agent_service.py:822-838`), so a newly strict validator would 422 an operator editing an unrelated field on a legacy row. **There is precedent for exactly this hazard**: `allow_empty_allowlist` (`:174,188-189,831-834`) exists because a migration backfilled `[]` and legacy rows started 422-ing on every edit. Follow that precedent rather than repeat the incident. `add_tool` (`:745-746`) has no legacy set and enforces fully. |
| Q-6 | Should stored `allowed_tools` be rewritten to the sanitised form? | **No backfill.** | The stored value should stay the upstream truth, and rewriting it would break the closure that carries the real name to `SMAP_TOOL_NAME` (Q-4). Composition-time sanitisation means legacy rows keep working regardless — grandfathering is about not blocking edits, not about correctness. |
| Q-7 | What happens when a re-probe no longer lists a previously-bound tool? | **Do not silently drop it.** Surface it as an advisory. | A transient upstream error must not be able to disable a working binding. Use the existing `config_warnings` channel (§7). |
| Q-8 | Does this depend on any open dossier, or overlap the a2a orchestration audit? | No. `depends_on: []`. | Checked against `BOARD.md`. Note `docs/tasks/2026-07-22-tool-dispatch-failure-categories/` records as its FU-3 that MCP tools cannot benefit from its new schema-validation gate until this dossier lands — a **logical** relationship in that direction, not a build-order dependency for this one. |

## 4. Reproduction

**A.** Bind a `hosted_mcp` tool:
`POST /api/agents/{id}/tools` with
`{"tool_type":"hosted_mcp","config":{"source":"package","reference":"npx:@modelcontextprotocol/server-filesystem","allowed_tools":["read_file"]}}`.
Start a turn needing it and inspect `payload["tools"]` (`turn_engine.py:2660-2661`): the entry
carries `input_schema: {"type":"object","additionalProperties":true}` and the description
`"MCP tool 'read_file' from bound server npx:@modelcontextprotocol/server-filesystem."`.
Unit-level, with no provider: `bt.build_agent_tools(...)` per the existing harness at
`backend/tests/unit/test_builtin_tools_wiring.py:63-72,108-118`, then assert on
`tool.input_schema` and `tool.description`. **What the user sees**: the model calls `read_file`
with invented argument names, the server rejects, the failure returns as text
(`builtin_tools.py:587-588`), and the model burns rounds guessing — up to 8
(`turn_engine.py:96`).

**B — a concrete bricking value.**

```json
{"tool_type": "hosted_mcp",
 "config": {"source": "url", "reference": "https://mcp.example.com",
            "allowed_tools": ["filesystem.read_file"]}}
```

Accepted today: a list, one entry, a non-empty `str` (`agent_service.py:181-193`), and a tiny
object to `BoundedConfig` (`agents.py:451`). Composed: `mcp__1a2b3c4d__filesystem.read_file` —
the `.` is outside `^[a-zA-Z0-9_-]{1,64}$`. A length-only trigger, for a test independent of
charset rules: any name ≥51 characters.

**What the user sees, and why the diagnosis misleads.** Every turn for that agent dies, not just
calls to that tool, because `tool_specs` is computed once at `turn_engine.py:2649` and attached
at `:2660-2661` to every round of every turn. The provider 400 classifies as
`RotationReason.ABORT` (`backend/contexts/keys/application/router_policy.py:26,55-56` —
`_ABORT_STATUSES = {400, 404, 422}`), so `provider_router.py:379-383` raises
`KeyGroupExhausted(reason="request_rejected")` **without rotating**. `turn_engine.py:2942-2947`
maps that to `provider_exhausted:request_rejected`, emitted to the room at `:2228-2230` and
audited as `agent.turn_failed` at `:2238`. Sibling keys are not burned — but **the surfaced
string names the key group, not the tool**, so the operator's diagnosis points at their API
keys. That misdirection is the real user harm and belongs in the acceptance criteria.

## 5. Root Cause Analysis

**Shared root cause: the MCP binding negotiates no tool contract at bind time.** The stored
config carries exactly one datum per tool — a string — so:

- **A** follows because there is no field to put a schema in, and the upstream schema is
  discarded at `driver.py:181` before it can reach one.
- **B** follows because there is no name contract, so the string flows unchecked from
  `agent_service.py:190-193` through `builtin_tools.py:551-552` to the adapters.

Both halves of the upstream truth — name *and* `inputSchema` — arrive in the same `tools/list`
response and are thrown away in the same expression.

**Not identical**: capture confirms a name exists but does not make it provider-legal. B needs
Q-3's name-legality decision layered on top.

## 6. Blast Radius and Sibling Suspects

| Suspect | Status |
|---|---|
| `local_function` — schema gap | **Cleared.** `parameters` is required, validated as a `type:"object"` schema, size/property/`$ref`-capped (`agent_service.py:109-121`), and passed to the provider (`builtin_tools.py:609,673`). The permissive default at `:609` is unreachable for stored rows, since validation runs on both `add_tool` (`:748-749`) and `patch_tool` (`:828-829`). |
| Any other tool type advertised permissively | **Cleared — MCP is the only one.** Exhaustive sweep of `input_schema=` across `backend/`: `_UPDATE_WAKEUP_SCHEMA` (`tool_registry.py:229`), `_CAST_APPROVAL_VOTE_SCHEMA` (`:297`), `_READ_SKILL_SCHEMA` (`:555`), `_WEB_SEARCH_SCHEMA` (`builtin_tools.py:160`), `_CODE_EXEC_SCHEMA` (`:237`), `_FILE_SCHEMA` (`:396`), `_FILE_SEARCH_SCHEMA` (`:441`) — all real named schemas. Only `builtin_tools.py:593` is permissive. |
| Built-in tool names vs the 64-char/charset limit | **Cleared.** All seven of `BUILTIN_TOOL_NAMES` (`tool_registry.py:111-121`) are short `[a-z_]`, with a standing drift guard at `test_builtin_tools_wiring.py:121-136`. |
| `local_function` names vs the limit | **Cleared** — `_FUNCTION_NAME_RE` is exactly the provider constraint, plus a reserved-name and `mcp__`-prefix guard (`agent_service.py:84-85,102-103`). |
| Skill-derived tools | **Cleared.** Skills are not exposed as individual provider tools — they are reached through the single fixed `read_skill` tool (`tool_registry.py:302,555`), so a skill name is an *argument*, never a tool name, and the hyphens its naming pattern permits never reach a provider `name` field. `skills.allowed_tools` is an unrelated concept. |
| Duplicate-name collision from sanitisation | **Confirmed as a fix-design constraint, not a current defect.** `ToolRegistry.__init__` (`tool_registry.py:159-164`) drops duplicates with a `logger.warning` only. |

Two MCP bindings on one agent cannot collide today, since the name embeds `str(tool.id)[:8]`.
Within one binding, two upstream names differing only past a truncation point would collide —
hence the digest suffix in §7.

## 7. Fix Design

Three independently revertible pieces; §9 sequences them.

**1. Widen the probe to carry the contract.** `deploy/sandbox/driver/driver.py:181,184-186` and
`frame_tools` (`protocol.py:150-152`) emit objects carrying `name`, `description` and
`inputSchema`; `docker_runsc._run_mcp_probe` (`:915-919`) parses **both** the new object form
and the legacy bare-string form. The legacy branch matters: a deployed `mcp_image` may lag the
backend, and `:918-919` currently raises `RuntimeError` on anything it cannot read. Add
`tools: tuple[McpToolSpec, ...]` to `ToolProbeResult`
(`backend/contexts/agents/domain/models.py:210-217`) **alongside** the existing `tool_names`,
which is consumed by the API and by `frontend/src/slices/agents/api/index.ts:326-333` and
`useToolTest.ts:43` — changing its element type would break the frontend contract and
`gen:api`.

**2. Persist and use the contract.** New JSONB column (migration 0062, pure DDL) holding the
captured specs plus a `captured_at`. Validate before persisting with the caps `local_function`
already uses — reuse `_has_ref_key` (`agent_service.py:88-95`), the 10 KB cap and the
50-property cap (`:114-119`) **verbatim**; they exist and are tested. At turn time,
`builtin_tools.py:590-595` reads the stored schema and the stored description instead of
hardcoding both — **fixing the description is half of defect A**. Make "Test" a re-capture
rather than a read-only probe, surface staleness through `config_warnings`, and per Q-7 never
silently drop a tool missing from a re-probe.

Schema drift after binding degrades to today's behaviour — the model guesses, the server errors
(`builtin_tools.py:587-588`), and `MAX_TOOL_ROUNDS = 8` allows retry — so drift is strictly no
worse than the status quo. Design for drift to be *detectable and repairable*, not prevented.

**3. Validate and sanitise the name.** At bind time (`_validate_mcp_config`,
`agent_service.py:174-193`): reject entries over a hard length cap, containing control
characters or whitespace, or that cannot be sanitised to a unique legal name; once capture
exists, **warn — not reject** — on names absent from the last capture. Keep it a `ValueError`,
which the existing mapper turns into the 422 the frontend already renders for
`config.allowed_tools` (`frontend/src/slices/agents/__tests__/AgentToolsView.test.ts:211`).
Per Q-5, validate only newly-added entries on patch.

At composition time (`builtin_tools.py:551-552`): sanitise unconditionally as the backstop, so
pre-existing rows and any future bypass cannot brick a turn. Map illegal characters to `_`; when
the result exceeds 64 characters, truncate and append a short deterministic digest of the
original to preserve uniqueness. **Resolve collisions deterministically**, never leaving them to
the registry's silent first-wins drop. Preserve the `mcp__` prefix through sanitisation — it is
what makes it structurally impossible to sanitise into a reserved built-in name — and add the
drift test.

**Detection query**, to size the affected population before shipping (50 = 64 minus the
14-character prefix):

```sql
SELECT t.id, t.agent_id, a.project_id, elem AS tool_name
FROM agent_tools t
JOIN agents a ON a.id = t.agent_id
CROSS JOIN LATERAL jsonb_array_elements_text(t.config -> 'allowed_tools') AS elem
WHERE t.tool_type = 'hosted_mcp'
  AND (length(elem) > 50 OR elem !~ '^[a-zA-Z0-9_-]+$');
```

**Surface, do not silence.** Generalise `_function_warnings`
(`backend/app/api/v1/agents.py:509-528`) to `_tool_warnings` with an MCP branch: names that had
to be rewritten, names absent from the last capture, and a stale-capture notice. **This is the
single best reuse in the whole fix** — a non-blocking advisory path that already exists, is
already wired end to end through `AgentToolOut.config_warnings` (`agents.py:439,504`) to
`AgentToolsView`, and is already the right UX for "your config is legal but degraded".

**Schema backfill is a no-op by construction**: the column starts NULL and NULL degrades to
today's permissive schema; population happens on the next probe. Prefer a one-shot admin/CLI
re-probe (`backend/smap/` already hosts CLI tools) over a data migration — a backfill would have
to spin gVisor containers from inside Alembic.

## 8. Regression Test Plan

**`backend/tests/unit/test_agent_service.py`** — extend `TestValidateMcpConfig` (`:822-843`),
which today covers only source/reference/empty-list/non-empty-string.

**The failing test comes first** — `test_rejects_illegal_charset_entry`: `"filesystem.read_file"`
raises `ValueError` matching `allowed_tools`. **Fails today**: `:192` checks only
`isinstance(name, str) and name`.

Then:

- `test_rejects_overlong_allowed_tool_entry` — a 51+ character entry raises.
- `test_patch_grandfathers_preexisting_illegal_entry` — patching an unrelated field on a row
  whose stored `allowed_tools` already contains `"a.b"` succeeds. **Guards against
  re-introducing the `allow_empty_allowlist` regression class** (Q-5).
- `test_patch_rejects_newly_added_illegal_entry`.
- `test_captured_schema_rejects_ref` and `_oversize` — the capture validator reuses
  `_has_ref_key` and the 10 KB cap.

**`backend/tests/unit/test_builtin_tools_wiring.py`** — extend around `_mcp` (`:63-72`) and
`test_assembles_singletons_plus_mcp_tools` (`:108-118`):

- `test_mcp_tool_advertises_captured_schema` — with a stored contract, `tool.input_schema`
  equals it and `tool.description` carries the upstream description. **Fails today**: `:593` is
  a literal and `:592` a fixed f-string.
- `test_mcp_tool_falls_back_when_schema_absent` — NULL capture still yields a working tool, no
  crash, no dropped tool.
- `test_mcp_advertised_name_is_provider_legal` — for
  `allowed_tools=["a"*80, "fs.read_file", "ns:tool"]`, every emitted `tool.name` matches
  `^[a-zA-Z0-9_-]{1,64}$`. **Fails today**: `:551-552` is a bare f-string.
- `test_mcp_sanitised_names_stay_unique` — two upstream names sharing a 50-character prefix
  produce two distinct registry entries. **Fails today** under a naive truncate; guards the
  silent-drop hazard at `tool_registry.py:161-164`.
- `test_mcp_invoke_uses_the_real_upstream_name` — invoke the sanitised-name tool through a fake
  runner and assert `tool_name` received is the **unsanitised** original. This is the round-trip
  guard for the whole sanitise design (Q-4); **it passes today and must keep passing**.
- A drift test asserting sanitisation can never produce a name in `BUILTIN_TOOL_NAMES`, in the
  style of `:121-136`.

**Sandbox driver** — assert `frame_tools` emits objects carrying `inputSchema`, and that
`_run_mcp_probe` parses both the new object form and the legacy bare-string form.

**Frontend** — `AgentToolsView.test.ts` already exercises the 422 `field_errors` path (`:205-216`)
and MCP test results (`:239`); add a `config_warnings` rendering case for a rewritten or stale
name. Re-run `pnpm run gen:api` if `ToolProbeResult` gains a field.

## 9. Risks and Rollback

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Advertised names change for existing bindings, invalidating provider prompt-caching keyed on the tool block | high | one-off cost | Sanitisation is identity for already-legal names — only non-conforming names change. Size it with the §7 detection query, which should return few rows |
| Sanitised-name collision silently drops a tool | medium | one tool disappears | Digest suffix, explicit uniqueness test, and surface via `config_warnings` |
| `mcp_image` / backend version skew in either direction | medium | probe raises `RuntimeError` (`docker_runsc.py:918-919`), "Test" breaks | Parser accepts both forms; driver frame is additive. **Ship driver-first** |
| Retroactive validation 422s legacy edits | medium — **this exact regression already happened once** | operators cannot edit bindings | Delta-only validation on patch (Q-5), the `allow_empty_allowlist` precedent, and the grandfathering test |
| Oversized capture from a large or hostile server | medium | row bloat, node-cap breaches | Per-tool and total caps enforced before persist; stored outside `config` so `BoundedConfig` is not the (insufficient) gate |
| Stale schema misleads the model worse than no schema | low | wasted tool rounds | Strictly bounded by today's behaviour; staleness advisory; "Test" re-captures |

**Security.** The fix widens what crosses the sandbox boundary and what is interpolated into a
provider payload. Four things must not weaken:

1. **Sealed auth stays fail-closed.** `resolve_tool_auth` and the `unsealable` branch
   (`builtin_tools.py:564-571`, mirrored at `:620-627` and in both probes at
   `agent_service.py:980-982,1016-1018`) return an error rather than proceeding with no
   credential. Capture runs through the same probe path and must keep that ordering:
   unsealable ⇒ abort, **never** "probe anonymously and cache a schema". A schema captured with
   credentials may differ from one captured without.
2. **The captured schema is untrusted input from a third party.** It arrives as JSON from an
   external server, crosses the sandbox boundary via container stdout, is persisted, and is then
   placed in a prompt-adjacent field the model reads. Enforce a size cap **before** persisting —
   a malicious server can return megabytes, and `docker_runsc.py:906-907` reads container logs
   unbounded. Ban `$ref` (reuse `_has_ref_key`) so no schema can induce a fetch or a parser
   loop. And recognise that the captured **description is a prompt-injection vector**: bound its
   length and strip control characters. `_validate_function_config` caps descriptions at 1000
   characters (`:106-107`) — match that.
3. **The egress allowlist path is untouched and must stay so.** Capture reuses the existing
   probe, so `url`-source traffic still goes through the egress proxy with HMAC headers
   (`deploy/sandbox/driver/driver.py:141-158`) and denial still surfaces as exit 42 →
   `McpEgressDenied` (`docker_runsc.py:910-911`). **Do not add a host-side HTTP fetch of
   `tools/list` as a "cheaper" capture** — that would bypass the proxy entirely.
4. **Name sanitisation is a defence, not only a bugfix.** Today an operator-supplied string is
   interpolated into a provider request body with no charset restriction. Constraining it closes
   an injection-adjacent surface. Correspondingly, the reserved-name guard must extend to the
   sanitised output.

Multi-tenant AuthZ is unaffected: capture is stored per `agent_tools` row, project-scoped
through `agent_id`, and the list endpoint already enforces project membership
(`agents.py:543-547`).

**Rollback**, three independently revertible pieces, which is also the shipping order:

1. **Driver + probe widening** — additive, no behaviour change alone. Revert is clean; the old
   name-only path stays exercised by the fallback parser.
2. **Migration 0062 + capture/persist + turn-time read** — revert requires only reverting code;
   the NULL column is inert and `alembic downgrade -1` is a plain column drop with no data
   dependency. Code-only rollback is safe (forward-compatible per the backend migration rule).
3. **Name validation + sanitisation** — pure code, no data written; revert restores today's
   exact composed names. **This piece is independently shippable and is the one that stops a
   live agent being bricked**, so ship it first if the capture work needs more design time.

No user data is rewritten anywhere in the plan, which is what makes every step cleanly
reversible.

## 10. Acceptance Criteria

- [x] AC-1: `test_rejects_illegal_charset_entry` (§8) fails against current code and passes
      after the fix. (Value changed from the spec's `"filesystem.read_file"` to a real
      control-character example — see D-2.)
- [x] AC-2: every advertised MCP tool name matches `^[a-zA-Z0-9_-]{1,64}$`, whatever the stored
      `allowed_tools` contains — including legacy rows that predate validation.
- [x] AC-3: sanitised names are unique within a turn's registry; no tool is silently dropped.
- [x] AC-4: invoking a sanitised-name tool sends the **unsanitised** upstream name to the MCP
      server.
- [x] AC-5: a bound MCP tool is advertised with the parameter schema and description its server
      declared; absent a capture it falls back to today's permissive schema without error.
- [x] AC-6: a captured schema exceeding the size, property or `$ref` limits is rejected before
      persisting, and a captured description is length-bounded.
- [x] AC-7: adding an illegal entry to an existing binding is rejected; editing an unrelated
      field on a binding whose stored entries are already illegal is **not**.
- [x] AC-8: a tool absent from a re-probe is surfaced as a warning, not dropped.
- [x] AC-9: the probe parses both the new object form and the legacy bare-string form.
- [x] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm run check:openapi-drift` pass in
      `frontend/`. (`check:openapi-drift`'s bash wrapper could not resolve `python` in this
      environment's shell; verified by running its two steps manually instead — see D-5.)

## 11. SRS Delta

None. No `[Rxx.yy]` governs the MCP binding contract; this brings it to parity with
`local_function`, which enforces the same constraints in the same file. See FU-1.

## 12. Deviation Log

- **D-1** — Migration renumbered from the spec's **0062** to **0066**. 0062–0065 were taken by
  other work (`workflow_run_participants`, `auth_identities`, `egress_allowlist_seed_backfill`,
  `activity_agent_visibility`) that landed on `main` between spec approval (2026-07-22) and
  implementation (2026-07-25). No design impact — the migration is pure DDL either way. Confirmed
  via `git log`/directory listing before implementation started (build skill Step 2 freshness
  check); BOARD.md's Ready-now row updated to match.
- **D-2** — §8's `test_rejects_illegal_charset_entry` named `"filesystem.read_file"` as the
  example that should raise at bind time. That directly contradicts Q-3's own decision (`.` and
  `:` are legal MCP and must not be bind-time-rejected) and the sibling composition-time test
  (`test_mcp_advertised_name_is_provider_legal`), which uses the near-identical `"fs.read_file"`
  as an example that must **not** raise, only sanitise. Judged a copy-paste slip in §8 (it likely
  reused reproduction-B's example string without re-checking it against Q-3, decided later in the
  same document). Raised to the user before implementing M3; resolved in favour of Q-3 — bind-time
  rejects control characters/whitespace/overlong names only, never `.`/`:`. The regression test
  was rewritten to use an actual illegal-charset value (`"read\x00file"}`); AC-1 is satisfied by
  the rewritten test, not the spec's literal string.
- **D-3** — Fix Design §7 piece 3 called for "Generalise `_function_warnings` to `_tool_warnings`
  with an MCP branch." `_tool_warnings` (`app/api/v1/agents.py`) was already generalised beyond
  function-only — including an MCP egress-allowlist branch — by unrelated work that landed after
  the spec was written (its docstring cites "R12.16"). No rename was needed; only the two new
  MCP capture-staleness warnings (never-captured, absent-from-last-capture) were added to the
  existing MCP branch.
- **D-4** — After M1–M3 landed, a quality + security audit pass (9 parallel dimension-scoped
  reviews) surfaced two HIGH-severity findings beyond the approved design and two related
  quality warnings; fixed in a fourth milestone with the user's explicit agreement rather than
  deferred, since both HIGH findings sit squarely in this task's own threat model (a hostile or
  misconfigured MCP server):
  - `docker_runsc._run_mcp_probe` read a probe container's entire stdout via `container.logs()`
    with no byte cap, then `json.loads()`'d it outside the concurrency semaphore — unbounded
    memory/CPU from a hostile server, un-throttled by the 8-concurrent-container limit. Fixed
    with `_read_capped_logs` (5 MB, streamed) and by keeping status-check + JSON parsing inside
    the semaphore.
  - Captured `description` got control-character stripping, but strings embedded in the captured
    `inputSchema` (property descriptions, `enum`, `pattern`, ...) did not, despite being equally
    prompt-adjacent third-party text. Fixed with `_sanitize_schema_strings`, applied recursively
    to schema values only (never keys).
  - (Folded in, same pass) the 200 KB total-byte cap only counted schema bytes, not
    name/description; a captured tool's `name` had no length bound at all. Both fixed in
    `_sanitize_captured_tools`.
  A DRY finding (the schema cap's "verbatim reuse" claim is actually copy-pasted constants) and
  an SoC finding (`_tool_warnings`' new MCP branch deepens an existing domain-object-in-API-layer
  leak) were judged lower-severity and deferred — see FU-5/FU-6.
- **D-5** — `frontend/scripts/check-openapi-drift.sh` invokes `python -m scripts.export_openapi`
  from a bash subshell in which `python` was not on `PATH` in this sandboxed environment (a
  local-shell-alias gap, not a real drift signal). Verified the same two steps manually instead:
  `python scripts/export_openapi.py` (backend) regenerated `openapi.json` with only the expected
  diff (the M2 `warnings` field), and `pnpm run gen:api` (frontend) regenerated only
  `AgentToolTestOut.ts` to match — `git status` showed no other drift. AC-10 is marked satisfied
  on that basis.

## 13. Follow-ups

- **FU-1** — No SRS entry defines what a `hosted_mcp` binding guarantees about the tools it
  exposes. `local_function`'s contract is enforced in code but likewise unstated.
- **FU-2** — `agent_service.py` uses function-local imports throughout the probe helpers
  (`:962-969`, `:1011-1013`) to break the skills↔agents cycle noted at `:209-213`. Follow the
  local pattern; do not add module-level imports.
- **FU-3** — The MCP invoke closure (`builtin_tools.py:563-588`) mixes auth resolution, sandbox
  invocation, audit and output formatting in one function. Adding schema handling should not
  grow it further — keep capture and validation in the service layer and the runtime builder a
  lookup.
- **FU-4** — `allow_empty_allowlist` (`agent_service.py:174,188-189`) documents in a comment
  that this exact surface has already shipped one retroactive-validation incident. Cited here as
  prior art; worth a short note in the module docstring so the next author finds it before
  repeating it.
- **FU-5** — `_tool_warnings`' HOSTED_MCP branch (`app/api/v1/agents.py`) reads `AgentTool`
  domain fields (`mcp_captured_at`, `mcp_captured_tools`) and iterates raw `McpToolSpec` objects
  directly inside the API layer, deepening a pre-existing abstraction leak in that function
  (`tool.config` was already touched there). Move the staleness computation into `AgentService`
  (e.g. a `tool_warnings()` method) or onto `AgentTool` as a domain method.
- **FU-6** — `_validate_captured_schema`'s size/property caps (`agent_service.py`) claim to reuse
  `_validate_function_config`'s local_function caps "verbatim," but only `_has_ref_key` is
  actually shared — the byte-size and property-count checks are independent copies of the same
  literals (`10_000` / `50`) with nothing enforcing they stay equal. Extract a shared
  `_validate_bounded_schema(schema, *, max_bytes, max_properties)` helper used by both.
- **FU-7** — `invoke_mcp_tool` (`docker_runsc.py`, the tool-*call* path, not probe) has the same
  uncapped `container.logs()` read that `_read_capped_logs` now fixes for the probe path (D-4).
  Lower risk today — its output flows through `clip_tool_output` and is never persisted or
  `json.loads`'d — but worth the same treatment for consistency and defense in depth.
- **FU-8** — No recursion/nesting-depth cap exists anywhere in the captured-schema validation
  pipeline, only a serialized-byte-size cap (`_MCP_CAPTURE_SCHEMA_MAX_BYTES`). A schema built
  from many thin nesting levels could stay under the byte cap while still being deep enough to
  stress `json.loads`'s recursive descent. Flagged MEDIUM/plausible by the security audit, not
  confirmed exploitable in this environment; worth a depth-walking check before persisting if it
  proves reachable.
</content>
