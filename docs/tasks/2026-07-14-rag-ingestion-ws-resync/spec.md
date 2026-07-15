---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R24.22, R24.23]
---

# F-21 / F-28: RAG ingestion WebSocket cannot recover state on reconnect or late subscribe

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-21, and its
confirmed sibling F-28 folded in per the audit hand-off).

## 1. Summary

The RAG ingestion WebSocket composable applies only live events and never re-derives its
local `progress` state from authoritative REST data, so two symptoms appear. **F-21
(terminal missed):** if the client disconnects during indexing and misses
`ingestion.completed`/`failed`, reconnect only re-lists configs — it never rebuilds
`progress` or invalidates the documents query — so the progress bar and per-document status
stay stale indefinitely. **F-28 (non-terminal missed):** the `ingestion.progress` handler
never sets `state`, and `ingestion.started` (the only event that sets `state='ingesting'`)
is not replayed on late subscribe, so opening the detail page mid-ingestion leaves
`state='idle'` and the progress bar never shows. Both share one root cause — no
authoritative resync on (re)connect — and the codebase already has the correct pattern in
`useBuildStateSocket` (fetch authoritative state on `onStatus`, apply locally, plus a backstop
poll). The fix rewrites the RAG composable's resync to fetch documents and derive `progress`
from them, mirroring that pattern, satisfying [R24.23]'s "replay a delta fetch before resuming
event application" contract.

## 2. Observed vs Expected

- **Observed:**
  - `syncOnReconnect` fires on every connect/reconnect but only calls `listRagConfigs`,
    finds the config, and invalidates the config-**list** query; it never touches the local
    `progress` ref and never invalidates the documents query
    (`frontend/src/slices/agents/composables/useRagConfigSocket.ts:61-71`, wired at `:74-77`).
  - `ingestion.progress` sets only `documentsProcessed` and never `state`
    (`useRagConfigSocket.ts:42-44`); `ingestion.started` (sets `state='ingesting'`,
    `:34-41`) is a live-only event, not replayed.
  - The detail view shows the bar only for `state ∈ {ingesting, indexing}`
    (`frontend/src/slices/agents/views/RagConfigDetailView.vue:429-431`) and refetches
    documents only on a live terminal `state` transition (`:161-168`) — so a missed terminal
    event never refetches.
  - The backend WS route emits only live events; it sends no snapshot/replay on (re)subscribe
    (`backend/contexts/knowledge/interfaces/ws_config_route.py:47-85`).
  - Authoritative state exists but is unused: `GET /api/rag-configs/{id}/documents` returns
    per-document `status` (`ingesting`/`ready`/`failed`/`quarantined`)
    (`backend/app/api/v1/rag.py:394-420`, `RagDocumentOut` `:109-120`); the config-detail
    endpoint carries no progress field (`:274-291`).
- **Expected** — [R24.23]: "On reconnect, composables replay a delta fetch ... before
  resuming event application, to avoid gaps." On connect, reconnect, and late subscribe, the
  composable must fetch authoritative document state and rebuild `progress` (state + counts)
  before/alongside resuming live events, so the UI reflects reality regardless of missed
  events. This is exactly what the sibling `useBuildStateSocket` already does.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Cover F-28 (non-terminal late-subscribe) in this dossier? | **Yes** — one fix covers both. | Same root cause and same lines; the authoritative-resync rewrite fixes terminal (F-21) and non-terminal (F-28) together. Splitting would duplicate the change and collide. |
| Q-2 | Frontend-only, or add a backend subscribe-time snapshot? | **Frontend-only**, mirroring `useBuildStateSocket`. | The authoritative REST endpoint already exists; the [R24.23] contract is a client-side delta fetch; and the sibling graph/knowmap sockets already recover this way. A backend snapshot-on-subscribe is a larger change for no additional correctness. |

## 4. Reproduction

Preconditions: a RAG config with an in-progress upload.

- **F-21:** open the detail page during indexing (bar showing). Kill the socket (drop network)
  and let ingestion reach `completed` while disconnected. Restore the network. Observed: the
  bar stays up and document rows keep their pre-terminal status; only a manual reload or an
  unrelated event clears it (`useRagConfigSocket.ts:61-71` never rebuilds progress/documents).
- **F-28:** start an upload, then open the detail page fresh while ingestion is running. Only
  `ingestion.progress` frames arrive (no `started` replay); `progress.state` stays `'idle'`
  (`:42-44`), so `showProgress` is false (`RagConfigDetailView.vue:429-431`) and the bar never
  appears though ingestion is active.

## 5. Root Cause Analysis

The composable treats the WebSocket as the sole source of truth and applies events forward-only.
The root cause is the absence of an authoritative resync: `syncOnReconnect` fetches the wrong
resource (config list) and discards it without updating `progress` or documents
(`useRagConfigSocket.ts:61-71`). Two aggravating factors flow from it: `ingestion.progress`
carries no `state` (`:42-44`), and `ingestion.started` is never replayed — so any client that
was not connected at `started` (late subscribe = F-28) or missed the terminal frame
(disconnect = F-21) has a `progress` object that no code path can correct. Fixing the resync to
derive `progress` from `listDocuments` corrects both, because document `status` is the durable
truth the events were only mirroring.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — designer-facing ingestion feedback on the RAG config detail page after any
  routine reconnect or any page open during active ingestion; both the progress bar and the
  per-document status table.
- **Sibling suspects:**
  - **`useBuildStateSocket` (cleared — this is the exemplar).** GraphRAG/Knowmap sockets already
    resync correctly: `onStatus(connected → syncStatus → applyState)` plus a 15s backstop poll
    and a seeded `initialState`
    (`frontend/src/slices/agents/composables/useBuildStateSocket.ts:87-93,100-120`; callers
    `useGraphragSocket.ts:12-28`, `useKnowmapSocket.ts:13-22`). The RAG fix mirrors this. The
    difference: graph/knowmap read a single authoritative `state`/`last_build_state` field,
    whereas RAG has none on the config — RAG must derive `progress` from the per-document
    `status` list instead (§7).
  - **`RagConfigDetailView` terminal-watch document refetch (confirmed, in scope).** The
    document refetch is bound to a live `state` transition (`RagConfigDetailView.vue:161-168`);
    once the composable rebuilds `state` from authoritative data on resync, this watch fires
    correctly, and the resync itself also invalidates the documents query as a backstop.
  - **Knowmap detail auto-build visibility (cleared — separate finding).** F-22 covers the
    Knowledge Map idle-page case; different composable/view, not in this dossier.

## 7. Fix Design

Mirror `useBuildStateSocket`'s recovery loop in `useRagConfigSocket.ts`, and make `progress` a
consistently **document-level** view owned by a single writer.

**7.1 Authoritative resync — the single writer of `progress`.** Replace `syncOnReconnect`
(`:61-71`) with a `syncState(configId)` that calls `agentsApi.listDocuments(configId)`, rebuilds
the whole `progress` ref from per-document `status`, then invalidates
`agentKeys.ragDocuments(configId)` (and keeps the config-list invalidation). Derivation over the
document set:
- any document `status === 'ingesting'` → `state = 'ingesting'`;
- else any `status === 'failed'` → `state = 'failed'` (surface a generic error string);
- else if there are documents and none ingesting → `state = 'ready'`;
- else (no documents) → `state = 'idle'`.
- `documentsTotal = documents.length`; `documentsProcessed =` count of non-`ingesting`
  documents (`ready + failed + quarantined`).

**7.2 WS events become triggers, not count-writers (fixes a unit bug).** The live `ingestion.*`
frames are **chunk-granular for one document**, not corpus-document-granular: `ingestion.progress`
`processed`/`total` are `total_chunks = len(pieces)` for a single document
(`backend/contexts/knowledge/application/ingest_service.py:321,336,342`), and each
`ingestion.started` sends `{"total": 1}` per document (`:155,223`;
`backend/contexts/knowledge/application/rag_tus_finalizer.py:159`). The current handlers write
these into the document-level `documentsTotal`/`documentsProcessed` (`useRagConfigSocket.ts:37,43,50`),
which corrupts the counts across a multi-file upload (a 47-chunk file would set the document
denominator to 47). So `syncState` must be the **only** writer of the count fields, and the WS
handlers become change-triggers:
- `ingestion.started` / `ingestion.indexing` / `ingestion.completed` / `ingestion.failed` →
  call `syncState(configId)` (re-derive document-level truth); for responsiveness, optimistically
  set `state` (`ingesting`/`indexing`/`ready`/`failed`) immediately, but let `syncState`
  reconcile the counts.
- `ingestion.progress` → set `state = 'ingesting'` if currently `'idle'` (instant feedback for a
  late subscriber, F-28) and call a **debounced** `syncState` (~750 ms, to absorb the per-embed-
  batch frame storm). It must **not** write `ev.processed`/`ev.total` into the document-level
  counts.

This unifies `progress` on one document-level source, eliminating the pre-existing chunk-vs-
document unit mix as a side effect. The trade-off — no sub-document chunk-level bar — is
acceptable (the bar tracks documents; see FU-3).

**7.3 Wire on connect, reconnect, and mount.** Keep `channel.onStatus((connected) => { if
(connected) void syncState(configId) })` (already fires on initial connect and every reconnect,
`:74-77`). Also run `syncState` once on mount/`configId` change so the page seeds authoritative
state even before the socket connects (mirroring `useBuildStateSocket`'s `initialState` seeding
at `:100-108`).

**7.4 Backstop poll.** Add a bounded poll (reuse `useBuildStateSocket`'s `POLL_FALLBACK_MS` =
15s, `:17,63-70`) that re-runs `syncState` while `state ∈ {ingesting, indexing}`, so a dropped
terminal frame still self-heals without a reload, and stops polling once terminal/idle.

**7.5 No backend change.** The WS route and events are unchanged (Q-2); the authoritative
`GET .../documents` endpoint already returns everything needed
(`rag.py:394-420`). The frontend api-client wrapper `listDocuments(configId)` already exists
(`frontend/src/slices/agents/api/index.ts` — `RagDocument.status`).

No data or schema changes.

## 8. Regression Test Plan

Frontend (Vitest) — these are **net-new**: there is no existing `useRagConfigSocket` or
`useBuildStateSocket` test to break or rewrite. Mirror the sibling socket-recovery tests
`frontend/src/slices/agents/__tests__/useGraphragSocket.test.ts` and `useKnowmapSocket.test.ts`
(which exercise the same `onStatus → fetch → apply` + poll pattern). Note the directory
inconsistency: those sibling tests live in `agents/__tests__/`, while `composables/__tests__/`
also exists — place the new test to match the sibling socket tests (`agents/__tests__/`) unless
following local convention dictates otherwise; Vitest collects both.

1. **Reconnect after missed terminal (F-21)** (new): mount with an in-progress `progress`
   (`state='ingesting'`); the socket reports disconnect then reconnect while `listDocuments`
   now returns all `ready`; assert `syncState` sets `progress.state='ready'`,
   `documentsProcessed===documentsTotal`, and invalidates `agentKeys.ragDocuments(configId)`.
   Fails today — `syncOnReconnect` never touches `progress` or the documents query.
2. **Late subscribe mid-ingestion (F-28)** (new): mount when `listDocuments` returns a mix
   including an `ingesting` document and no `started` event is delivered; assert
   `progress.state==='ingesting'` and counts derived from the document set (so the detail
   view's `showProgress` would be true). Fails today — `state` stays `'idle'`.
3. **Failed document derivation** (new): `listDocuments` includes a `failed` document and no
   `ingesting`; assert `state==='failed'`.
4. **Backstop poll self-heals** (new): while `state='indexing'` and no live terminal event
   arrives, the poll re-runs `syncState` and transitions to `ready` when documents complete;
   polling stops at terminal/idle.
5. **Live `ingestion.progress` does not corrupt document counts** (new, unit-bug regression):
   with two documents (`documentsTotal===2`) and a live `ingestion.progress` frame carrying a
   chunk `total` of e.g. 47, assert `documentsTotal` stays 2 (the frame does not overwrite it)
   and that a debounced `syncState` re-derives the counts. Fails against the current handler,
   which sets `documentsProcessed = ev.processed` (chunk count) directly.

Primary red-first test: (1).

## 9. Risks and Rollback

- **Derivation vs live granularity.** Document `status` has no `indexing` sub-state, so a
  resync during indexing shows `ingesting` until the next live `ingestion.indexing` event or
  completion. Acceptable — it never under-reports progress and self-corrects; documented.
- **Mid-upload snapshot.** A resync mid multi-file upload derives `documentsTotal` from the
  documents that exist at that instant, which can be below the eventual total (rows are created
  as files arrive). The bar may momentarily read e.g. `2/2` before a later file appears; the
  next live `ingestion.started`/`progress` frame triggers a re-`syncState` (§7.2) and the
  backstop poll corrects it. Acceptable for a recovery snapshot.
- **Debounce / refetch load.** Routing `ingestion.progress` through a debounced `syncState`
  turns a per-embed-batch frame storm into at most ~1 document refetch per debounce window; the
  immediate `state` set keeps the bar responsive. Without the debounce, a large multi-chunk file
  would refetch documents on every batch.
- **Poll load.** A 15s poll per open in-progress config detail page; bounded to in-progress
  states and stopped at terminal, matching the existing `useBuildStateSocket` budget.
- **Rollback** — revert the composable to the prior `syncOnReconnect`; frontend-only, no API
  or schema change.

## 10. Acceptance Criteria

- [x] AC-1: The reconnect-after-terminal test (§8.1) fails before the fix and passes after.
- [x] AC-2: On connect/reconnect/mount, the composable fetches `listDocuments(configId)`,
  rebuilds `progress` (state + total + processed) from per-document `status`, and invalidates
  the documents query.
- [x] AC-3: Opening the detail page mid-ingestion shows the progress bar (derived
  `state='ingesting'`), without relying on an `ingestion.started` replay (F-28).
- [x] AC-4: After a missed terminal event, reconnect (or the backstop poll within ~15s)
  transitions the bar to `ready`/`failed` and refetches documents, with no manual reload
  (F-21).
- [x] AC-5: A `failed` document is reflected as `state='failed'` on resync.
- [x] AC-6: `syncState` is the only writer of `documentsTotal`/`documentsProcessed`; a live
  `ingestion.progress` frame (chunk-granular) never overwrites the document-level counts, and
  the counts stay consistent across a multi-document upload.
- [x] AC-7: `pnpm test`, `pnpm lint`, `pnpm typecheck`, and `pnpm build` pass in `frontend/`.

## 11. SRS Delta

None. This restores the documented [R24.23] reconnect-resync behavior; no new requirement.

## 12. Deviation Log

- **D-1 (optimistic state — forward-only, not terminal).** §7.2 prescribed optimistically
  setting `state` to `ready`/`failed` on `ingestion.completed`/`ingestion.failed` for
  responsiveness, letting `syncState` reconcile counts. Verification found both terminal frames
  are **per-document**, not per-corpus (they carry `document_id`:
  `ingest_service.py:396,414`), so optimistically flipping the whole-corpus bar to `ready`/
  `failed` on one document's terminal event wrongly reports the corpus done mid-upload, then
  `syncState` snaps it back — a visible wrong-direction flicker on a multi-file upload. The
  implementation keeps the spec's architecture (syncState is the sole authority for `state` and
  counts, derived from document status) but makes optimistic hints **forward-only** —
  `idle → ingesting` on `started`/`progress`, and `→ indexing` on `indexing` — and lets
  `syncState` alone decide the terminal/corpus state authoritatively. This never regresses F-28
  responsiveness (the bar still appears instantly) and removes the flicker. `deriveProgress`
  additionally **preserves a live `indexing`** over a derived `ingesting` (document status has no
  `indexing` sub-state), so a resync during indexing does not downgrade the finer live state
  (strictly better than §9's "shows ingesting until the next event", never a downgrade).
- **D-2 (view unchanged).** §6/§7.3 noted the detail view's terminal-watch document refetch
  (`RagConfigDetailView.vue:166-173`) would fire correctly once the composable rebuilds `state`.
  Confirmed: no view edit was needed — the composable rewrite alone drives that watch, and
  `syncState` also invalidates `ragDocuments` as an independent backstop. This dossier is
  composable + test only.

## 13. Follow-ups

- **FU-1 (backend snapshot, deferred):** a server-side snapshot on WS subscribe would remove
  the need for a client fetch and shrink the resync window; deliberately not built here (Q-2),
  since the REST-fetch pattern matches [R24.23] and the sibling sockets. Revisit if the poll
  proves insufficient.
- **FU-2 (shared resync helper):** the RAG and `useBuildStateSocket` recovery loops now share
  structure (onStatus → fetch authoritative → apply → backstop poll). A future refactor could
  extract a common resync primitive; out of scope for this bugfix.
- **FU-3 (sub-document chunk progress):** unifying `progress` on document granularity (§7.2)
  drops the intra-document chunk-level bar the current `ingestion.progress` handler attempts.
  Document-level feedback is sufficient for this fix; a future enhancement could add a separate
  per-document chunk sub-progress field without re-mixing it into the document counts.
