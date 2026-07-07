---
type: feature
status: approved
created: 2026-07-07
requirements: [R11.12, R11.14, R11.23, R24.05]
supersedes: 2026-07-07-graphrag-phase4-frontend-rehome
---

# GraphRAG Phase 4β — Knowledge Map document UI

## 1. Summary

Phase 4β adds the Knowledge Map (Axis-1, document GraphRAG) frontend, mirroring the existing
file-RAG UI: config CRUD, document upload (multipart + resumable tus), a per-document allowlist
editor, build/rebuild with live status, and a graph view. It also completes the agent Knowledge
tab by adding the Knowledge Map attach (`knowmap_config_id`) that Phase 4α left for this half.
It is pure frontend + i18n, realizing R11.12/R11.14/R11.23.

This is the Knowledge Map half of the split Phase 4; the Concept Map re-home is Phase 4α
(`2026-07-07-graphrag-phase4a-concept-map-rehome`). Both supersede the combined
`2026-07-07-graphrag-phase4-frontend-rehome` draft.

**Phase dependencies (hard):** the Phase 3 backend (Knowledge Map CRUD/upload/build/retrieve)
must be built first. Depends on Phase 4α for the generalized concept-map graph-view (reused for
the Knowledge Map graph) and the lifted `useProjectRole` (owner-gating).

## 2. Goals and Non-goals

**Goals**
- G1 — Knowledge Map config CRUD in the `agents` slice under the "Knowledge" nav (R11.12).
- G2 — A Knowledge Map detail view (tabbed Settings/Documents) with document upload (multipart
  ≤32 MB, else tus), mirroring file-RAG (R11.12).
- G3 — A per-document allowlist editor (Project-Owner-gated) mirroring `setDocumentAgents`, with
  the secure-by-default empty-allowlist meaning surfaced (R11.23, R10.11).
- G4 — Build/rebuild trigger + live build status (reuse the build-state machine + a knowmap
  socket composable) and a graph view (reuse the 4α-generalized `GraphragGraphView`) (R11.12).
- G5 — Agent Knowledge tab: add the `knowmap_config_id` attach SSelect (R11.14), alongside the
  file-RAG attach that 4α left in place.
- G6 — All new strings i18n'd (en + zh-TW); barrels, boundaries, and transport gates respected
  (R24.05).

**Non-goals**
- Concept Map owner layers / privacy / temporal (Phase 4α).
- Any backend change — 4β only consumes Phase 3 endpoints. A missing endpoint is a backend-phase
  gap, not fixed here.
- Refactoring file-RAG UI — mirror it, do not change it (FU-4 covers owner-gating the existing
  RAG view separately).
- OCR/page provenance UI — evidence is `(document_id, chunk_idx)` granularity (Phase 3).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which slice hosts the Knowledge Map UI? | The `agents` slice, under the existing "Knowledge" nav group, mirroring file-RAG. | The Knowledge Map is agent-attached (`knowmap_config_id`) and project-scoped like file-RAG; co-locating with the RAG UI reuses `SFileUpload`/allowlist/build patterns and keeps one "Knowledge" home. |
| Q-2 | How is the document allowlist edited (no chip atom exists)? | The `STable` + add-form + `SConfirmDialog` pattern (as `McpEgressAllowlistView`), Project-Owner-gated, mirroring `setDocumentAgents`. | No removable-chip atom exists; the table+add-form is the in-repo allowlist idiom. Owner-gate the UI (unlike the ungated file-RAG detail view — that debt is FU-4). |

## 4. Current State

- **file-RAG detail is the exemplar.** `RagConfigDetailView.vue` — tabbed Settings/Documents;
  `SFileUpload` + a size-branch `onFiles` at `:291` (multipart ≤32 MB via
  `agentsApi.uploadDocumentMultipart` else `tusUpload`); a per-document allowlist editor modal via
  `agentsApi.setDocumentAgents`; ingestion progress via `useRagConfigSocket`. `RagConfigListView.vue`
  is the CRUD-list exemplar.
- **tus client.** `shared/transport/tus.ts` — resumable upload with `onProgress`, `AbortSignal`,
  base64 metadata; `purpose` union is `'chat_attachment' | 'rag_source'` (`:21`) — add a
  `'knowledge_map_source'` purpose.
- **Build-state/graph reuse (from 4α).** The `GraphragBuildState` machine, the socket-composable
  pattern (`useGraphragSocket`/`useRagConfigSocket`), and the 4α-generalized `GraphragGraphView`
  are reused for the Knowledge Map's build status and graph.
- **Agent Knowledge tab.** After 4α it renders file-RAG attach + read-only Concept Map coverage,
  with the Concept Map attach removed. 4β adds the `knowmap_config_id` SSelect next to the file-RAG
  select (options built like `ragConfigOptions` in `AgentDetailView.vue:592-595`).
- **Plumbing** (same as 4α §4): hand-written `api/index.ts` → `http`; TanStack Query + query keys;
  vee-validate + Zod + `useServerErrors`; owner-gating via the lifted `useProjectRole`; per-slice
  i18n; boundaries in `eslint.config.js`.

## 5. Design

### Options considered
- **Host slice (Q-1)** — `agents` (chosen) vs a new `knowledge` slice. `agents` reuses the RAG
  patterns and the single "Knowledge" nav; a new slice would duplicate the upload/allowlist
  machinery for no boundary gain.
- **Allowlist editor (Q-2)** — `STable`+add-form (chosen) vs a new chip atom (out of scope).

### Decision
Host in `agents`, mirror the file-RAG detail view, reuse the 4α-generalized graph-view and the
build-state/socket patterns. Consciously given up: a dedicated knowledge slice (unneeded churn)
and a chip atom (FU-1).

## 6. Detailed Changes

Two workstreams; WS1 is the bulk, WS2 is the one-field agent-tab addition.

### WS1 — Knowledge Map config UI (R11.12, R11.14, R11.23; Q-1/Q-2)
- **api** — add Knowledge Map wrappers to `agents/api/index.ts` over `http`: config CRUD, list,
  document list/upload/delete, `setDocumentAgents`-analogue, build/rebuild, status, graph. Hand-
  written types mirroring the Phase 3 backend. Query keys under `agentKeys` (e.g. `knowmapConfigs`,
  `knowmapDocuments`).
- **views** — `KnowledgeMapConfigListView.vue` (CRUD list, mirrors `RagConfigListView`) and
  `KnowledgeMapConfigDetailView.vue` (tabbed Settings/Documents, mirrors `RagConfigDetailView`):
  `SFileUpload` + size-branch upload (add `'knowledge_map_source'` to `tus.ts`), per-document
  allowlist editor (`STable`+add-form+`SModal`, Project-Owner-gated via `useProjectRole`, surfacing
  the empty-allowlist "no agent may retrieve" meaning), build/rebuild button + live status (a
  `useKnowmapSocket` composable mirroring `useRagConfigSocket`/`useGraphragSocket`, reusing the
  `GraphragBuildState` machine), and a graph view reusing the 4α-generalized `GraphragGraphView`.
- **nav/routes** — a "Knowledge Maps" item under the "Knowledge" sidebar group; routes in
  `agents/routes.ts`; i18n keys in `agents/locales/{en,zh-TW}.json` (escape `@`).

### WS2 — Agent Knowledge tab (Knowledge Map attach) (R11.14)
- Add a `knowmap_config_id` SSelect to `AgentDetailView.vue`'s Knowledge tab beside the file-RAG
  select, options built like `ragConfigOptions`, persisted via the agent PATCH. This completes the
  tab that 4α rewired (file-RAG kept, Concept Map attach removed + coverage shown).

**Plumbing** — the `'knowledge_map_source'` tus purpose; `gen:api` N/A (hand-written api); keep
`check:openapi-drift` green against the Phase 3 endpoints.

## 7. NFR Checklist

- [x] **i18n** — new strings in `agents/locales/{en,zh-TW}.json`; no bare template strings; literal
  `@` escaped.
- [x] **Audit log** — N/A on the frontend; the backend audits upload/delete/allowlist/build (Phase
  3). The UI surfaces them, records nothing.
- [x] **Tenant isolation** — project-scoped routes; owner-only allowlist edit gated by
  `useProjectRole` (`decided` flag); never bypass backend AuthZ.
- [x] **Error handling UX** — loading/error/empty states; RFC 7807 via `useServerErrors`;
  `SConfirmDialog` for deletes; upload errors surfaced from `SFileUpload`/tus.
- [x] **Performance** — TanStack Query caches; large-file upload via resumable tus; build/ingest
  progress via WS + poll backstop.

## 8. Security Considerations

Touches file upload, per-Agent document allowlist, and tenant boundaries — required.

- **Allowlist editing.** The document allowlist editor mirrors `setDocumentAgents` (Project-Owner-
  gated backend); the UI validates ids against agents bound to the config before submit and shows
  the secure-by-default empty-allowlist meaning ("no agent may retrieve"). Gate the UI with
  `useProjectRole` — do not copy the ungated file-RAG detail view (FU-4).
- **Upload surface.** Reuse `SFileUpload`'s client size check and the native `accept` MIME filter,
  but rely on the backend for authoritative MIME/scan gating (Phase 3); show the quarantine/scan
  state, never index-and-hide.
- **Client gating is UX, not security.** The backend (Phase 3) authorizes every mutation; the UI
  degrades gracefully on a 403.
- **No transport bypass / no `v-html`.** Calls via slice `api/` → `http`; the new WS channel via
  the shared client; graph/entity labels render as text.

## 9. Quality Notes

- **Existing debt (do not imitate).** `RagConfigDetailView.vue` renders upload/allowlist for
  everyone and relies on a backend 403; the Knowledge Map UI must owner-gate with `useProjectRole`
  (FU-4 tracks fixing the RAG view separately).
- **Patterns to follow.** CRUD list (`RagConfigListView.vue`); detail with upload + allowlist +
  progress (`RagConfigDetailView.vue`); owner-gating (`McpEgressAllowlistView.vue`); build-state +
  socket (`useGraphragSocket.ts`/`useRagConfigSocket.ts`); forms (`schemas.ts` + `useForm` +
  `useServerErrors`).
- **Reuse inventory (import, do not re-create).** `SFileUpload`, `SModal`, `STable`, `STabs`,
  `SConfirmDialog`, `SStatusBadge`, `SFormField`, `SInput`, `SEmptyState`, `SPagination` from
  `@shared/ui`; `http` + `tusUpload` from `@shared/transport`; `useServerErrors`; the
  `GraphragBuildState` machine and the 4α-generalized `GraphragGraphView`; `useProjectRole`
  (lifted in 4α).

## 10. Risks and Rollback

- **Backend-endpoint gaps** — 4β assumes Phase 3 endpoints (Knowledge Map CRUD, upload, allowlist
  set, build/rebuild, status, graph). Mitigation: pre-flight against the built backend; a missing
  endpoint is a backend-phase gap (recorded, not patched in the UI).
- **4α dependency** — the generalized `GraphragGraphView` and lifted `useProjectRole` must land
  first. Mitigation: build 4β after 4α.
- **Upload UX regressions** — mirror the file-RAG size-branch exactly; a mis-set threshold breaks
  large uploads. Mitigation: reuse the constant and add a component test.
- **Rollback** — frontend-only; revert the commit(s). Additive routes/nav; no migration.

## 11. Acceptance Criteria

- [ ] AC-1: a Knowledge Map config can be created, listed, and deleted under the "Knowledge" nav.
- [ ] AC-2: the detail view uploads documents (multipart + tus), lists them with status, and
  deletes them.
- [ ] AC-3: a per-document allowlist editor is Project-Owner-gated, validates ids against
  config-bound agents, and shows the empty-allowlist "no agent may retrieve" meaning.
- [ ] AC-4: a build/rebuild button triggers a build with live status (build-state machine +
  socket); a graph view renders the Knowledge Map.
- [ ] AC-5: the agent Knowledge tab has a `knowmap_config_id` attach beside the file-RAG attach,
  and it persists.
- [ ] AC-6: all new strings resolve in both `en` and `zh-TW`; no bare-string or literal-`@`
  lint/test failure.
- [ ] AC-7: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` pass; `check:openapi-drift`
  green.

## 12. Test Plan

- **Component/unit** (Vitest + Vue Test Utils): AC-2 (upload size-branch), AC-3 (allowlist editor
  owner-gating + id validation + empty meaning), AC-4 (build trigger + status), AC-5 (agent-tab
  knowmap attach persists).
- **i18n**: AC-6 (both locales resolve; literal-`@` test green).
- **Integration/manual (`verify`)**: against the built Phase 3 backend — upload a document, build,
  attach to an agent, confirm the Axis-1 block and allowlist behavior in a live turn.

## 13. SRS Delta

None. 4β realizes existing R11.12/R11.14/R11.23 in UI and adds no slice or structural change (the
`agent-groups` slice and the R24.06 amendment belong to Phase 4α).

## 14. Open Questions

None blocking.

## 15. Deviation Log

Appended by `/build`.

## 16. Follow-ups

- FU-4 — owner-gate the existing file-RAG detail view (pre-existing debt surfaced in §9), not in
  scope here.
