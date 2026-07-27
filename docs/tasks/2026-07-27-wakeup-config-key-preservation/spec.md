---
type: bugfix
status: implemented
created: 2026-07-27
requirements: [R15.06, R15.07, R15.08, R15.09]
depends_on: []
---

# wakeup_config keys are dropped by every UI save, taking the authored snapshot with them

## 1. Summary

From `docs/audits/2026-07-27-wakeup-subsystem/findings.md` F-1 (major, confirmed). The frontend
normalizes an agent's `wakeup_config` into a closed shape that has no `soft_bounds` member, then
sends that normalized object back as the PATCH payload. Because a `wakeup_config` PATCH by a human
actor also replaces `wakeup_authored_snapshot`
(`backend/contexts/agents/application/agent_service.py:755-761`), one unrelated edit in any of the
three UI surfaces erases a Platform Admin's designer soft bounds from both the live config and the
authored baseline. The agent's next self-modification then lands at the hard floor `N_MIN = 1`
instead of the designer's floor, and the hourly refresh restores a snapshot that no longer contains
the bounds, so the loss is permanent and unrecoverable. This is the F-12 defect class that
`docs/tasks/2026-07-22-wakeup-trigger-state-and-bounds/spec.md` closed on the self-modification
path, still open on the human-edit path — and specifically on the one path that also destroys the
recovery mechanism that dossier's §7 "Data repair position" relied on. User-visible impact: an agent
the admin bounded to `n >= 5` silently becomes free to wake on every single message, spending on the
user's own provider key.

## 2. Observed vs Expected

- **Observed**
  - `frontend/src/shared/types/workflow.ts:133-149` — `normalizeWakeupConfig` builds and returns a
    fresh object literal containing exactly `triggers`, `allow_self_open`, `refresh_every_hours`.
    Any other root key present in `raw` is read past and discarded. The `WakeupConfig` interface
    (`:47-60`) has no `soft_bounds` member and no root-level index signature, so TypeScript cannot
    flag the loss; the index signature at `:44` is on `WakeupTriggerConfig`, one level down.
  - All three save paths send that object verbatim:
    `frontend/src/slices/agents/views/AgentDetailView.vue:388` normalizes on load and `:417-431`
    clones the same object into the create/patch payload;
    `frontend/src/slices/workflow/views/AgentOrchestrationView.vue:37,42-54`;
    `frontend/src/slices/conversation/composables/useChatroomBindings.ts:55,149-175`.
  - `backend/contexts/agents/application/agent_service.py:755-756` replaces the whole `wakeup_config`
    JSONB column with the submitted dict, and `:760-761` additionally writes that same dict into
    `wakeup_authored_snapshot` whenever the actor is not the system actor.
  - `backend/contexts/orchestration/application/wakeup_service.py:292-293` then parses
    `soft_bounds` as absent and `_clamp_n` (`:462-466`) falls back to `N_MIN = 1`.
- **Expected** R15.08: "Platform Admin can also set soft per-agent bounds at creation time;
  self-modification must respect these", restated at `docs/implement/G-orchestration.md:98`.
  `docs/tasks/2026-07-22-wakeup-trigger-state-and-bounds/spec.md` Q-8 already decided the governing
  rule for this column — "the overlay makes the write additive rather than replacing" — and
  implemented it for the self-modification path
  (`wakeup_service.py:322-330`, `_overlay_config` at `:475-483`). The human-edit path must obey the
  same rule.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Fix at the client, at the server, or both? | Both. The server makes the `wakeup_config` write additive so no client can drop a key; the client stops discarding root keys it does not model. | The server fix is the invariant: SMAP is self-hosted with a documented REST API, so the browser is not the only writer, and a client-only fix leaves the same hole for the next consumer. The client fix is still needed because a passthrough is the honest behavior for a form that never showed the field — without it the editor round-trips a value it silently cannot represent, which is the shape of defect that produced this finding. |
| Q-2 | With an additive write, how does a designer remove a key? | Explicit `null` is a tombstone: a key present with value `null` in the submitted `wakeup_config` deletes that key from the stored column; an omitted key is left unchanged. | This is the convention the same endpoint already uses for scalar fields — `app/api/v1/agents.py:335-336` distinguishes "explicit null" from "omitted" for `model_id`, and `agent_service.py:705-752` acts on that distinction throughout. Reusing it costs no new concept. The rejected alternative, a `replace_wakeup_config: true` flag, adds an API-surface switch whose only correct use is rare and whose incorrect use reintroduces exactly this defect. |
| Q-3 | Does the additive rule also apply to `wakeup_authored_snapshot`? | Yes — the snapshot is written from the *merged* result, not from the submitted fragment. | The snapshot's contract (`agent_service.py:757-759`, "Human edit → update the authored snapshot") is "what the designer authored". After a merge, the merged dict *is* what the designer authored: their earlier keys plus this edit. Writing the fragment instead is precisely the mechanism that makes this defect unrecoverable. |
| Q-4 | Does the merge recurse into nested objects, or only the root? | Recurse, reusing the semantics of `WakeupService._overlay_config` (`wakeup_service.py:475-483`): dict-vs-dict merges, anything else replaces. | A root-only merge would still drop an unmodelled key nested inside `triggers.silence_minutes`, which is where every numeric field lives and therefore where the next unmodelled key is most likely to be added. Matching the already-shipped `_overlay_config` behavior also means one semantic to learn, not two. |
| Q-5 | Where does the merge live, given `_overlay_config` is a private static method in the orchestration context? | Promote it to a shared helper in `shared_kernel` and have both `AgentService.patch` and `WakeupService._build_new_dict` call it. | `shared_kernel` is imported by any context and imports from none (`backend/CLAUDE.md`), so it is the only place both an agents-context service and an orchestration-context service can legally share code. Duplicating the four-line recursion in two contexts is how the two copies drift; the audit's F-1 is a drift story already. |
| Q-6 | Does `AgentCreateIn` need the same treatment? | No. Create has nothing to merge against — `agent_service.py:568-593` writes the submitted config and mirrors it into the snapshot, which is correct. | The defect is loss of a *previously stored* value. §6 records this as CLEARED rather than omitting it. |

## 4. Reproduction

**Preconditions** a project with agent A, a Platform Admin token, and any project member able to
open the agent detail page.

1. `PATCH /api/agents/{A}` as an admin with
   `{"wakeup_config": {"triggers": {"every_n_messages": {"enabled": true, "n": 8}}, "soft_bounds": {"n_min": 5, "n_max": 10}}}`.
   Returns 200. `GET /api/agents/{A}` shows `soft_bounds` present in both `wakeup_config` and (via
   the DB) `wakeup_authored_snapshot`.
2. As any project member, open A in the agent detail page, change the system prompt by one
   character, and save.
3. `GET /api/agents/{A}`: `wakeup_config.soft_bounds` is gone.
   `SELECT wakeup_authored_snapshot FROM agents WHERE id = '{A}'` — also gone.
4. Have A call `update_wakeup(every_n_messages=1)` during a turn. It lands at 1, not 5, and no
   `agent.wakeup_clamped` audit row is emitted (`wakeup_service.py:301-307` records a clamp only
   when the value actually changed).
5. Wait for the hourly `wakeup_refresh` cron. The config is restored from a snapshot that no longer
   contains `soft_bounds`, so step 4 stays reproducible forever.

The same sequence reproduces from the chatroom settings wake-up editor
(`useChatroomBindings.ts:163`) and from the agent-orchestration page
(`AgentOrchestrationView.vue:46`); those two PATCH only `wakeup_config`, so step 2 needs only a
trigger toggle.

## 5. Root Cause Analysis

**Root cause: `AgentService.patch` treats `wakeup_config` as a whole-column replacement of a
free-form JSONB value, so the write can only preserve what the caller happened to send.**

Causal chain:

1. `app/api/v1/agents.py:89,123` types `wakeup_config` as `BoundedConfig`
   (`shared_kernel/validation.py:90`) — size-bounded, otherwise free-form. Any subset of keys is a
   valid payload, and the endpoint cannot tell a deliberate omission from an accidental one.
2. `agent_service.py:755-756` assigns the submitted dict to the column. **This is the earliest link
   whose correction prevents the symptom**: if the write merges, every downstream caller — including
   the three client paths and any third-party API consumer — becomes safe at once.
3. `agent_service.py:760-761` writes the same fragment into `wakeup_authored_snapshot`, converting a
   recoverable loss into an unrecoverable one. Aggravating factor, not the root cause: with link 2
   corrected, the snapshot is written from a merged dict and this link is harmless.
4. `frontend/src/shared/types/workflow.ts:133-149` is the producer that actually omits the key. It is
   a second instance of the same class rather than the root cause — the same payload could come from
   `curl`. It is in scope because Q-1 requires the editor to stop round-tripping a value it drops.

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- Every agent carrying designer `soft_bounds`, from the first human edit through any UI surface.
  Silent: the write emits a normal `agent.edited` audit row (`agent_service.py:778-788`) whose
  `fields` list says `wakeup_config` — indistinguishable from a legitimate wake-up edit.
- Any other root-level key a designer wrote into `wakeup_config` is lost by the same mechanism. The
  column is free-form by design, so this set is open-ended.
- Data already written: agents whose bounds were erased before this fix cannot be repaired from the
  database — neither column retains the value. See §7 "Data repair position".
- Not affected: the self-modification path (already additive since
  `2026-07-22-wakeup-trigger-state-and-bounds`), and the refresh path
  (`wakeup_service.py:396-399`), which writes a snapshot it did not construct.

**Sibling suspects**

Whole-column JSONB replacements reachable from a client payload:

- **CONFIRMED, in scope** `agent_service.py:764-765`, `workflow_capabilities`, is the same
  replacement shape on the same endpoint, also typed `BoundedConfig` (`agents.py:90,124`). The
  frontend builds it from a closed literal (`AgentDetailView.vue:424-429`), so an unmodelled key
  written by an admin is dropped identically. Folded into the same fix: the merge helper is applied
  to both columns, which is one extra line and removes the second instance of the pattern rather
  than leaving it armed.
- **CLEARED** `agent_service.py:568-593` (create). Nothing to merge against; see Q-6.
- **CLEARED** `wakeup_service.py:396-399` (`refresh_wakeup_config`). Writes
  `wakeup_authored_snapshot` back verbatim — a complete dict by construction, not a serializer
  output. This is the recovery path, not another instance.
- **CLEARED** `wakeup_service.py:322-330` (`update_wakeup`). Already overlays via `_overlay_config`;
  pinned by `tests/unit/test_wakeup_self_modification.py:17,59`.
- **CLEARED** `contexts/knowledge` RAG/GraphRAG config writes. Their API models are typed field by
  field, not free-form dicts, so an unmodelled key cannot exist to be dropped.

Client-side closed shapes over free-form server columns:

- **CONFIRMED, in scope** `frontend/src/shared/types/workflow.ts:47-60,133-149` — the finding.
- **CONFIRMED, in scope** `AgentDetailView.vue:424-429` builds `workflow_capabilities` from a closed
  literal, matching the backend sibling above.
- **CLEARED** `normalizeNestedTriggers` (`workflow.ts:89-97`) spreads the stored sub-object over the
  defaults, so unmodelled keys *inside* a trigger already survive. Only the root loses keys.

**Existing debt in the touched files** (record, do not silently fix): `AgentService` still combines
agent configuration, knowledge-binding reconciliation and tool CRUD — the wake-up dossier's FU-9.
`AgentService.patch` is a 90-line field-by-field `values` assembly (`agent_service.py:700-765`); this
change adds to it rather than restructuring it, because restructuring it is a refactor with its own
characterization-test cost and does not belong in a bugfix.

## 7. Fix Design

Three changes. C1 is the invariant; C2 and C3 remove the two producers that rely on it.

**C1, the merge helper and the additive write**
(`backend/shared_kernel/` new module, `backend/contexts/agents/application/agent_service.py`,
`backend/contexts/orchestration/application/wakeup_service.py`)

- Add `merge_json_config(stored, patch)` to `shared_kernel` (Q-5), with the semantics of
  `WakeupService._overlay_config` (`wakeup_service.py:475-483`) plus the Q-2 tombstone: dict-vs-dict
  recurses, a `null` value deletes the key, anything else replaces. Pure function, no I/O.
- `agent_service.py:755-761` becomes: merge `draft.wakeup_config` over `current.wakeup_config`
  (`current` is already loaded at `:647`), assign the merged dict, and — for a non-system actor —
  assign that same merged dict to `wakeup_authored_snapshot` (Q-3). Apply the same merge to
  `workflow_capabilities` at `:764-765` per §6.
- Replace `WakeupService._overlay_config` with a call to the shared helper, deleting the private
  copy so there is one implementation.

Why this corrects rather than masks: the symptom is "the browser drops a key", and the tempting fix
is to teach the browser about `soft_bounds`. That fixes today's key for today's client and leaves
the column's write semantics — "whatever you send replaces everything" — intact for the next key and
the next client. Making the write additive is the rule Q-8 of the prior dossier already chose for
this exact column; C1 applies it at the layer that owns the column.

**C2, client passthrough for unmodelled root keys**
(`frontend/src/shared/types/workflow.ts`)

- Add `soft_bounds?: Record<string, unknown>` to the `WakeupConfig` interface (`:47-60`) and a root
  index signature `[k: string]: unknown`, mirroring what `WakeupTriggerConfig` already does at `:44`.
- `normalizeWakeupConfig` (`:133-149`) spreads the unrecognized root keys of `raw` into its return
  value instead of dropping them: start from the passthrough keys, then set the three normalized
  fields over them. The `call_only` mutual-exclusivity correction at `:139-142` is unchanged.
- `DEFAULT_WAKEUP` (`:68-82`) gains nothing — `soft_bounds` has no default and must stay absent when
  the server has none, or C1 would merge an empty object over a stored one.

**C3, the editor payload**
(`frontend/src/slices/agents/views/AgentDetailView.vue`)

No change needed beyond C2: `assemblePayload` (`:417-431`) clones `wakeupConfig.value`, which after
C2 carries the passthrough keys. Verified, not assumed — the clone is `deepCloneJSON`, which
preserves unknown keys. The same is true of `AgentOrchestrationView.vue:46` and
`useChatroomBindings.ts:163`, which pass the normalized object directly.

**Data repair position (explicit).** No backfill migration, because no repair is possible: for an
agent whose bounds were erased, neither `wakeup_config` nor `wakeup_authored_snapshot` retains the
original value, and no audit row records it either (`agent.edited` stores only the changed field
*names*, `agent_service.py:786`). Ship a one-time operator query so the affected set is known rather
than assumed, and record its result in §12:
`SELECT id, project_id, name FROM agents WHERE deleted_at IS NULL AND wakeup_config ? 'triggers' AND NOT (wakeup_config ? 'soft_bounds');`
Every row is an agent that either never had bounds or lost them; the two are indistinguishable from
the database, which is the point.

## 8. Regression Test Plan

**T-1 (the failing test, write this first)**
`backend/tests/unit/test_agent_service.py::test_patch_merges_wakeup_config_instead_of_replacing_it`

Following the existing patch-test setup around `test_agent_service.py:600-630`. Stored
`wakeup_config` carries `soft_bounds: {n_min: 5}` and `designer_note: "x"`; patch with
`{"triggers": {"every_n_messages": {"n": 8}}}` as a *human* actor. Assert the captured `values`
dict has `wakeup_config` containing all three, and that `wakeup_authored_snapshot` equals the merged
dict, not the fragment (Q-3).

Fails today: `agent_service.py:756,761` assign `draft.wakeup_config` directly, so both columns become
the fragment.

**T-2** `backend/tests/unit/test_agent_service.py::test_explicit_null_deletes_a_wakeup_config_key`

Same setup; patch with `{"soft_bounds": null}`. Assert the stored config no longer has
`soft_bounds` and still has `designer_note`. Fails today for a different reason than T-1 — the whole
column is replaced by `{"soft_bounds": null}` — so this test pins the Q-2 semantics rather than
merely re-testing T-1.

**T-3** `backend/tests/unit/test_shared_json_merge.py` (new file)

Direct unit coverage of `merge_json_config`: nested dict-vs-dict recursion, scalar replacing dict,
dict replacing scalar, `null` tombstone at root and nested, empty patch is identity, and the stored
dict is not mutated in place. Fails today: the module does not exist.

**T-4** `backend/tests/unit/test_wakeup_self_modification.py`

The existing assertions at `:17,59` must keep passing with `_overlay_config` replaced by the shared
helper. Passes today and must pass after — this is the guard against the C1 refactor changing the
already-correct self-modification behavior.

**T-5** `backend/tests/unit/test_agent_service.py::test_patch_merges_workflow_capabilities`

The §6 sibling. Stored `workflow_capabilities` carries an unmodelled key; patch with a partial
capabilities dict; assert the unmodelled key survives. Fails today at `agent_service.py:765`.

**T-6** `frontend/src/shared/types/__tests__/workflow.test.ts`

Extend the file created by the prior dossier's T-10. Assert `normalizeWakeupConfig` on
`{triggers: {...}, soft_bounds: {n_min: 5}, designer_note: 'x'}` returns an object still carrying
both `soft_bounds` and `designer_note`, and that `defaultWakeupConfig()` has no `soft_bounds` key at
all. Fails today: `workflow.ts:144-148` returns a three-key literal.

**T-7** `frontend/src/slices/agents/__tests__/AgentDetailView.test.ts`

Load an agent whose `wakeup_config` carries `soft_bounds`, save without touching the wake-up editor,
and assert the intercepted PATCH body still contains it. This is the end-to-end statement of the
finding; T-6 alone would pass with a broken view. Fails today.

## 9. Risks and Rollback

- **C1 changes PATCH semantics for two free-form columns.** A client that today clears a key by
  omitting it will stop clearing it. The three in-repo clients never do this (they always send a
  full normalized object), and the documented way to clear becomes the Q-2 tombstone, but any
  external integration built against the replace semantics is affected. This belongs in the release
  note.
- **C1's tombstone makes `null` meaningful inside `wakeup_config`.** An agent whose config
  legitimately stores a `null` value for a key would have that key deleted on the next merge. No
  such key exists today — `WakeupConfig.to_dict` (`models.py:230-262`) never emits `null`, and
  `soft_bounds` entries are filtered on `is not None` (`:260`) — but it is a semantic the codebase
  did not previously carry. Note the interaction with
  `2026-07-27-wakeup-config-type-validation`, which will reject `null` for the *typed* numeric
  fields at the boundary; the tombstone therefore applies only to whole keys the typed model does
  not own.
- **C2 changes what the editor sends** for agents with unmodelled keys: more keys, not fewer. Since
  C1 merges, the outcome is identical either way; C2's real effect is that a client-only deployment
  (frontend updated, backend not) is also fixed.
- **Replacing `_overlay_config` touches shipped, tested behavior.** T-4 is the guard. Low risk: the
  helper's dict-vs-dict semantics are copied verbatim; only the tombstone branch is new, and
  `to_dict()` output contains no nulls, so `update_wakeup` never exercises it.
- **Rollback** C1, C2 and C3 are independently revertable. Reverting C1 while C2 is live is safe
  (the client sends more keys, the server replaces with them — which is the current behavior plus
  the preserved keys). Reverting C2 while C1 is live is safe (the server merges the missing keys back
  in). There is no ordering constraint.

**Merge adjacency, not a dependency**: `2026-07-27-wakeup-config-type-validation` declares this
dossier in its `depends_on` because its boundary model must permit the unmodelled root keys C1
preserves. Nothing in this dossier needs that one.

## 10. Acceptance Criteria

- [x] **AC-1** T-1 fails before the fix and passes after: a human `wakeup_config` PATCH preserves
      stored keys the payload omits, in both `wakeup_config` and `wakeup_authored_snapshot`.
      Verified failing first (whole-column replace), then passing.
- [x] **AC-2** T-2 passes: an explicit `null` deletes that key and only that key.
- [x] **AC-3** T-3 passes and `WakeupService._overlay_config` no longer exists — repo-wide grep
      returns `merge_json_config` only, called by both `AgentService` (`:778,793`) and
      `WakeupService` (`:325`).
- [x] **AC-4** T-4 passes unchanged: `update_wakeup` still preserves `soft_bounds` and unmodelled
      keys and still clamps to the designer floor on a second call.
- [x] **AC-5** T-5 passes: `workflow_capabilities` is merged on the same terms.
- [x] **AC-6** T-6 and T-7 pass: `normalizeWakeupConfig` round-trips unmodelled root keys, and an
      unrelated save from the agent detail page keeps `soft_bounds` in the PATCH body (T-7 drives
      the real component and asserts the intercepted wire payload).
- [x] **AC-7** The reproduction in §4 no longer reproduces. Verified against a real Postgres rather
      than by inference: `tests/integration/test_agent_config_merge_persistence.py` performs the
      editor-shaped human patch and asserts `soft_bounds` and `designer_note` survive in both
      columns after a JSONB round-trip. 3/3 pass inside `smap_backend_web`.
- [x] **AC-8** No data-repair migration is added. The §7 operator query is recorded in D-5 with its
      result and with the explicit caveat that the local result does not speak for production.
- [x] **AC-9** Definition of Done: backend `ruff check`/`ruff format --check` clean (859 files),
      `mypy .` clean (859 files, strict on `shared_kernel.*` covers the new helper), unit tier
      6,034 passed / 6 skipped after the `/code-review` fixes (D-10, D-11); the new integration
      tier passes 3/3 against real Postgres. Frontend
      Vitest 856 passed, `vue-tsc` clean, ESLint clean, Vite build succeeds. See D-6/D-7/D-8 for
      the environment caveats on the wiring tier, `gen:api`, and the `pnpm` wrappers.

## 11. SRS Delta

None. R15.08 is correct as written; the code diverged from it.

## 12. Deviation Log

- **D-1 (design correction found during implementation, 2026-07-27):** C1 as approved made
  *every* `wakeup_config` write additive, which broke the G.5 refresh. `refresh_wakeup_config`
  restores `wakeup_authored_snapshot`, and a snapshot is often a strict subset of the live config
  (`update_wakeup` persists a fully normalized dict via `to_dict()`, while the snapshot stays the
  partial human dict). Merged rather than replaced, the restored config keeps the drifted extra
  keys, so `current == authored` (`wakeup_service.py:392-394`) can never hold again and every
  later sweep refreshes and audits the same agent — forever. §6 had already asserted the refresh
  must write the snapshot verbatim; C1 silently broke that assertion. Fixed with an internal
  `AgentDraft.replace_wakeup_config` flag set only by `refresh_wakeup_config`, following the
  nine existing `clear_*` sentinels on the same dataclass. Not reachable from the API:
  `AgentPatchIn` is `extra="forbid"` and the router builds the draft field by field. Pinned by
  `test_replace_flag_restores_the_authored_snapshot_verbatim`,
  `test_refresh_asks_for_a_replacing_write_not_a_merge`, and the integration test's
  `test_refresh_restores_the_snapshot_and_then_converges`.
- **D-2 (security-gate correction, 2026-07-27):** `BoundedConfig` bounds the *request*
  (16 KB / depth 12 / 500 nodes), which bounded the stored column only while writes replaced it.
  Under C1's additive write, N bounded patches accumulate into an unbounded row — the exact
  failure `shared_kernel/validation.py:1-17` exists to prevent, and it compounds on read since
  `WakeupConfig.from_dict` runs on every message dispatch and every 30-second sweep.
  `json_bounds_violation` was split out of `bounded_json` so the limits stay defined once,
  `AgentService.patch` re-applies them to the merged result, and a new `AgentConfigTooLarge`
  maps to 413 alongside the existing `WorkspaceQuotaExceeded` size-limit precedent. Pinned by
  `test_merged_config_exceeding_the_bound_is_rejected`.
- **D-3 (self-audit correction, 2026-07-27):** the frontend passthrough initially returned a live
  reference into the TanStack Query cache entry for unmodelled subtrees, so mutating one would
  have silently mutated cached server state — the in-place-mutation pitfall this codebase has
  been bitten by before. The passthrough is now cloned; pinned by
  `clones passed-through keys instead of aliasing the input`.
- **D-4 (quality-gate simplification, 2026-07-27):** the first passthrough implementation filtered
  a `NORMALIZED_ROOT_KEYS` list out of the result. Redundant: those three keys are spread last in
  the returned literal and win regardless, so the list was dead logic and a second place to update
  when a root key is added. Removed; only the legacy-shape filter remains.
- **D-5 (AC-8 operator query):** the §7 query was run against the local Postgres backing the dev
  compose stack, migrated to head. It returned **0 rows** — no agent on that database has a
  `triggers` config without `soft_bounds`, because the database carries no real agent rows. **The
  operator must rerun the query against the target database before rollout**; the local result
  says nothing about production, and per §7 there is no repair for the rows it finds.
- **D-6 (gate execution environment):** backend `pytest -q` from `backend/` collects the
  integration and wiring tiers, which this host cannot run: service DSNs resolve only inside the
  compose network (`postgres:5432`), and host→container port publishing does not work here —
  verified by confirming a socat listener active *inside* a container while the host connection
  was refused. The same 12 integration failures / 37 errors and 54 wiring failures occur at the
  task's base commit `4d30909`, confirmed by running both tiers in a worktree at that commit, so
  nothing in this diff is implicated. Unit tier ran on the host; the new integration test ran
  inside `smap_backend_web`, which bind-mounts the working tree, against real Postgres.
- **D-7 (AC-9 / gate 2):** `pnpm run gen:api` is N/A — the OpenAPI schema is unchanged. Verified
  by exporting the spec and comparing it to the committed `backend/openapi.json` semantically:
  identical. The raw file hashes differ only because PowerShell's `>` adds a BOM and CRLF, the
  artifact commit `54fc1a8` already had to strip once; the committed file was not touched.
- **D-10 (`/code-review` finding, HIGH — fixed 2026-07-27):** D-1's `replace_wakeup_config=True`
  reached only the first-attempt draft. The draft rebuilt inside `refresh_wakeup_config`'s
  `AgentVersionMismatch` handler (`wakeup_service.py:431-434`) omitted it, so on any version
  conflict — routine here, since wake-up workers and the hourly sweep race on the same row — the
  retry fell back to merging and reintroduced D-1's never-converging refresh on exactly the common
  path. Cause: the edit that added the flag used `replace_all`, which matched only the outer
  occurrence because the retry draft is indented differently. `test_refresh_asks_for_a_replacing_write_not_a_merge`
  exercised only the first attempt, so nothing caught it. Fixed, and
  `test_refresh_retry_after_a_version_conflict_still_replaces` now drives a forced conflict and
  asserts *both* drafts carry the flag.
- **D-11 (`/code-review` finding, MEDIUM — fixed 2026-07-27):** Q-3's rationale ("after a merge,
  the merged dict *is* what the designer authored") is false whenever the live config carries
  R15.06 self-modification that has not been refreshed away. Basing the snapshot merge on
  `current.wakeup_config` therefore laundered the agent's own drift into the designer baseline: an
  agent that self-modified `n` 3→20 followed by any unrelated human PATCH would have 20 recorded
  as authored intent, and R15.09 could never restore 3. The snapshot now merges over the
  **previous snapshot**, falling back to the live config only when no snapshot exists (no baseline
  to protect, and F-1's key preservation still applies). Pinned by
  `test_human_edit_does_not_launder_runtime_drift_into_the_snapshot`; the integration test still
  passes, since with no drift both bases coincide.
- **D-9 (one flaky frontend run, recorded rather than hidden):** one full Vitest run reported
  `1 failed | 855 passed` while a full backend `pytest` was running concurrently on the same host;
  its transform/import timings were inflated roughly 10x by the contention. The reporter output
  did not survive to name the test. Re-run uncontended, the suite passes 856/856, as does the run
  taken before the final two commits (855/855 at that point). Treated as host contention, not a
  defect, but the failing test was never identified, so this is a known gap rather than a
  dismissal.
- **D-8 (frontend gate invocation):** the `pnpm` wrappers attempt an interactive `node_modules`
  store relink on this host, so the installed project binaries were invoked directly — Vitest,
  `vue-tsc`, ESLint and Vite. Same tools, same configs, no wrapper. This matches the precedent
  recorded as D-4 in `2026-07-22-wakeup-trigger-state-and-bounds`.

## 13. Follow-ups

- **FU-1** The frontend `WakeupConfig` type serves as both the editor's view model and the PATCH
  payload shape. C2 makes it tolerant, but the structural answer is separate read and write types so
  the compiler, not a passthrough, guarantees nothing is lost. Route to `check-quality` at the next
  refactor of this slice.
- **FU-2** `soft_bounds` still has no editor control (the prior dossier's FU-2). After this fix an
  admin can set it via API and it survives, but they still cannot see or edit it in the UI, which is
  why the erasure went unnoticed for so long.
- **FU-3** `agent.edited` records only changed field *names* (`agent_service.py:786`). For free-form
  JSONB columns that makes the audit trail useless for reconstructing a lost value — the reason §7
  can offer no repair. Consider recording a before/after digest for JSONB columns.
- **FU-4** The merged-config bounds check from D-2 also runs on the G.5 restore path, where the
  value being written is the authored snapshot rather than caller input. An agent whose snapshot
  predates `BoundedConfig` and exceeds its limits would fail its hourly refresh forever — logged by
  the worker's per-agent guard, so it fails safe, but silently. Either exempt the replace path or
  measure the snapshot at write time. No such row is known to exist; the bounds predate this work.
- **FU-5** `uuid(int=0)`, the actor the wake-up service writes as, has no `users` row in a
  bootstrapped-but-unseeded database (verified: `select count(1) ... where id = '000...0'` returns
  0 on the dev stack). `audit_logs.actor_user_id` carries an FK to `users`, so any G.4/G.5 write
  that emits an audit row raises `ForeignKeyViolationError` on such a database. Pre-existing and
  out of scope here — the integration test seeds the row itself — but it means the wake-up
  self-modification and refresh paths depend on a seeding step nothing enforces. Worth pinning in
  the bootstrap CLI or making the system actor id nullable in the audit FK.
- **FU-7 (`/code-review` finding, LOW — not fixed)** `normalizeWakeupConfig` strips the legacy flat
  root keys from the payload, but with the server write now additive, omitting a key no longer
  removes it: a pre-2026-06-26 row opened and saved through the UI keeps both shapes permanently,
  and no UI path can clear them (removal needs an explicit `null`, which the editor never sends).
  Behaviour is unaffected — `WakeupConfig.from_dict` reads the root-level legacy keys only when
  `triggers` is absent — so this is unremovable dead state, not a defect. The comment at
  `workflow.ts:144-152` has been corrected to say so rather than implying the filter cleans up.
  A deliberate one-time migration (or having the editor send explicit nulls for the legacy keys)
  would clear it.
- **FU-8 (`/code-review` finding, LOW — not fixed)** D-2's bounds check can block R15.06
  self-modification for an agent whose `wakeup_config` sits within roughly 250 bytes / 15 nodes of
  the `BoundedConfig` ceiling: `_build_new_dict` merges the fully normalized `to_dict()` over the
  stored value, so the result is strictly larger, and `AgentConfigTooLarge` propagates out of
  `patch_agent`. `update_wakeup`'s retry loop catches only `AgentVersionMismatch`, and
  `build_update_wakeup_tool._invoke` (`tool_registry.py:211-220`) has no handler, so the agent's
  own tool call raises rather than degrading. It fails closed (the agent cannot self-tune; nothing
  else breaks) and the error *is* mapped at the API boundary (413), so the reachable impact is
  narrow. Left open because the right answer is a product decision — whether a near-ceiling agent
  should be refused self-tuning or allowed a bounded overshoot — not a bugfix.
- **FU-9** With `{}` no longer clearing a config column (it merges to a no-op) and deletion only
  possible key by key via the Q-2 tombstone, there is no API path left to reset `wakeup_config` or
  `workflow_capabilities` wholesale. `merged_wakeup or None` in `AgentService.patch` is
  correspondingly close to dead. Accepted under Q-2, but if a reset operation is ever wanted it
  needs its own explicit affordance.
- **FU-6** This host cannot run the real-DB test tiers (D-6). The integration and wiring suites are
  effectively CI-only for anyone on a Docker Desktop setup without published ports. A documented
  `docker compose exec backend-web pytest` recipe — which is what actually worked here — would
  save the next person the rediscovery.
