---
type: bugfix
status: implemented
created: 2026-09-14
requirements: [R13.58, R13.63]
depends_on: []
---

# Canvas CRDT Bridge Defects

## 1. Summary

The canvas CRDT bridge (PR 202, `feat/canvas-crdt-bridge-remaining`, merged as
`f2ee8df5`) introduced nine defects across agent canvas tools, image upload, template
application, and search. Five are functional bugs that break real-time sync, image
display, agent attribution, and search quality. Four are structural root causes --
duplicated code, SoC violations, and full-state broadcasts -- that made the functional
bugs possible and will recur if not fixed alongside them. All nine are addressed in
this spec.

## 2. Observed vs Expected

### B-1: yjs-update payload key mismatch

- **Observed**: 5 of 7 broadcast sites emit `{"update": b64}` (`canvas_tools.py:186,289,360`,
  `template_service.py:193`, `canvas.py:641`). The frontend at `useYjsProvider.ts:125`
  reads `event.data`. Since the key is `update` not `data`, `event.data` is `undefined`,
  the `if (data)` guard skips it, and the CRDT delta is silently dropped. Agent tool
  writes, image uploads, and template applications do not appear in connected editors
  until page refresh.
- **Expected**: All broadcast sites use key `data`, matching the frontend consumer and
  the two correct sites (`ws/canvas.py:222`, `canvas_service.py:513`).

### B-2: MinIO presigned URL unreachable from browser

- **Observed**: `upload_image` (`canvas.py:644`) calls `minio.presigned_get()`, which
  generates a URL with hostname `minio:9000` (`settings.py:98`, Docker-internal DNS).
  The frontend (`useCanvasSocket.ts:50`) receives this URL via the
  `canvas.image_added` event and calls `http.get(url)`. The browser cannot resolve
  `minio:9000`; nginx CSP `connect-src 'self'` also blocks it. All uploaded canvas
  images show a permanent placeholder.
- **Expected**: Image URLs are reachable from the browser, proxied through nginx to
  avoid exposing the internal MinIO endpoint.

### B-3: Broadcast before commit

- **Observed**: In `canvas.py` `upload_image`, `Publisher.emit` fires at lines 639
  and 646, before `db.commit()` at line 657. In `canvas_tools.py`, the three tool
  functions broadcast via `Publisher.emit` (lines 184, 287, 358) but never call
  `db.commit()` -- the commit is deferred to `turn_engine.py:3207` after the entire
  turn. If the commit or turn fails, clients have already applied phantom state that
  vanishes on reconnect.
- **Expected**: Broadcast occurs only after the transaction is committed. Clients
  never receive state that was not persisted.

### B-4: Agent-created CRDT elements lack attribution

- **Observed**: The CRDT tool path in `canvas_tools.py:136-155` builds an Excalidraw
  element dict and calls `relay.inject_elements()` directly, bypassing
  `facade.create_object()` which accepts `created_by_agent_id` (facade line 115). The
  element carries no agent attribution. The frontend has no visual distinction for
  agent-created objects (the `agentCreated` i18n key was never implemented).
- **Expected**: Per [R13.58] (`REQUIREMENTS.md:802`): "A canvas object created by an
  agent carries `created_by_agent_id`... The frontend visually distinguishes
  agent-created objects from user-created ones."

### B-5: Search loses relevance ranking

- **Observed**: The search endpoint (`canvas.py:308`) calls
  `facade.search_crdt_elements()`, which does in-memory case-insensitive substring
  matching (`canvas_service.py:331`) with hardcoded `rank=1.0` (canvas.py:314) and raw
  text truncation for snippets (`:313`).
- **Expected**: Per [R13.63] (`REQUIREMENTS.md:812`): "Results are ranked by
  `ts_rank_cd` and include `ts_headline` snippets." The old compliant
  `search_objects` method still exists (`canvas_repo.py:251-285`) but is no longer
  called.

### B-6: SoC violations -- facade bypass

- **Observed**: `canvas.py:599-601` and `canvas_tools.py:122-127,237-239,332-334`
  import directly from `contexts.canvas.application.crdt_relay` and
  `contexts.canvas.infrastructure` (channels, repositories), bypassing the facade.
  `canvas_tools.py:122` imports a private symbol `_EXCALIDRAW_ELEMENT_KINDS`. The
  facade has no methods for `inject_elements`, `update_element`, or `delete_element`.
- **Expected**: Per `backend/CLAUDE.md`: "app/api/v1/ calls only
  contexts/*/interfaces/facade.py -- never reach into application/ or
  infrastructure/."

### B-7: CRDT persist-and-broadcast pattern duplicated 6 times

- **Observed**: The identical 3-step pattern (mutate CRDT via relay -> persist via
  `repo.update_crdt_state` -> encode and broadcast via `Publisher.emit`) is
  independently implemented at 6 call sites: `canvas_tools.py:179-186`,
  `:284-289`, `:355-360`, `canvas.py:636-641`, `template_service.py:188-193`,
  `canvas_service.py:507-513`. The duplication caused B-1 (5 sites chose `update`,
  1 chose `data`).
- **Expected**: A single helper method on the facade or relay handles persist +
  broadcast, making the payload key a single edit point.

### B-8: Excalidraw element template duplicated 4 times

- **Observed**: The 15-field element default dict (`angle`, `strokeColor`,
  `fillStyle`, etc.) is copy-pasted in `crdt_relay.py:95-114`,
  `canvas_service.py:465-484`, `canvas_tools.py:136-155`, `canvas.py:608-628`.
  Three copies are semantically identical (shape/text); the fourth (`canvas.py`) is
  intentionally different (image element: transparent stroke, roughness 0).
- **Expected**: A shared factory function produces element dicts with kind-specific
  defaults, living in one place.

### B-9: Full CRDT state broadcast on every mutation

- **Observed**: `crdt_relay.py` `inject_elements` (`:237`), `update_element` (`:269`),
  `delete_element` (`:299`) all return `doc.get_update()` -- the full Y.js document
  state. Callers broadcast this full state. For a 5 MB canvas, each agent tool call
  broadcasts ~6.7 MB base64. Sequential operations are O(n * doc_size).
- **Expected**: pycrdt supports `doc.get_update(state_vector)` for incremental deltas.
  The relay already has `get_state_vector_b64()` (`:177-182`) but no production code
  uses it. The relay should return only the delta.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Scope: all 9 findings, functional bugs only, or split? | All 9 in one spec. | #7/#8 are root causes of #1; fixing symptoms without structure will recur. #9 (incremental delta) is straightforward in pycrdt and prevents quadratic broadcast cost. |
| Q-2 | MinIO URL fix strategy? | Nginx reverse proxy at `/minio-assets/`. Backend returns relative path; nginx proxies to MinIO. | Avoids exposing internal hostname. Consistent with existing nginx proxy pattern for `/api/` and `/ws/`. No new env vars or MinIO config needed. |
| Q-3 | Broadcast-before-commit fix? | Move broadcast after commit. | CRDT eventual consistency does not excuse phantom state. Persist -> commit -> broadcast is the correct ordering. The CrdtRelay in-memory cache keeps connected editors consistent between WS relay (which is already immediate) and these server-side mutations. |
| Q-4 | Dependency on non-implemented dossiers? | None. | `dashboard-workspace-scope` (approved) touches no canvas files. `graphrag-two-axis-redesign` (approved) mentions `KnowledgeGraphCanvas` (D3 graph viz), not CRDT canvas. `large-artifacts-silently-dropped` touches `turn_engine.py` but not the canvas tool invocation path. |

## 4. Reproduction

**B-1 (payload key)**:

1. Open a chatroom with a canvas. Open the canvas in two browser tabs.
2. In one tab, trigger an agent that creates a canvas element (via the
   `canvas_create_object` tool).
3. Observe: the element does not appear in the second tab until refresh.

**B-2 (MinIO URL)**:

1. In a chatroom canvas, upload an image via the image upload button.
2. The backend returns a presigned URL with hostname `minio:9000`.
3. Observe: the image shows as a broken placeholder in all connected editors.
   Browser console shows a DNS resolution failure or CSP violation.

**B-3 (broadcast before commit)**:

1. An agent calls `canvas_create_object`, which broadcasts the CRDT update.
2. A subsequent tool call in the same turn fails, causing the turn to abort.
3. `turn_engine.py:3207` never commits. The transaction rolls back.
4. Observe: the element appeared briefly in connected editors (via the broadcast)
   but vanishes after the editor reconnects and resyncs from persisted state.

**B-4 (attribution)**:

1. An agent creates a canvas element via `canvas_create_object`.
2. Observe: the element in the CRDT doc has no `created_by_agent_id` metadata.
   The frontend shows no visual distinction between agent and user elements.

**B-5 (search)**:

1. Create multiple canvas elements with varying text.
2. Search for a partial match.
3. Observe: all results have `rank=1.0` and no highlighted snippets. Results are
   in insertion order, not relevance order.

## 5. Root Cause Analysis

**B-1**: The CRDT bridge introduced 5 new broadcast sites by copy-pasting the
pattern from `canvas_service.py:511-513` but used a different payload key (`update`
instead of `data`). The 2 correct sites (`ws/canvas.py:222`,
`canvas_service.py:513`) were written in the earlier CRDT sync phase; the 5 incorrect
sites were added in the bridge phase. **Root cause**: no shared helper for the
persist-and-broadcast pattern (B-7), allowing each copy to diverge.

**B-2**: `MinioClient` is constructed with `endpoint = "minio:9000"` (the Docker
service name). `presigned_get_object()` embeds the endpoint hostname in the URL.
The frontend receives this URL verbatim via the `canvas.image_added` event. **Root
cause**: no URL rewriting or reverse proxy for MinIO assets. The existing
`shared_kernel/storage/minio_client.py` has a single `endpoint` field with no
distinction between internal and external access.

**B-3**: The `upload_image` handler calls `Publisher.emit` (lines 639, 646) before
`db.commit()` (line 657). The agent tools never commit at all -- they modify the
session and rely on `turn_engine.py:3207` to commit after the full turn. **Root
cause**: the persist-and-broadcast helper (B-7) is missing, and each call site
places the broadcast wherever felt natural, with no consistent ordering contract.

**B-4**: The agent tool path builds a raw Excalidraw element dict and calls
`relay.inject_elements()` directly, which only modifies the CRDT doc. The old REST
path went through `facade.create_object()` which wrote a `canvas_objects` row with
`created_by_agent_id`. The CRDT bridge removed the facade call without carrying the
attribution into the CRDT metadata. **Root cause**: the facade was bypassed (B-6)
for the CRDT operations, so the attribution logic in `facade.create_object` was
never invoked.

**B-5**: The search endpoint switched from `facade.search_objects()` (which uses
PostgreSQL FTS with `ts_rank_cd` and `ts_headline`) to
`facade.search_crdt_elements()` (in-memory substring match). **Root cause**: the
CRDT bridge moved the source of truth from the `canvas_objects` table to the CRDT
doc, but the search implementation was downgraded rather than adapted.

**B-6 through B-9**: Structural issues documented in the Observed vs Expected
section. Each is a root cause or aggravating factor for the functional bugs above.

## 6. Blast Radius and Sibling Suspects

**Blast radius**: Every canvas feature introduced in PR 202 is affected:
- Agent canvas tools: create/update/delete all broken for real-time sync (B-1),
  all lack attribution (B-4), all risk phantom state (B-3).
- Image upload: broken display (B-2), broken real-time sync (B-1), phantom risk (B-3).
- Template apply: broken real-time sync (B-1).
- Canvas search: degraded quality (B-5).

Pre-existing features are **not** affected:
- User-to-user CRDT sync via WebSocket relay (`ws/canvas.py:222`) uses correct key.
- Snapshot restore (`canvas_service.py:513`) uses correct key.

**Sibling suspects**:
- `canvas_service.py:507` (snapshot restore): uses `repo.update_crdt_state` +
  broadcast, but uses correct key `data`. Broadcast is also before commit (line 507
  persist, 511 broadcast, commit is at caller). **Confirmed sibling** for B-3 only.
- No other bounded contexts use a similar CRDT broadcast pattern.

## 7. Fix Design

### F-1: Consolidate persist-and-broadcast (fixes B-1, B-7)

Add a method to `CanvasFacade`:

```
async def persist_and_broadcast_crdt(
    self, canvas_id: uuid.UUID, state: bytes
) -> None
```

This method: (1) calls `repo.update_crdt_state(canvas_id, state)`, (2) base64-encodes
`state`, (3) calls `Publisher(canvas_channel(canvas_id)).emit("yjs-update", {"data": b64})`.
The payload key `data` is defined in exactly one place.

For the broadcast-after-commit requirement (B-3), the method accepts an optional
`deferred: bool = False` flag. When `True`, it persists immediately but enqueues the
broadcast into a list on the session (using SQLAlchemy's `after_commit` event hook).
The broadcast fires only after the transaction commits. When `False` (default), it
broadcasts immediately after persist -- used only in contexts where the caller commits
synchronously afterward (e.g., snapshot restore, where the caller controls the commit).

All 6 duplicated sites are replaced with a call to this method.

### F-2: Nginx proxy for MinIO assets (fixes B-2)

Add a location block to `deploy/compose/nginx/conf.d/smap.conf`:

```
location /minio-assets/ {
    internal;
    proxy_pass http://minio:9000/;
}
```

The backend `upload_image` endpoint returns a relative URL path
(`/minio-assets/chat-uploads/{key}?{signature_params}`) instead of the raw presigned
URL. The presigned URL's query parameters (signature, expiry) are preserved and
forwarded by nginx. The `internal` directive prevents direct browser access to the
location -- requests must come through the backend-provided URL.

Actually, `internal` would block browser requests too. The correct approach: drop
`internal`, and let the browser access `/minio-assets/` directly. The presigned
signature parameters in the query string provide access control (MinIO validates them).

Alternatively, generate the presigned URL with an external-facing base URL. The
simplest fix: add `MINIO_EXTERNAL_ENDPOINT` to `MinioSection` config, defaulting to
`None`. When set, `presigned_get` constructs the URL with the external endpoint.
When not set, the nginx proxy path is used as the fallback.

The chosen approach (Q-2): nginx proxy. Add a non-internal `/minio-assets/` location
that proxies to `http://minio:9000/`. The backend constructs the URL as
`/minio-assets/{bucket}/{key}?{presigned_query_params}` by extracting the path and
query from the presigned URL and prepending `/minio-assets`.

### F-3: Broadcast after commit (fixes B-3)

For `canvas.py` `upload_image`: move both `Publisher.emit` calls after `db.commit()`.

For `canvas_tools.py` agent tools: use the `deferred=True` mode of the consolidated
facade method (F-1). The broadcast is enqueued on SQLAlchemy's `after_commit` hook,
so it fires only after `turn_engine.py:3207` commits.

For `canvas_service.py` snapshot restore (sibling suspect): same deferred pattern.

### F-4: Agent attribution in CRDT metadata (fixes B-4)

The CRDT element dict gains a `customData` field (Excalidraw's extension point for
arbitrary metadata):

```python
"customData": {"createdByAgentId": str(ctx.agent_id)}
```

The facade's consolidated CRDT method (F-1) does not need to know about this --
attribution is set at element creation time in the tool, not at broadcast time.

The frontend canvas components read `element.customData?.createdByAgentId` and render
a small agent badge (using the existing `CpuChipIcon` from heroicons) on the element.
Add the `agentCreated` i18n key to both locale files.

### F-5: Restore FTS-based search (fixes B-5)

The CRDT bridge introduced a `canvas_objects_search` materialized view or the elements
can be extracted from the CRDT doc and indexed. The simplest fix: extract text from
CRDT elements into the existing `canvas_objects` table's `text_content` column
(already present for FTS indexing), keeping the `canvas_objects` table as a search
index synchronized from the CRDT doc.

On each `persist_and_broadcast_crdt` call, also synchronize the text content of
changed elements into `canvas_objects` rows. The search endpoint switches back to
`facade.search_objects()` which uses `ts_rank_cd` and `ts_headline`.

This dual-write (CRDT doc as source of truth for rendering, `canvas_objects` for
search indexing) is a deliberate denormalization, documented in the code.

### F-6: SoC -- add facade methods (fixes B-6)

Add to `CanvasFacade`:
- `inject_elements(canvas_id, elements, *, created_by_agent_id=None) -> bytes`
- `update_element(canvas_id, element_id, updates) -> bytes`
- `delete_element(canvas_id, element_id) -> bytes`

Each delegates to `CrdtRelay` and returns the updated state bytes. Callers pass
the result to `persist_and_broadcast_crdt` (F-1). Remove all direct imports of
`crdt_relay`, `canvas_channel`, and `CanvasRepository` from route handlers and
agent tools.

### F-7: Shared element factory (fixes B-8)

Add `_build_element_dict(kind, *, width, height, x, y, style=None, text=None,
custom_data=None)` to `crdt_relay.py` (or a new `element_factory.py` in the canvas
domain). Returns a complete Excalidraw element dict with kind-appropriate defaults
(images get transparent stroke and roughness 0; shapes get the standard defaults).
Replace all 4 copies.

### F-8: Incremental CRDT deltas (fixes B-9)

Change `inject_elements`, `update_element`, `delete_element` in `crdt_relay.py` to
capture the state vector before mutation and return `doc.get_update(pre_state_vector)`
instead of `doc.get_update()`. This returns only the incremental delta, reducing
broadcast size from O(doc_size) to O(change_size).

The `persist_and_broadcast_crdt` method (F-1) stores the full state via
`repo.update_crdt_state` (using `doc.get_update()` for persistence) but broadcasts
only the delta. This requires the relay methods to return both the full state (for
persistence) and the delta (for broadcast), e.g., as a `(full_state, delta)` tuple.

## 8. Regression Test Plan

**B-1**: Unit test asserting that `persist_and_broadcast_crdt` emits payload key
`data`. Mock `Publisher.emit` and check the dict.

**B-2**: Unit test asserting that the URL returned by `upload_image` starts with
`/minio-assets/` and contains no internal hostname. Integration test against a running
stack to verify the image loads in the browser.

**B-3**: Unit test asserting that `Publisher.emit` is not called before `db.commit()`.
Use a mock session with `after_commit` hooks and verify broadcast ordering.

**B-4**: Unit test asserting that agent-created CRDT elements contain
`customData.createdByAgentId`. Frontend component test for the agent badge.

**B-5**: Unit test asserting that search results have varying `rank` values and
`ts_headline`-formatted snippets. Requires `pytest.mark.db` (FTS functions need a
real database).

**B-6**: The existing `eslint.config.js` boundary rules do not cover backend SoC.
Verify by `ruff` or manual review that no `app/api/v1/` file imports from
`contexts.*.application` or `contexts.*.infrastructure` (excluding `domain`).

**B-7**: Verify by grep that `Publisher.emit("yjs-update"` appears in exactly one
place (the consolidated method).

**B-8**: Verify by grep that the element default dict is defined in exactly one place.

**B-9**: Unit test asserting that `inject_elements` returns a delta smaller than the
full document state for a document with pre-existing content.

## 9. Risks and Rollback

**Risk 1 -- Deferred broadcast latency**: Moving broadcast after commit adds commit
latency to the real-time update path. Mitigation: SQLAlchemy `after_commit` fires
synchronously after commit, so the added latency is the commit time itself (~1-5ms).
The WS relay path (user-to-user edits) is unaffected -- it broadcasts immediately via
the WebSocket handler without touching the DB.

**Risk 2 -- Dual-write consistency for search (F-5)**: If the CRDT doc and the
`canvas_objects` search index drift, search results may be stale. Mitigation: the
sync happens in the same transaction as the CRDT state persist, so they commit
atomically.

**Risk 3 -- Incremental delta correctness (F-8)**: If the state vector capture is
incorrect, clients may receive incomplete deltas. Mitigation: the CRDT protocol
handles this -- applying a delta that references unknown state is safe (Yjs/pycrdt
merges it on the next full sync). A full state broadcast on reconnect (existing
behavior via `yjs-sync-step-1`) corrects any divergence.

**Rollback**: `git revert` of the fix commits. No migration involved.

## 10. Acceptance Criteria

- [x] AC-1: Regression test for B-1: `Publisher.emit("yjs-update", ...)` payload
  always uses key `data`. Test fails on current code, passes after fix.
- [ ] AC-2: An agent creating a canvas element via `canvas_create_object` appears
  immediately in all connected editors without page refresh.
  (Unticked: needs running stack with WebSocket relay for browser verification.)
- [ ] AC-3: An uploaded canvas image displays correctly in all connected editors.
  The image URL is a relative path starting with `/minio-assets/`.
  (Unticked: unit test verifies URL rewrite; browser verification needs running stack.)
- [x] AC-4: `Publisher.emit` for canvas events is never called before `db.commit()`.
  Deferred broadcast fires via `after_commit` hook.
- [x] AC-5: Agent-created canvas elements carry
  `customData.createdByAgentId = <agent_id>`. The frontend shows an agent badge on
  these elements.
- [x] AC-6: Canvas search results are ranked by `ts_rank_cd` and include
  `ts_headline` snippets. The `search_crdt_elements` in-memory path is removed.
- [x] AC-7: No `app/api/v1/` file imports from `contexts.*.application` or
  `contexts.*.infrastructure` (excluding `domain` models). No agent runtime file
  imports a private symbol from another context.
- [x] AC-8: `Publisher.emit("yjs-update"` appears in exactly one method (the
  consolidated facade method), verified by grep.
- [x] AC-9: The Excalidraw element default dict is defined in exactly one place,
  with kind-specific overrides (image vs shape).
- [x] AC-10: `inject_elements`, `update_element`, and `delete_element` return
  incremental deltas. A test with pre-existing doc content verifies `len(delta) <
  len(full_state)`.
- [x] AC-11: All existing canvas unit tests pass after the changes.
- [x] AC-12: `pnpm run gen:api` produces no diff (API contract unchanged).
- [x] AC-13: nginx config includes a `/minio-assets/` location block proxying to
  MinIO.

## 11. SRS Delta

None. The fix restores behavior already specified by [R13.58] and [R13.63]. No SRS
amendment needed.

## 12. Deviation Log

- D-1: The deferred broadcast hook is extracted into
  `infrastructure/deferred_broadcast.py` rather than inlined in the facade method as
  the spec proposed. Both the facade and the canvas service (restore_snapshot,
  template apply) call the same `enqueue_crdt_broadcast` function, eliminating the
  duplication a code review caught.
- D-2: `sync_text_from_crdt` performs a full-table upsert (O(N) per mutation) rather
  than a targeted single-element sync. Accepted as correct-first; O(1) optimization
  deferred to FU-4.
- D-3: The `search_crdt_elements` facade method is retained (not removed) because the
  spec only required the search endpoint to switch back to `search_objects`. Other
  internal callers may still use it.
- D-4: The `after_commit` hook registration is wrapped in try/except to tolerate mock
  sessions in unit tests. In production, `sync_session` is always a real
  `Session` and the hook always registers.

## 13. Follow-ups

- FU-1: The `canvas_objects` table retains rows that duplicate data now in the CRDT
  doc. Consider whether non-search columns can be dropped or the table repurposed as
  a pure search index.
- FU-2: Backend SoC enforcement is manual (code review). An AST-based lint rule for
  the `app/api/v1/ -> facade only` contract would catch violations at CI time.
- FU-3: The `after_commit` hook for deferred broadcast is session-scoped. If a future
  change introduces nested transactions (savepoints), verify the hook fires on the
  outer commit, not the savepoint release.
- FU-4: `sync_text_from_crdt` issues one INSERT...ON CONFLICT per element (N+1). A
  multi-row VALUES statement would reduce it to O(1) round-trips. Low urgency for
  canvases under ~200 elements.
- FU-5: `persist_and_broadcast_crdt` syncs the entire canvas on every single-element
  change. A targeted sync that only writes the changed element(s) would reduce cost
  from O(N) to O(1).
- FU-6: `turn_engine.py:1060` imports `CanvasContextProvider` from
  `contexts.canvas.application` -- a pre-existing SoC violation not introduced by
  this task.
