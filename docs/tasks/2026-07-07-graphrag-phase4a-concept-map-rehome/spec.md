---
type: feature
status: approved
created: 2026-07-07
requirements: [R11.07, R11.09, R11.10, R11.17, R11.21, R11.22, R24.05, R24.06]
supersedes: 2026-07-07-graphrag-phase4-frontend-rehome
---

# GraphRAG Phase 4α — Concept Map frontend re-home (owner layers, privacy, temporal)

## 1. Summary

Phase 4α re-homes the Concept Map UI off the single agent onto its three owner layers
(chatroom, agent_group, workspace) and adds the privacy and temporal controls. Today all
knowledge UI lives in the `agents` slice and GraphRAG is 1:1 with an agent, with no owner-
layer, privacy, temporal, or multi-member-group surface. 4α introduces a new first-class
`agent-groups` slice (group + member management), generalizes the shipped GraphRAG
api/build-state/socket/graph-view for `owner_kind`, surfaces Concept Maps hybrid (a central
overview plus per-owner contextual panels), exposes the Project-Owner-gated
`concept_map_enabled` opt-in and the per-config recency half-life, and rewires the agent
Knowledge tab now that the `agents.graphrag_config_id` attach was dropped in Phase 1
(coverage is by membership). It is pure frontend + i18n, realizing R11.07–R11.22.

This dossier is the Concept Map half of the split Phase 4; the Knowledge Map UI is Phase 4β
(`2026-07-07-graphrag-phase4b-knowledge-map-ui`). Both supersede the combined
`2026-07-07-graphrag-phase4-frontend-rehome` draft.

**Phase dependencies (hard):** the Phase 1 and Phase 2b backends (owner layers, privacy,
temporal, multi-member) must be built first — 4α consumes their endpoints. 4β depends on 4α
for the generalized concept-map surface and the lifted `useProjectRole`.

## 2. Goals and Non-goals

**Goals**
- G1 — A new `agent-groups` slice: group CRUD + multi-member management (add/remove member
  agents), Project-Owner-gated (R11.07).
- G2 — Concept Maps surfaced hybrid (Q-2): one project-scoped overview (owner-type
  column/filter, build state, manage) plus contextual panels in each owner's settings
  (chatroom, workspace in `conversation`; agent_group in `agent-groups`).
- G3 — Privacy: a Project-Owner-gated `concept_map_enabled` toggle on wide-layer (agent_group,
  workspace) maps; chatroom maps show a read-only "inherits room ACL" note (R11.10, R11.17).
- G4 — Temporal: a per-config `recency_half_life_days` numeric control on Concept Map settings
  (R11.21).
- G5 — Agent Knowledge tab: remove the dropped `graphrag_config_id` attach, show read-only
  Concept Map coverage (room + groups + workspace) (R11.09). The Knowledge Map attach is 4β.
- G6 — All new strings i18n'd (en + zh-TW); cross-slice access via barrels; boundaries and
  transport gates respected (R24.05, R24.06).

**Non-goals**
- Knowledge Map document UI (Phase 4β).
- Any backend change — 4α only consumes Phase 1/2b endpoints. A missing endpoint is a backend-
  phase gap, not fixed here.
- Time-travel / bitemporal UI (R11.21 reserves it).
- Wide-layer full tier-matrix privacy UI — the backend uses a single `concept_map_enabled`
  (Phase 2b Q-2); the UI matches that single toggle.
- A chip/tag atom in `shared/ui` — reuse the `STable` + add-form pattern (FU-1).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does the `agent-groups` frontend live? | A new first-class `agent-groups` slice. | Mirrors the backend first-class, project-scoped `agent_group`. Costs a new-slice template (routes, locales, barrel, eslint boundary + dependency-chain insertion — §13). Rejected folding into `agents` (an agent group ≠ an agent, bloats the slice) and `tenancy` (owns org/project/members, not agent grouping). |
| Q-2 | How are the three Concept Map layers surfaced? | Hybrid: a central project-scoped overview plus per-owner contextual panels (chatroom/workspace in `conversation`, group in `agent-groups`). | Matches Design D (hybrid SaaS + chat-first): overview for project-level manage/status, contextual panels keep each owner's map in its natural workflow. Rejected central-only (can't see a room's map from room settings) and per-owner-only (no project overview). |
| Q-3 | Where does the shared Concept Map api/build-state/graph-view live so three slices consume it? | Generalize the existing `agents`-slice GraphRAG api/composables for `owner_kind` and expose via the `agents` barrel; owner panels import from `@slices/agents`. | Least churn — reuses the shipped build-state/socket/graph-view. `agents` sits right of `conversation`/`agent-groups` in the chain (R24.06), so both may consume it. Extract-to-`shared` is a later refactor (FU-3). |

## 4. Current State

- **All knowledge UI is agent-scoped in `agents`.** GraphRAG: `GraphragConfigListView.vue`
  (list, create modal, delete, build trigger, status drawer), `GraphragGraphView.vue` (Vue Flow
  viz). Create binds `agent_id`+`builder_key_group_id`+`trigger_config`
  (`agents/types/schemas.ts:68-71`); no privacy/temporal fields today
  (`shared/api-client/models/GraphRagTriggerConfig.ts:12-16`). Attach is the agent-editor
  Knowledge-tab reverse binding (`AgentDetailView.vue:969-999`, `graphrag_config_id` SSelect).
- **Backend edit exists, unused.** `PATCH /api/graphrag/{id}` is generated
  (`GraphragService.ts:76`) but no hand-written wrapper/edit UI — the seam for privacy/temporal.
- **Build-state/live UI exists.** `GraphragBuildState` + `GRAPHRAG_IN_PROGRESS`
  (`agents/api/index.ts:101-115`); `useGraphragSocket.ts` (WS `/graphrag/{configId}` + 15 s poll
  backstop). Reusable across owners.
- **Owner-layer entities live elsewhere.** workspace + chatroom UI in `conversation`
  (`WorkspaceListView.vue`, `ChatroomSettingsView.vue`); **`agent_group` UI does not exist**
  anywhere (grep-confirmed). tenancy owns org/project/members.
- **Shared building blocks.** `SToggle` (used in `ChatroomSettingsView.vue` room flags),
  `SModal`/`SDrawer`, `SConfirmDialog`, `SStatusBadge`, `SFormField`+`SInput`
  (`type="number"` for half-life — no slider atom), `STable`+add-form for member/allowlist
  editing (no chip atom; exemplar `McpEgressAllowlistView.vue`).
- **Plumbing.** Slices call a hand-written `api/index.ts` → `http` from `@shared/transport`
  (NOT the generated `*Service`); TanStack Query + per-slice query keys; forms via vee-validate
  + Zod + `useServerErrors` (RFC 7807 → field errors). Role-gating has no global directive —
  fetch members → `isOwner = members.find(m=>m.user_id===me.id)?.role==='owner'` (exemplar
  `McpEgressAllowlistView.vue:47,185,229,294`); reusable shape `useProjectRole.ts` (in
  `workflow`, to be lifted). i18n per-slice `locales/{en,zh-TW}.json` via `install*Slice()`;
  literal `@` must be `{'@'}`. Tokens in `shared/styles/main.css` (accent `#2563eb`); icons
  `@heroicons/vue`. Boundaries in `eslint.config.js` (barrel-only cross-slice, one-way chain).

## 5. Design

### Options considered
- **agent_group home (Q-1)** — new slice (chosen) vs fold into `agents` vs `tenancy`.
- **Concept Map surface (Q-2)** — hybrid (chosen) vs central-only vs per-owner-only.
- **Shared concept-map code (Q-3)** — generalize in `agents` + barrel (chosen) vs move to
  `shared` vs duplicate.

### Decision
New `agent-groups` slice, hybrid surface, shared concept-map surface generalized in `agents`.
Least code churn while respecting R24 layering: shipped build-state/socket/graph-view reused,
owner panels thin consumers of the `agents` barrel, and the only SoC-contract change is
inserting `agent-groups` into the dependency chain (R24.06, §13).

## 6. Detailed Changes

Five workstreams. WS1 (slice) and WS2 (generalized surface) are foundational; WS3/WS4/WS5 build
on them.

### WS1 — `agent-groups` slice (R11.07; Q-1)
- New `src/slices/agent-groups/` with the standard shape: `api/index.ts` (group CRUD, member
  add/remove over `http`), `types/schemas.ts` (Zod), `queries/index.ts` (keys `['agent-groups',
  ...]`), `views/` (list + detail with member management), `routes.ts`, `locales/{en,zh-TW}.json`,
  `index.ts` barrel + `installAgentGroupsSlice()`. Member management mirrors the
  `McpEgressAllowlistView` `STable`+add-form+`SConfirmDialog` pattern; Project-Owner-gated via
  the lifted `useProjectRole`. Register in `app/router.ts`, `app/main.ts`, the sidebar, and
  `eslint.config.js` boundaries + dependency chain (§13). Add the slice with an empty barrel
  first so lint validates the boundary before it is filled.

### WS2 — Generalize the shared Concept Map surface (R11.09; Q-3)
- Generalize the `agents`-slice GraphRAG api/types for `owner_kind` (`agent_group|chatroom|
  workspace`) + `owner_id`: extend the config type, add `patchGraphragConfig` wrapping the
  existing `PATCH /api/graphrag/{id}`, add a project-scoped list across owner kinds, and a
  coverage query (which maps cover a given agent/room). Keep `useGraphragSocket`, the
  `GraphragBuildState` machine, and `GraphragGraphView` (rename props off "agent"); export the
  public surface from the `agents` barrel. Lift `useProjectRole` from `workflow` to `shared`
  (or `tenancy`) so owner panels reuse it without a deep cross-slice import.

### WS3 — Concept Map overview + contextual panels (R11.09; Q-2)
- **Central overview** (`agents` "Knowledge" nav): a project-scoped "Concept Maps" list — owner-
  type column/filter, build-state pill (`SStatusBadge`), create (owner picker), manage, delete;
  reuses the generalized list + build controls + `SPagination`.
- **Contextual panels**: a chatroom-map panel in `conversation/ChatroomSettingsView.vue`, a
  workspace-map panel in a `conversation` workspace settings view, and an agent_group-map panel
  in the `agent-groups` detail view. Each imports the concept-map api from `@slices/agents` and
  shows that owner's map: build state, build/rebuild, graph link, and the WS4 controls.

### WS4 — Privacy + temporal controls (R11.10, R11.17, R11.21)
- **Privacy**: on wide-layer (agent_group, workspace) map settings, a `concept_map_enabled`
  `SToggle`, rendered/enabled only for a Project Owner (`useProjectRole`, respecting its
  `decided` flag), with a non-owner read-only note (mirror `McpEgressAllowlistView:229`); the
  toggle PATCHes via WS2. Chatroom maps render a read-only "inherits room access" note (R11.17)
  — no toggle.
- **Temporal**: a `recency_half_life_days` `SInput type="number"` in `SFormField`, Zod-validated
  (positive, bounded), persisted via `patchGraphragConfig`.

### WS5 — Agent Knowledge tab (Concept Map side) (R11.09)
- Remove the `graphrag_config_id` SSelect from `AgentDetailView.vue` (column dropped in Phase
  1). Add a read-only "Concept Map coverage" block: the room map + each agent_group map the
  agent belongs to + the workspace map, from the WS2 coverage query (transparency, not an
  attach). Keep `rag_config_id` untouched. (The `knowmap_config_id` attach is added in 4β.)

**Plumbing** — lift `useProjectRole` (WS2); new i18n keys in each owning slice's locales (escape
`@`); new routes + sidebar entries; `gen:api` N/A (hand-written api); keep `check:openapi-drift`
green against the Phase 1/2b endpoints.

## 7. NFR Checklist

- [x] **i18n** — every new string in the owning slice's `locales/{en,zh-TW}.json`; no bare
  template strings (eslint gate); literal `@` escaped (enforced test).
- [x] **Audit log** — N/A on the frontend; the backend audits privacy toggles/deletes (Phase
  2b). The UI surfaces the actions, records nothing itself.
- [x] **Tenant isolation** — every list/detail project-scoped by route; owner-only controls gated
  by `useProjectRole` (`decided` prevents hiding an owner's control mid-load); never bypass
  backend AuthZ (a 403 still shows an error).
- [x] **Error handling UX** — loading/error/empty states on every new list/detail; RFC 7807 via
  `useServerErrors`; `SConfirmDialog` for destructive actions (no native `confirm`).
- [x] **Performance** — TanStack Query caches per project; the overview paginates; build progress
  via WS + poll backstop (reuse `useGraphragSocket`).

## 8. Security Considerations

Touches tenant boundaries and Project-Owner-gated controls — a Security Considerations section
is required.

- **Client gating is UX, not security.** Owner-only toggles are hidden for non-owners for
  clarity; the backend (Phase 2b) remains the authority and gates every mutation. The UI must
  degrade gracefully on a 403.
- **`decided`-flag correctness.** Do not hide an owner's control before membership resolves
  (`useProjectRole.decided`) — a flash-hidden-then-shown control is a correctness bug.
- **No transport bypass.** All calls via slice `api/` → `http` (R24.02/R24.03); WS via the shared
  client; no bare `fetch`/`WebSocket`.
- **No `v-html`.** Graph/entity labels render as text; `v-html` stays confined to the whitelisted
  conversation files.

## 9. Quality Notes

- **Existing debt (do not imitate).** `useProjectRole` living in `workflow` is a misplacement —
  lift it to shared rather than deep-importing across slices. (File-RAG's ungated detail view is
  4β/FU-4 territory.)
- **Patterns to follow.** Slice shape + barrel (`conversation/index.ts`); CRUD list
  (`RagConfigListView.vue:62,202,223`); owner-gating (`McpEgressAllowlistView.vue:47,185,229,294`);
  build-state + socket (`useGraphragSocket.ts`); forms (`schemas.ts` + `useForm` +
  `useServerErrors`); toggle (`ChatroomSettingsView.vue` room flags via `SToggle`).
- **Reuse inventory (import, do not re-create).** `@shared/ui` atoms (`SToggle`, `SModal`,
  `SDrawer`, `SConfirmDialog`, `SStatusBadge`, `SFormField`, `SInput`, `STable`, `STabs`,
  `SEmptyState`, `SPagination`); `http` from `@shared/transport`; `useServerErrors`; the
  `GraphragBuildState` machine, `useGraphragSocket`, `GraphragGraphView` (WS2-generalized);
  `useProjectRole` (WS2-lifted); the query-key + TanStack patterns.

## 10. Risks and Rollback

- **Scope** — five workstreams. Mitigation: WS ordered and independently shippable.
- **New-slice boundary churn** — inserting `agent-groups` into the R24.06 chain and eslint config
  can break the boundary lint if mis-ordered. Mitigation: the SRS Delta (§13) fixes the position;
  add the slice with an empty barrel first.
- **Backend-endpoint gaps** — 4α assumes Phase 1/2b endpoints (owner-kind config list, coverage
  query, `concept_map_enabled`/`recency_half_life_days` on PATCH). Mitigation: pre-flight against
  the built backend; a missing endpoint is a backend-phase gap (recorded, not patched in the UI).
- **Agent-tab regression** — removing `graphrag_config_id` must not break agent save. Mitigation:
  a component test that the Knowledge tab persists `rag_config_id` and no longer sends the dropped
  field.
- **Rollback** — frontend-only; revert the commit(s). The new slice is additive; removing it and
  the sidebar/route entries fully reverts. No migration.

## 11. Acceptance Criteria

- [ ] AC-1: an `agent-groups` slice exists with group CRUD and member add/remove; a non-owner
  cannot see the mutating controls; the slice passes the boundaries lint.
- [ ] AC-2: the "Concept Maps" overview lists maps of all three owner kinds with an owner-type
  filter and a build-state pill; create lets the user pick the owner.
- [ ] AC-3: a chatroom-map panel appears in chatroom settings, a workspace-map panel in workspace
  settings, and an agent_group-map panel in the group detail — each showing that owner's map and
  build controls.
- [ ] AC-4: on a wide-layer map, a Project Owner sees an enabled `concept_map_enabled` toggle that
  PATCHes; a non-owner sees a read-only note; a chatroom map shows "inherits room access" with no
  toggle.
- [ ] AC-5: a Concept Map's `recency_half_life_days` is editable (validated, positive) and
  persists.
- [ ] AC-6: the agent Knowledge tab no longer renders a Concept Map attach and shows read-only
  coverage (room + groups + workspace); `rag_config_id` still persists.
- [ ] AC-7: all new strings resolve in both `en` and `zh-TW`; no bare-string or literal-`@`
  lint/test failure.
- [ ] AC-8: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` pass; `check:openapi-drift`
  is green.

## 12. Test Plan

- **Component/unit** (Vitest + Vue Test Utils): AC-1 (group member add/remove, owner-gating),
  AC-2 (overview filter + owner picker), AC-4 (toggle owner-gating + `decided` flag; chatroom
  read-only note), AC-5 (half-life validation + persist), AC-6 (agent-tab omits the dropped
  field, persists rag attach).
- **Boundaries/i18n**: AC-1 (eslint boundaries pass for the new slice), AC-7 (both locales
  resolve; literal-`@` test green).
- **Integration/manual (`verify`)**: against the built Phase 1/2b backend — create a group,
  enable its map, set half-life, confirm coverage on an agent.

## 13. SRS Delta

Apply verbatim on approval.

**Amend [R24.06]** (insert `agent-groups` into the dependency chain):
> **[R24.06]** **Cross-slice dependency direction.** Allowed:
> `conversation → agent-groups → agents → keys → tenancy → identity → shared` (plus the
> orthogonal `workflow`, `admin`, `notifications`, and `prompt-studio` slices, each importing
> only rightward). Disallowed: any reverse direction. The new `agent-groups` slice is a first-
> class, project-scoped slice owning agent-group CRUD, membership, and the group-owned Concept
> Map panel; it may import `agents`/`keys`/`tenancy`/`identity`/`shared` and is imported by
> `conversation`. Codified in `eslint.config.js` with the `boundaries` plugin; violations fail
> CI.

## 14. Open Questions

- Q-A (non-blocking) — whether the shared Concept Map api/build-state/graph-view should later be
  extracted from `agents` into `shared` (FU-3). Not needed now.

## 15. Deviation Log

Appended by `/build`.

## 16. Follow-ups

- FU-1 — a reusable removable-chip/tag atom in `shared/ui` if the `STable`+add-form pattern
  proves too heavy.
- FU-2 — a dedicated numeric slider atom for half-life (currently `SInput type="number"`).
- FU-3 — extract the shared Concept Map surface from `agents` into `shared` (Q-A/Q-3).
