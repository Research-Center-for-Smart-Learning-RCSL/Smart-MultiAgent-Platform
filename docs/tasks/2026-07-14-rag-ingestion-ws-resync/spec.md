---
type: bugfix
status: draft
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

Mirror `useBuildStateSocket`'s recovery loop in `useRagConfigSocket.ts`:

**7.1 Authoritative resync.** Replace `syncOnReconnect` (`:61-71`) with a `syncState(configId)`
that calls `agentsApi.listDocuments(configId)` and rebuilds the whole `progress` ref by
deriving from per-document `status`, then invalidates `agentKeys.ragDocuments(configId)` (and
keeps the existing config-list invalidation). Derivation over the document set:
- any document `status === 'ingesting'` → `state = 'ingesting'`;
- else any `status === 'failed'` → `state = 'failed'` (surface a generic error string);
- else if there are documents and none ingesting → `state = 'ready'`;
- else (no documents) → `state = 'idle'`.
- `documentsTotal = documents.length`; `documentsProcessed =` count of non-`ingesting`
  documents (`ready + failed + quarantined`).

This yields a correct bar on both late subscribe (F-28) and post-terminal reconnect (F-21).
Live events continue to refine `state` afterward (e.g. a live `ingestion.indexing` can move
`ingesting → indexing`, which document status does not distinguish).

**7.2 Wire on connect, reconnect, and mount.** Keep `channel.onStatus((connected) => { if
(connected) void syncState(configId) })` (it already fires on initial connect and every
reconnect, `:74-77`). Also run `syncState` once on mount/`configId` change so the page seeds
authoritative state even before the socket connects (mirroring `useBuildStateSocket`'s
`initialState` seeding at `:100-108`).

**7.3 Backstop poll.** Add a bounded poll (reuse `useBuildStateSocket`'s `POLL_FALLBACK_MS` =
15s, `:17,63-70`) that re-runs `syncState` while `state ∈ {ingesting, indexing}`, so a dropped
terminal frame still self-heals without a reload, and stops polling once terminal/idle.

**7.4 Non-terminal live event hardening.** In the `ingestion.progress` handler (`:42-44`), if
`state` is currently `'idle'`, set it to `'ingesting'` (progress frames imply active ingestion),
and adopt `ev.total` into `documentsTotal` when the frame carries it (the wire already sends
`total` at `ingest_service.py:342` but the handler ignores it today, so a mid-upload growing
corpus keeps a correct denominator). This is a cheap belt-and-braces for the window between
subscribe and the first `syncState` resolve; the authoritative `syncState` remains the primary
fix.

**7.5 No backend change.** The WS route and events are unchanged (Q-2); the authoritative
`GET .../documents` endpoint already returns everything needed
(`rag.py:394-420`). The frontend api-client wrapper `listDocuments(configId)` already exists
(`frontend/src/slices/agents/api/index.ts` — `RagDocument.status`).

No data or schema changes.

## 8. Regression Test Plan

Frontend (`frontend/src/slices/agents/composables/__tests__/` — Vitest):

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

Primary red-first test: (1).

## 9. Risks and Rollback

- **Derivation vs live granularity.** Document `status` has no `indexing` sub-state, so a
  resync during indexing shows `ingesting` until the next live `ingestion.indexing` event or
  completion. Acceptable — it never under-reports progress and self-corrects; documented.
- **Mid-upload snapshot.** A resync mid multi-file upload derives `documentsTotal` from the
  documents that exist at that instant, which can be below the eventual total (rows are created
  as files arrive). The bar may momentarily read e.g. `2/2` before a later file appears; the
  next live `ingestion.progress`/`started` frame (which carries `total`, §7.4) and the backstop
  poll correct it. Acceptable for a recovery snapshot.
- **Poll load.** A 15s poll per open in-progress config detail page; bounded to in-progress
  states and stopped at terminal, matching the existing `useBuildStateSocket` budget.
- **Rollback** — revert the composable to the prior `syncOnReconnect`; frontend-only, no API
  or schema change.

## 10. Acceptance Criteria

- [ ] AC-1: The reconnect-after-terminal test (§8.1) fails before the fix and passes after.
- [ ] AC-2: On connect/reconnect/mount, the composable fetches `listDocuments(configId)`,
  rebuilds `progress` (state + total + processed) from per-document `status`, and invalidates
  the documents query.
- [ ] AC-3: Opening the detail page mid-ingestion shows the progress bar (derived
  `state='ingesting'`), without relying on an `ingestion.started` replay (F-28).
- [ ] AC-4: After a missed terminal event, reconnect (or the backstop poll within ~15s)
  transitions the bar to `ready`/`failed` and refetches documents, with no manual reload
  (F-21).
- [ ] AC-5: A `failed` document is reflected as `state='failed'` on resync.
- [ ] AC-6: `pnpm test`, `pnpm lint`, `pnpm typecheck`, and `pnpm build` pass in `frontend/`.

## 11. SRS Delta

None. This restores the documented [R24.23] reconnect-resync behavior; no new requirement.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (backend snapshot, deferred):** a server-side snapshot on WS subscribe would remove
  the need for a client fetch and shrink the resync window; deliberately not built here (Q-2),
  since the REST-fetch pattern matches [R24.23] and the sibling sockets. Revisit if the poll
  proves insufficient.
- **FU-2 (shared resync helper):** the RAG and `useBuildStateSocket` recovery loops now share
  structure (onStatus → fetch authoritative → apply → backstop poll). A future refactor could
  extract a common resync primitive; out of scope for this bugfix.
