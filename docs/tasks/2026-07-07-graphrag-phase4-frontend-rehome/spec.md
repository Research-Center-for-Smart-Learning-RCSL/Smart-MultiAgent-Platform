---
type: feature
status: draft
created: 2026-07-07
requirements: [R11.07, R11.09, R11.10, R11.12, R11.14, R11.21, R11.22, R11.23, R24.05, R24.06]
---

# GraphRAG Phase 4 — Frontend re-home (owner layers, privacy, temporal, Knowledge Map)

## 1. Summary

Phase 4 brings the two-axis redesign to the UI. Today every knowledge feature (file-RAG and
GraphRAG) lives in the `agents` slice and GraphRAG is 1:1 with a single agent; there is no
owner-layer, privacy, temporal, multi-member-group, or Knowledge Map surface at all. Phase 4
re-homes the Concept Map off the single agent onto its three owner layers (chatroom,
agent_group, workspace) with a hybrid central-overview + per-owner-panel UX, exposes the
Project-Owner-gated privacy opt-in and the per-config recency half-life, introduces a new
first-class `agent-groups` slice for group and member management, adds the Knowledge Map
document UI (mirroring file-RAG), and rewires the agent editor's Knowledge tab now that the
`agents.graphrag_config_id` attach is gone (dropped in Phase 1) and coverage is by membership.
It is pure frontend + i18n; it realizes backend requirements R11.07–R11.23 already shipped by
Phases 1–3 and touches no backend behavior.

**Phase dependencies (hard):** the backend of Phases 1, 2b (owner layers, privacy, temporal,
multi-member) and Phase 3 (Knowledge Map) must be built first — Phase 4 consumes their
endpoints. It is specified as six workstreams so `/build` can land it in stages; WS5
(Knowledge Map) and WS4 (agent-tab rewire) are separable from the Concept Map workstreams.

## 2. Goals and Non-goals

**Goals**
- G1 — A new `agent-groups` slice: group CRUD + multi-member management (add/remove member
  agents), Project-Owner-gated (R11.07).
- G2 — Concept Maps surfaced hybrid (Q-2): one project-scoped overview list (owner-type
  column/filter, build state, manage) plus contextual panels in each owner's settings
  (chatroom, workspace in `conversation`; agent_group in `agent-groups`).
- G3 — Privacy: a Project-Owner-gated `concept_map_enabled` toggle on wide-layer (agent_group,
  workspace) maps; chatroom maps show read-only "inherits room ACL" (R11.10, R11.17).
- G4 — Temporal: a per-config `recency_half_life_days` numeric control on Concept Map settings
  (R11.21).
- G5 — Agent Knowledge tab rewired: remove the dropped `graphrag_config_id` attach, show
  read-only Concept Map coverage (room + groups + workspace), keep file-RAG, add the Knowledge
  Map attach `knowmap_config_id` (R11.09, R11.14).
- G6 — Knowledge Map UI in `agents`: config CRUD, document upload (multipart + tus), per-
  document allowlist editor, build/rebuild + status, graph view (R11.12, R11.14, R11.23).
- G7 — All new strings i18n'd (en + zh-TW), all cross-slice access via barrels, boundaries and
  transport gates respected (R24.05, R24.06).

**Non-goals**
- Any backend change — Phase 4 only consumes Phase 1–3 endpoints. A missing endpoint is a
  backend-phase gap, not fixed here.
- Time-travel / bitemporal UI (R11.21 reserves it).
- A rich chip/tag atom in `shared/ui` — reuse the `STable` + add-form allowlist pattern; a new
  atom is out of scope (FU).
- Redesigning file-RAG UI — mirror it for Knowledge Map, do not refactor it.
- Wide-layer full tier-matrix privacy UI — the backend uses a single `concept_map_enabled`
  (Phase 2b Q-2); the UI matches that single toggle.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does the `agent-groups` frontend live? | A new first-class `agent-groups` slice. | Mirrors the backend first-class, project-scoped `agent_group`; keeps group CRUD/members/its map/privacy self-contained. Costs a new-slice template (routes, locales, barrel, eslint boundary entry, dependency-chain insertion — see §13). Rejected folding into `agents` (semantically an agent group ≠ an agent, bloats the slice) and `tenancy` (owns org/project/members, not agent grouping). |
| Q-2 | How are the three Concept Map layers surfaced? | Hybrid: a central project-scoped "Concept Maps" overview plus per-owner contextual panels (chatroom/workspace settings in `conversation`, group detail in `agent-groups`). | Matches Design D (hybrid SaaS + chat-first): the overview gives a project-level manage/status view, the contextual panels keep each owner's map in its natural workflow. Rejected central-only (can't see a room's map from room settings) and per-owner-only (no project overview). |
| Q-3 | Where does the shared Concept Map API/build-state/graph-view live so three slices consume it? | Generalize the existing `agents`-slice GraphRAG api/composables (build-state machine, `useGraphragSocket`, `GraphragGraphView`) for `owner_kind` and expose them via the `agents` barrel; owner panels import from `@slices/agents`. | Least churn — reuses the shipped build-state/socket/graph-view code rather than moving it. `agents` already sits right of `conversation`/`agent-groups` in the dependency chain (R24.06), so both may consume it. Extracting to `shared` is a possible later refactor (FU-3). |

## 4. Current State

- **All knowledge UI is agent-scoped in `agents`.** GraphRAG: `GraphragConfigListView.vue`
  (list, create modal, delete, build trigger, status drawer) and `GraphragGraphView.vue` (Vue
  Flow viz). Create binds `agent_id`+`builder_key_group_id`+`trigger_config`
  (`agents/types/schemas.ts:68-71`); `GraphRagTriggerConfig` carries only
  `every_n_messages|manual|silence_minutes` — no privacy/temporal fields
  (`shared/api-client/models/GraphRagTriggerConfig.ts:12-16`). Attach is a reverse binding in
  the agent editor Knowledge tab (`AgentDetailView.vue:969-999`, `graphrag_config_id` SSelect).
- **Backend edit exists, unused.** `PATCH /api/graphrag/{id}` is generated
  (`GraphragService.ts:76`) but no hand-written `agentsApi.patchGraphragConfig` and no edit UI
  wrap it — the seam for privacy/temporal edits.
- **file-RAG is the Knowledge Map exemplar.** `RagConfigListView.vue` (CRUD) +
  `RagConfigDetailView.vue` (tabbed Settings/Documents; `SFileUpload` + size-branch `onFiles`
  at `:291`, multipart ≤32 MB else `tusUpload`; per-document allowlist editor modal via
  `setDocumentAgents`; ingestion progress via `useRagConfigSocket`).
- **Build-state/live UI already exists.** `GraphragBuildState` union + `GRAPHRAG_IN_PROGRESS`
  (`agents/api/index.ts:101-115`); `useGraphragSocket.ts` (WS `/graphrag/{configId}` + 15 s
  poll backstop). Reusable across owners.
- **Owner-layer entities live elsewhere.** workspace + chatroom UI in `conversation`
  (`WorkspaceListView.vue`, `ChatroomSettingsView.vue`); **`agent_group` UI does not exist**
  anywhere (grep-confirmed). tenancy owns org/project/members.
- **Shared building blocks.** `SToggle` (used in `ChatroomSettingsView.vue` for room flags),
  `SFileUpload`, `SModal`/`SDrawer`, `SConfirmDialog`, `SStatusBadge`, `SFormField`+`SInput`
  (`type="number"` for half-life — no slider atom), `STable`+add-form for allowlists (no chip
  atom; exemplar `McpEgressAllowlistView.vue`).
- **Plumbing.** Slices call a hand-written `api/index.ts` → `http` from `@shared/transport`
  (NOT the generated `*Service`); TanStack Query for server state + per-slice query keys; forms
  via vee-validate + Zod + `useServerErrors` (RFC 7807 → field errors). Role-gating has no
  global directive — fetch project members → `isOwner = members.find(m=>m.user_id===me.id)?.role
  ==='owner'` (exemplar `McpEgressAllowlistView.vue:47,185,294`); reusable shape
  `useProjectRole.ts` (currently in `workflow`). i18n per-slice `locales/{en,zh-TW}.json`
  registered via `install<Slice>Slice()`; literal `@` must be `{'@'}`. Design tokens in
  `shared/styles/main.css` (`@theme`, accent `#2563eb`); icons `@heroicons/vue`. Boundaries in
  `eslint.config.js` (barrel-only cross-slice, one-way dependency chain).

## 5. Design

### Options considered

**agent_group home (Q-1).** New `agent-groups` slice (chosen) vs fold into `agents` vs
`tenancy` — see Q-1 rationale.

**Concept Map surface (Q-2).** Hybrid overview + contextual panels (chosen) vs central-only vs
per-owner-only — see Q-2 rationale.

**Shared concept-map code (Q-3).** Generalize in `agents` + barrel-expose (chosen) vs move to
`shared` vs duplicate per slice — see Q-3 rationale.

### Decision

New `agent-groups` slice, hybrid surface, shared concept-map surface generalized in `agents`.
Together these re-home the Concept Map with the least code churn while respecting the R24
layering: the shipped build-state/socket/graph-view are reused, owner panels are thin
consumers of the `agents` barrel, and the only structural change to the SoC contract is
inserting `agent-groups` into the dependency chain (R24.06, §13).

## 6. Detailed Changes

Six workstreams. WS1 (slice) and WS3 (shared concept-map api generalization) are foundational;
WS2/WS4/WS5 build on them; WS6 is cross-cutting plumbing landed alongside.

### WS1 — `agent-groups` slice (R11.07; Q-1)
- New `src/slices/agent-groups/` with the standard shape: `api/index.ts` (group CRUD, member
  add/remove — hand-written over `http`), `types/schemas.ts` (Zod), `queries/index.ts` (keys
  `['agent-groups', ...]`), `views/` (list + detail with member management), `routes.ts`,
  `locales/{en,zh-TW}.json`, `index.ts` barrel + `installAgentGroupsSlice()`.
- Member management mirrors the `McpEgressAllowlistView` `STable`+add-form+`SConfirmDialog`
  pattern; Project-Owner-gated via the `useProjectRole` shape (lifted in WS6). Register the
  slice in `app/router.ts`, `app/main.ts`, the sidebar, and `eslint.config.js` boundaries +
  dependency chain (§13).

### WS3 — Generalize the shared Concept Map surface (R11.09; Q-3)
- Generalize the `agents`-slice GraphRAG api/types for `owner_kind` (`agent_group|chatroom|
  workspace`) + `owner_id`: extend the config type, add `patchGraphragConfig` wrapping the
  existing `PATCH /api/graphrag/{id}`, add a project-scoped list returning all owner kinds, and
  a coverage query (which maps cover a given agent/room). Keep `useGraphragSocket`, the
  `GraphragBuildState` machine, and `GraphragGraphView` (rename props off "agent"), and export
  the public surface from the `agents` barrel for cross-slice use.

### WS2 — Concept Map overview + contextual panels (R11.09, R11.10, R11.21; Q-2)
- **Central overview** (`agents` "Knowledge" nav): a project-scoped "Concept Maps" list —
  owner-type column/filter, build-state pill (`SStatusBadge`), create (owner picker), manage,
  delete; reuses the generalized list + build controls.
- **Contextual panels**: a chatroom-map panel in `conversation/ChatroomSettingsView.vue`, a
  workspace-map panel in a `conversation` workspace settings view, and an agent_group-map panel
  in the `agent-groups` detail view. Each imports the concept-map api from `@slices/agents` and
  shows that owner's map: build state, build/rebuild, graph link, and the WS3 controls.

### WS4 — Privacy + temporal controls (R11.10, R11.17, R11.21)
- **Privacy**: on wide-layer (agent_group, workspace) map settings, a `concept_map_enabled`
  `SToggle`, rendered/enabled only for a Project Owner (`useProjectRole`), with a non-owner
  read-only note (mirror `McpEgressAllowlistView:229`); the toggle PATCHes via WS3. Chatroom
  maps render a read-only "inherits room access" note (R11.17) — no toggle.
- **Temporal**: a `recency_half_life_days` `SInput type="number"` in `SFormField`, Zod-
  validated (positive, bounded), persisted via `patchGraphragConfig`. Knowledge Maps do not
  show it (non-temporal).

### WS5 — Agent Knowledge tab rewire (R11.09, R11.14)
- Remove the `graphrag_config_id` SSelect from `AgentDetailView.vue` (column dropped in Phase
  1). Add a read-only "Concept Map coverage" block: the room map + each agent_group map the
  agent belongs to + the workspace map, from the WS3 coverage query (transparency, not an
  attach). Keep `rag_config_id`. Add a `knowmap_config_id` SSelect (Knowledge Map attach, Phase
  3), mirroring the current file-RAG select.

### WS6 — Knowledge Map UI (R11.12, R11.14, R11.23) + shared plumbing
- **Knowledge Map** in `agents` (mirrors file-RAG): a `KnowledgeMapConfigListView` (CRUD) and
  `KnowledgeMapConfigDetailView` (tabbed Settings/Documents) with `SFileUpload` + a size-branch
  upload (add a `'knowledge_map_source'` purpose to `shared/transport/tus.ts`), a per-document
  allowlist editor mirroring `setDocumentAgents`, build/rebuild + status (reuse the build-state
  machine + a knowmap socket composable mirroring `useRagConfigSocket`/`useGraphragSocket`), and
  a graph view reusing the generalized `GraphragGraphView`. Add a "Knowledge Maps" sidebar item
  under "Knowledge" and routes.
- **Plumbing**: lift `useProjectRole` from `workflow` to `shared` (or `tenancy`) so all new
  owner-gated panels reuse it without a cross-slice deep import; add the tus purpose; add all
  new i18n keys in each owning slice's `locales/{en,zh-TW}.json` (escape literal `@`); wire new
  routes + sidebar entries; run `gen:api` is N/A (hand-written api), but keep the
  `check:openapi-drift` green against the new backend endpoints.

## 7. NFR Checklist

- [x] **i18n** — every new string in the owning slice's `locales/{en,zh-TW}.json` via
  `install*Slice()`; no bare template strings (eslint gate); literal `@` escaped `{'@'}`
  (enforced test).
- [x] **Audit log** — N/A on the frontend; the backend audits privacy toggles, allowlist
  changes, deletes (Phases 2b/3). The UI surfaces these actions but records nothing itself.
- [x] **Tenant isolation** — every list/detail is project-scoped by route; owner-only controls
  gated by `useProjectRole` (`decided` flag prevents hiding an owner's control mid-load); the UI
  never bypasses the backend AuthZ (a 403 still shows an error). No cross-project data mixing.
- [x] **Error handling UX** — loading (`SSkeleton`/`SLoadingSpinner`), error (`SAlert` +
  refetch), empty (`SEmptyState`) states on every new list/detail; RFC 7807 field errors via
  `useServerErrors`; `SConfirmDialog` for destructive actions (no native `confirm`).
- [x] **Performance** — TanStack Query caches per project; the overview paginates
  (`SPagination`) for many maps; build/ingest progress via WS with a poll backstop (reuse the
  existing socket composables); large-file upload via resumable tus.

## 8. Security Considerations

Touches tenant boundaries, Project-Owner-gated controls, per-Agent document allowlist, and file
upload — a Security Considerations section is required.

- **Client-side gating is UX, not security.** Owner-only toggles/edits are hidden for non-
  owners for clarity, but the backend remains the authority (Phases 2b/3 gate every mutation);
  the UI must degrade gracefully on a 403, never assume its own gate is sufficient.
- **`decided`-flag correctness.** Do not hide an owner's control before project membership
  resolves (`useProjectRole.decided`) — a flash-hidden control that then appears is a
  correctness bug, not just cosmetic.
- **Upload surface.** Reuse `SFileUpload`'s client size check and the native `accept` MIME
  filter, but rely on the backend for authoritative MIME/scan gating (Phase 3); show the
  quarantine/scan state, never index-and-hide.
- **Allowlist editing.** The Knowledge Map document allowlist editor mirrors `setDocumentAgents`
  (Project-Owner-gated backend); the UI validates ids against agents bound to the config before
  submit, and shows the secure-by-default empty-allowlist meaning ("no agent may retrieve").
- **No transport bypass.** All calls go through the slice `api/` → `http` (R24.02/R24.03); no
  bare `fetch`/`WebSocket`; the new WS channels go through the shared WS client.
- **No `v-html`.** Graph/entity labels render as text; `v-html` stays confined to the
  whitelisted conversation markdown files.

## 9. Quality Notes

- **Existing debt (do not imitate).** `RagConfigDetailView.vue` renders the upload/allowlist
  controls for everyone and relies on a backend 403 rather than owner-gating the UI — the
  Knowledge Map UI should gate with `useProjectRole` like `McpEgressAllowlistView`, not copy the
  ungated RAG view. `useProjectRole` living in `workflow` is a misplacement — lift it to shared
  rather than deep-importing it.
- **Patterns to follow.** Slice shape + barrel (`conversation/index.ts`); CRUD list
  (`RagConfigListView.vue:62,202,223`); detail with upload + allowlist + progress
  (`RagConfigDetailView.vue`); owner-gating (`McpEgressAllowlistView.vue:47,185,229,294`);
  build-state + socket (`useGraphragSocket.ts`); forms (`schemas.ts` + `useForm` +
  `useServerErrors`); toggle (`ChatroomSettingsView.vue` room flags via `SToggle`).
- **Reuse inventory (import, do not re-create).** `@shared/ui` atoms (`SToggle`, `SFileUpload`,
  `SModal`, `SDrawer`, `SConfirmDialog`, `SStatusBadge`, `SFormField`, `SInput`, `STable`,
  `STabs`, `SEmptyState`, `SPagination`); `http`/`tusUpload` from `@shared/transport`;
  `useServerErrors`; the `GraphragBuildState` machine, `useGraphragSocket`, `GraphragGraphView`
  (WS3-generalized); `useProjectRole` (WS6-lifted); the query-key + TanStack patterns.

## 10. Risks and Rollback

- **Scope size** — six workstreams. Mitigation: WS ordered and independently shippable; WS5
  (Knowledge Map) and WS4 (agent-tab rewire) split cleanly from the Concept Map workstreams —
  an α/β dossier split is offered at the gate.
- **New-slice boundary churn** — inserting `agent-groups` into the R24.06 chain and eslint
  config can break the boundary lint if mis-ordered. Mitigation: the SRS Delta (§13) fixes the
  chain position; add the slice with an empty barrel first and let lint validate before filling
  it.
- **Backend-endpoint gaps** — Phase 4 assumes Phase 1–3 endpoints exist (owner-kind config
  list, coverage query, `concept_map_enabled`/`recency_half_life_days` on PATCH, Knowledge Map
  CRUD/upload). Mitigation: a pre-flight check against the built backend; a missing endpoint is
  a backend-phase gap (recorded, not patched in the UI).
- **Agent-tab regression** — removing `graphrag_config_id` must not break agent save.
  Mitigation: a component test that the Knowledge tab persists `rag_config_id`/`knowmap_config_id`
  and no longer sends the dropped field.
- **Rollback** — frontend-only; revert the commit(s). No migration, no data. The new slice is
  additive; removing it and the sidebar/route entries fully reverts.

## 11. Acceptance Criteria

- [ ] AC-1: an `agent-groups` slice exists with group CRUD and member add/remove; a non-owner
  cannot see the mutating controls; the slice passes the boundaries lint.
- [ ] AC-2: the "Concept Maps" overview lists maps of all three owner kinds with an owner-type
  filter and a build-state pill; create lets the user pick the owner.
- [ ] AC-3: a chatroom-map panel appears in chatroom settings, a workspace-map panel in
  workspace settings, and an agent_group-map panel in the group detail — each showing that
  owner's map and build controls.
- [ ] AC-4: on a wide-layer map, a Project Owner sees an enabled `concept_map_enabled` toggle
  that PATCHes; a non-owner sees a read-only note; a chatroom map shows "inherits room access"
  with no toggle.
- [ ] AC-5: a Concept Map's `recency_half_life_days` is editable (validated, positive) and
  persists; Knowledge Maps do not show the field.
- [ ] AC-6: the agent Knowledge tab no longer renders a Concept Map attach, shows read-only
  coverage (room + groups + workspace), keeps file-RAG, and has a Knowledge Map attach.
- [ ] AC-7: a Knowledge Map config supports document upload (multipart + tus), a per-document
  allowlist editor, build/rebuild with live status, and a graph view.
- [ ] AC-8: all new strings resolve in both `en` and `zh-TW`; no bare-string or literal-`@`
  lint/test failure.
- [ ] AC-9: `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm build` pass;
  `check:openapi-drift` is green.

## 12. Test Plan

- **Component/unit** (Vitest + Vue Test Utils, mirroring source): AC-1 (group member add/remove,
  owner-gating), AC-2 (overview filter + owner picker), AC-4 (toggle owner-gating + `decided`
  flag; chatroom read-only note), AC-5 (half-life validation + persist), AC-6 (agent-tab omits
  the dropped field, persists knowmap attach), AC-7 (upload size-branch, allowlist editor).
- **Boundaries/i18n**: AC-1 (eslint boundaries pass for the new slice), AC-8 (both locales
  resolve; the literal-`@` test stays green).
- **Integration/manual (`verify`)**: end-to-end owner-panel flows against the built Phase 1–3
  backend — create a group, enable its map, set half-life, upload a Knowledge Map document,
  build, attach to an agent, confirm the Axis-1 block in a live turn.

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
  extracted from `agents` into `shared` for a cleaner home (FU-3). Not needed now; the `agents`
  barrel satisfies the dependency chain.

## 15. Deviation Log

Appended by `/build`.

## 16. Follow-ups

- FU-1 — a reusable removable-chip/tag atom in `shared/ui` if the `STable`+add-form allowlist
  pattern proves too heavy across features.
- FU-2 — a dedicated numeric slider atom for half-life (currently `SInput type="number"`).
- FU-3 — extract the shared Concept Map surface from `agents` into `shared` (Q-A/Q-3).
- FU-4 — owner-gate the existing file-RAG detail view (pre-existing debt surfaced in §9), not
  in scope here.
