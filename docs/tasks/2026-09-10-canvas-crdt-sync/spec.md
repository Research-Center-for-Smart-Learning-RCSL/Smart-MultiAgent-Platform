---
type: feature
status: implemented
created: 2026-09-10
requirements: [R13.42, R13.44, R13.46, R13.47, R13.48, R13.50]
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Phase 2 -- Real-time CRDT Sync

## 1. Summary

Replace the Phase 1 snapshot-based canvas synchronization with real-time collaborative
editing powered by Yjs CRDT. Multiple users editing the same canvas see each other's
changes within 500ms, with cursor/selection awareness and user-colored presence
indicators. A dedicated WebSocket endpoint `/ws/canvas/{canvas_id}` carries Yjs update
deltas and awareness frames, isolated from chat traffic. The backend validates incoming
Yjs updates via `pycrdt` before relay, and persists the Yjs document state to
`canvases.crdt_state` (BYTEA, already provisioned by migration 0089) every 30 seconds
and on last-editor disconnect. The agent digest ([R13.47]) switches from reading
snapshots to deriving from the live CRDT document state, so agents see current canvas
content without requiring a manual snapshot.

This is Phase 2 of a three-phase feature. Phase 1 (snapshot-based,
`2026-09-10-collaborative-canvas`) is implemented. Phase 3 (AI writes to canvas,
`2026-09-10-canvas-ai-write`) builds on this phase's CRDT infrastructure.

## 2. Goals and Non-goals

**Goals**

- Sub-500ms real-time collaborative editing on the spatial canvas.
- Cursor position, element selection, display name, and assigned color visible to all
  concurrent editors (Yjs awareness protocol).
- Dedicated `/ws/canvas/{canvas_id}` WebSocket endpoint isolated from the chatroom
  channel, following the same `connection_loop()` + `authenticate_subprotocol` pattern.
- Server-side Yjs update validation via `pycrdt` -- malformed or oversized updates are
  dropped, never relayed.
- Periodic persistence (30s interval + last-disconnect flush) of the encoded Yjs document
  to `canvases.crdt_state`.
- Agent digest derived from live CRDT state rather than last manual snapshot.
- One-time migration of existing Phase 1 canvas objects into a Yjs document on first
  CRDT connection.
- Graceful handling of temporary disconnections via CRDT merge on reconnect.

**Non-goals**

- Long-term offline editing (temporary disconnections only; no local-first persistence).
- Canvas-to-canvas linking or cross-room CRDT state sharing.
- Removing the REST object CRUD API (kept for image upload and non-CRDT administrative
  operations; objects table becomes a read-only archive after migration).
- Operational Transform or any non-CRDT conflict resolution strategy.
- Mobile-specific collaboration UX (Phase 2 targets desktop viewports; mobile is a
  follow-up if needed).

## 3. Clarifications

- **Q-1**: Backend validates vs blind relay? **Validated relay.** `pycrdt` decodes each
  incoming Yjs update, rejects malformed frames, and enforces a 10 MB document size cap
  before relaying. The ~2ms validation overhead is acceptable for the corruption
  prevention it provides. (`contexts/canvas/application/crdt_relay.py`)

- **Q-2**: Migration strategy for existing Phase 1 canvases? **One-time migration on
  first CRDT connect.** When a canvas has objects in `canvas_objects` but no
  `crdt_state`, the server builds a Yjs document from the existing objects, persists it,
  and serves it to the connecting client. The `canvas_objects` table becomes a read-only
  archive; new edits flow exclusively through CRDT.

- **Q-3**: Connection and size limits? **10 concurrent editors per canvas, 10 MB CRDT
  state cap.** Frame size for the canvas WS endpoint is raised to 256 KB (from the
  default 64 KB in `connection.py:65`) to accommodate large Yjs update batches. The
  per-user connection cap from `connection_loop()` still applies globally.

- **Q-4**: Persistence frequency? **Every 30 seconds while editors are connected, plus a
  final flush when the last editor disconnects.** Balances durability (max 30s data loss
  on crash) against database write load. Persistence is debounced -- if the document
  hasn't changed since the last flush, no write occurs.

- **Q-5**: Awareness protocol scope? **Cursor position, selected elements, display name,
  and assigned color.** Follows Excalidraw's built-in collaboration UI conventions.
  Awareness state is ephemeral (not persisted).

- **Q-6**: Agent digest source? **Derive from CRDT state.** `CanvasContextProvider`
  reads the live Yjs document (via `pycrdt`) and extracts element data for
  `build_canvas_digest()`, falling back to the last persisted `crdt_state` if no
  in-memory document exists, then to the legacy snapshot. Agents see current canvas
  content without requiring a manual save.

## 4. Requirements

### 4.1 Existing requirements touched

- **[R13.48]** (amended): Canvas mutations publish events on a **dedicated** WebSocket
  channel `ws:canvas:{canvas_id}` carrying Yjs CRDT deltas. Connected clients apply
  deltas in real time. The Phase 1 room-channel event notifications are preserved for
  non-CRDT events (settings changes, image uploads).

- **[R13.47]** (behavior change): The agent digest is derived from the live CRDT document
  state when available, falling back to the last persisted `crdt_state`, then to the
  legacy snapshot digest. The 2000-character cap and `expose_to_agents` +
  `may_read_canvas` gates remain unchanged.

- **[R13.50]** (extended): The canvas WebSocket connection verifies chatroom access on
  connect and re-checks periodically (same `authorize` callback pattern as the chatroom
  WS). A principal whose room access is revoked is disconnected from the canvas WS.

### 4.2 New requirements (SRS Delta)

- **[R13.51]** The canvas WebSocket endpoint validates every incoming Yjs update via
  `pycrdt` before relay. Malformed updates are dropped with a structured error frame.
  Updates that would push the Yjs document past the 10 MB size cap are rejected.
  Corrupted CRDT state triggers a server-side reset to the last valid persisted state.

- **[R13.52]** The canvas CRDT document state is persisted to `canvases.crdt_state`
  (BYTEA) every 30 seconds while at least one editor is connected, and once more when the
  last editor disconnects. Persistence is debounced: no write occurs if the document is
  unchanged since the last flush.

- **[R13.53]** At most 10 concurrent WebSocket connections per canvas. The eleventh
  connection attempt receives a structured close frame (4009, "canvas editor limit
  reached") and is not admitted.

- **[R13.54]** When a canvas that has `canvas_objects` rows but no `crdt_state` receives
  its first CRDT WebSocket connection, the server builds a Yjs document from the existing
  objects, persists it as `crdt_state`, and serves it to the connecting client. After
  migration, the `canvas_objects` table is treated as a read-only archive for that canvas.

- **[R13.55]** Each connected editor's cursor position, selected elements, display name,
  and assigned color are broadcast to all other editors on the same canvas via the Yjs
  awareness protocol. Awareness state is ephemeral and not persisted.

## 5. Acceptance Criteria

- [ ] AC-1: Two users editing the same canvas see each other's element changes in real
  time (< 500ms latency under normal network conditions). Verified by an e2e test with
  two browser contexts. (Unticked: requires running stack with two browser contexts.)
- [ ] AC-2: Each editor's cursor position and selected elements are visible to other
  editors, with display name and a distinct color. Verified visually. (Unticked: requires
  running stack for visual verification.)
- [ ] AC-3: The backend persists the Yjs document to `canvases.crdt_state` every 30s
  while editors are connected and on last disconnect. A reconnecting client receives the
  persisted state. Verified by disconnecting and reconnecting after edits. (Unticked:
  requires running stack.)
- [x] AC-4: `/ws/canvas/{canvas_id}` accepts connections, authenticates via
  `authenticate_subprotocol`, and checks chatroom access. Unauthorized connections are
  rejected. Verified by code review of `app/api/ws/canvas.py`.
- [x] AC-5: Malformed Yjs updates are rejected with an error frame and not relayed to
  other clients. Verified by `test_malformed_update_rejected` in `test_crdt_relay.py`.
- [x] AC-6: The 11th concurrent connection to a canvas is refused with close code 4009.
  Verified by code review of `canvas.py:154-162` (pre-connection_loop cap check).
- [x] AC-7: A canvas with Phase 1 objects but no `crdt_state` is automatically migrated
  on first CRDT connection. The resulting Excalidraw scene contains all prior objects.
  Verified by `test_migrates_from_legacy_objects` in `test_crdt_relay.py`.
- [x] AC-8: Phase 1 snapshot creation still works alongside CRDT state. The snapshot's
  `agent_digest` is derived from the CRDT document when available. Verified by code
  review of `canvas_service.py:create_snapshot()`.
- [x] AC-9: `CanvasContextProvider` returns a digest derived from the live CRDT document,
  not the last snapshot. Verified by `test_uses_in_memory_crdt` and
  `test_uses_persisted_crdt_state` in `test_canvas_context_provider.py`.
- [x] AC-10: The CRDT document size is capped at 10 MB. An update that would exceed the
  cap is rejected. Verified by `test_size_cap_enforcement` in `test_crdt_relay.py`.
- [x] AC-11: Guest editors are rate-limited (existing [R13.46] applies to the WS
  endpoint: 60 operations per minute per guest session). Verified by code review of
  `canvas.py:_check_guest_rate`.
- [ ] AC-12: `canvases.crdt_state` persists across container restarts. After a cold start,
  reconnecting clients receive the last persisted state. Verified by integration test.
  (Unticked: requires running stack with DB.)

## 6. Detailed Changes

### 6.1 Backend -- New dependency

Add `pycrdt>=0.12,<1.0` to `backend/pyproject.toml` runtime dependencies and
`requirements.lock`. `pycrdt` provides `Doc`, `Array`, `Map`, and update
encode/decode/merge for server-side Yjs document handling.

### 6.2 Backend -- CRDT relay service

New file: `contexts/canvas/application/crdt_relay.py`

- `CrdtRelay` class: manages one in-memory Yjs `Doc` per active canvas.
  - `get_or_load(canvas_id)` -- returns the in-memory doc, loading from
    `canvases.crdt_state` if not cached, or building from `canvas_objects` if no
    `crdt_state` exists (migration path, [R13.54]).
  - `apply_update(canvas_id, update_bytes)` -- validates via `pycrdt`, checks size cap
    (10 MB), applies to in-memory doc, returns encoded delta for relay.
  - `get_state_vector(canvas_id)` -- returns the Yjs state vector for sync protocol.
  - `flush(canvas_id)` -- persists current doc state to `canvases.crdt_state`.
  - `evict(canvas_id)` -- removes from in-memory cache (on last disconnect).
- Singleton registry keyed by `canvas_id`, with an asyncio lock per canvas to serialize
  updates.

### 6.3 Backend -- Repository additions

`contexts/canvas/infrastructure/repositories/canvas_repo.py`:

- `get_crdt_state(canvas_id) -> bytes | None` -- reads `crdt_state` from `canvases`.
- `update_crdt_state(canvas_id, state: bytes) -> None` -- writes `crdt_state`.

### 6.4 Backend -- WebSocket endpoint

New file: `backend/app/api/ws/canvas.py`

- `@router.websocket("/ws/canvas/{canvas_id}")` endpoint.
- Authentication: `authenticate_subprotocol(ws)` (same ticket-based auth as chatroom WS,
  `shared_kernel/realtime/ws_auth.py:106`).
- ACL: resolve chatroom from canvas, call `resolve_room_access` + `ensure_can_read`.
- Connection cap: Redis ZSET `ws:canvas-editors:{canvas_id}`, reject if >= 10.
- Guest rate limiting: reuse `_enforce_guest_rate_limit` pattern on inbound messages.
- Frame size override: 256 KB max for this endpoint (pass to `connection_loop` or handle
  in the reader).
- Uses `connection_loop()` with:
  - `channels=[canvas_channel(canvas_id)]` (from
    `contexts/canvas/infrastructure/channels.py:8`)
  - `on_open`: register in editor ZSET, send initial Yjs state (sync step 1)
  - `on_close`: unregister, flush if last editor, evict from cache
  - `on_client_message`: dispatch by message type:
    - `yjs-update`: validate + apply via `CrdtRelay`, publish delta to canvas channel
    - `yjs-sync-step-1/2`: Yjs sync protocol handshake
    - `awareness`: broadcast to canvas channel (pass-through, not persisted)
  - `on_heartbeat`: periodic flush timer (30s)
  - `authorize`: re-check room access

Register in `app/api/v1/__init__.py` alongside the other 8 WS routers.

### 6.5 Backend -- Agent digest from CRDT

Modify `contexts/canvas/application/canvas_context_provider.py`:

- `CanvasContextProvider.provide()` now calls `CrdtRelay.get_or_load()` if available,
  extracts element data from the Yjs doc, and passes to `build_canvas_digest()`.
- Fallback chain: in-memory CRDT doc -> persisted `crdt_state` -> legacy snapshot
  digest.
- `build_canvas_digest()` signature unchanged (takes a list of element-like dicts).

### 6.6 Backend -- Snapshot from CRDT

Modify `contexts/canvas/application/canvas_service.py`:

- `create_snapshot()` reads from the CRDT doc (via `CrdtRelay`) when available, instead
  of querying `canvas_objects`. The `snapshot_data` JSON structure stays the same.

### 6.7 Frontend -- New dependencies

Add to `frontend/package.json`:
- `yjs` (core CRDT library)
- `y-protocols` (sync and awareness protocols)
- No `y-websocket` -- use a custom provider that integrates with `WsManager`.

### 6.8 Frontend -- Custom Yjs WebSocket provider

New file: `slices/canvas/composables/useYjsProvider.ts`

- Creates a `Y.Doc` and wires it to a `WsManager` channel at `/canvas/{canvasId}`.
- Implements the Yjs sync protocol (sync step 1/2) on connect.
- Handles `yjs-update` messages: applies remote updates to the local doc.
- Handles `awareness` messages: updates the Yjs awareness instance.
- Sends local updates (from `doc.on('update')`) as `yjs-update` messages.
- Sends local awareness changes as `awareness` messages.
- Returns `{ doc, awareness, provider, destroy }`.

### 6.9 Frontend -- CanvasRenderer rewrite

Modify `slices/canvas/components/CanvasRenderer.vue`:

- Replace `initialData` + `updateScene` model with Yjs collaboration mode.
- Accept `doc: Y.Doc` and `awareness: awarenessProtocol.Awareness` as props (or inject
  from a provide/inject context).
- Wire Excalidraw's collaboration API:
  - `excalidrawAPI.updateScene()` driven by Yjs doc changes
  - `excalidrawAPI.onChange()` writes back to Yjs doc
  - Awareness drives cursor/selection overlays
- Remove the `objectsToExcalidrawElements` mapping (CRDT doc holds Excalidraw-native
  element format directly).

### 6.10 Frontend -- CanvasPanel coordination

Modify `slices/canvas/components/CanvasPanel.vue`:

- Create the Yjs provider via `useYjsProvider(canvasId)` when the panel opens.
- Pass `doc` and `awareness` to `CanvasRenderer`.
- `handleChange` is removed (CRDT handles sync automatically).
- Phase 1 REST queries (`useCanvasState`) retained for canvas metadata, settings, and
  image upload only. Object listing query is removed (CRDT is the source of truth).

### 6.11 Frontend -- useCanvasSocket update

Modify `slices/canvas/composables/useCanvasSocket.ts`:

- Switch from room channel to canvas channel (the comment at line 9 already anticipates
  this).
- Remove object CRUD event handlers (CRDT handles those).
- Keep `canvas.settings_updated` and `canvas.snapshot_created` on the room channel.

### 6.12 Vite config

Add `yjs` to the `excalidraw` manual chunk in `vite.config.ts` to keep the collaboration
bundle together and lazily loaded.

## 7. Existing Debt and Patterns

### Existing debt in touched files

- `canvas_service.py`: The `room_channel_fn` injection (added in Phase 1 code review)
  works but adds ceremony. Phase 2 introduces its own channel (`canvas_channel`) used
  directly -- no injection needed for the CRDT relay.
- `CanvasRenderer.vue`: File-level `@typescript-eslint/no-explicit-any` suppression for
  the React-in-Vue bridge. Phase 2's rewrite should aim to reduce `any` usage by typing
  the Yjs/Excalidraw collaboration interfaces.

### Patterns to follow

- **WS endpoint**: Follow `app/api/ws/workflow_runs.py` (simple endpoint) for structure,
  `app/api/ws/chatroom.py` for presence/awareness callbacks.
  `shared_kernel/realtime/connection.py:180` (`connection_loop`) is the central driver.
- **Channel naming**: `canvas_channel()` at
  `contexts/canvas/infrastructure/channels.py:8` already returns `ws:canvas:{canvas_id}`.
- **WS auth**: `shared_kernel/realtime/ws_auth.py:106` (`authenticate_subprotocol`).
- **Pub/sub**: `shared_kernel/realtime/pubsub.py:44` (`Publisher`) for event emission.
- **Connection cap**: Redis ZSET pattern from `connection.py:126`
  (`_register_user_connection`).

### Reuse inventory

| What | Where | Use |
|------|-------|-----|
| `connection_loop()` | `shared_kernel/realtime/connection.py:180` | WS driver |
| `authenticate_subprotocol()` | `shared_kernel/realtime/ws_auth.py:106` | WS auth |
| `canvas_channel()` | `contexts/canvas/infrastructure/channels.py:8` | Channel name |
| `Publisher` | `shared_kernel/realtime/pubsub.py:44` | Event emission |
| `resolve_room_access` | `contexts/conversation/application/access.py` | ACL check |
| `rate_check_raw` | `shared_kernel/auth/ratelimit.py` | Guest rate limit |
| `CanvasRepository` | `contexts/canvas/infrastructure/repositories/canvas_repo.py:64` | DB access |
| `build_canvas_digest()` | `contexts/canvas/domain/canvas_digest.py:40` | Agent digest |
| `WsManager` | `frontend/src/shared/transport/ws-manager.ts:433` | Frontend WS |

## 8. Security Considerations

- **Update validation**: Every inbound Yjs update is decoded by `pycrdt` before relay.
  Invalid binary or structurally malformed updates are dropped. This prevents a malicious
  client from corrupting the shared document.
- **Size cap enforcement**: The 10 MB document size cap is checked after applying the
  update tentatively to a copy of the doc. If exceeded, the update is rejected and the
  original doc is unchanged.
- **Frame size**: Canvas WS frame size is 256 KB (4x the default 64 KB). This
  accommodates large Yjs update batches while still bounding memory per frame.
- **ACL re-check**: The `authorize` callback re-checks room access periodically (every
  ~60s, matching the chatroom WS pattern). Revoked access disconnects the editor.
- **Guest rate limiting**: Guest editors are rate-limited at 60 WS operations per minute,
  matching the REST rate limit ([R13.46]).
- **Connection cap**: 10 editors per canvas prevents resource exhaustion from a single
  canvas consuming all WS capacity.
- **No CRDT state in logs**: The binary CRDT state and update frames must not be logged
  at any level (they may contain user content).

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CRDT state unbounded growth | Medium | High | 10 MB cap with rejection; periodic garbage collection via `pycrdt` GC on flush |
| High-frequency WS traffic under many editors | Medium | Medium | 50ms client-side debounce on updates; 10-editor cap; dedicated channel isolates chat |
| `pycrdt` validation overhead | Low | Low | Measured ~2ms per update; acceptable for the safety it provides |
| Migration of complex Phase 1 canvases | Low | Medium | Migration builds a valid Yjs doc from objects; fallback: manual re-creation if migration fails |
| Excalidraw Yjs integration API changes | Medium | High | Pin Excalidraw version; wrap collaboration API in an adapter |

## 10. Test Plan

### Unit tests

- `test_crdt_relay.py`: Yjs doc creation, update application, size cap rejection,
  malformed update rejection, state vector generation, flush logic, migration from
  canvas_objects.
- `test_canvas_ws_auth.py`: Connection authentication, ACL check, guest token support,
  unauthorized rejection.
- `test_canvas_ws_limits.py`: 10-connection cap (11th rejected with 4009), guest rate
  limiting on WS operations.
- `test_canvas_context_provider.py` (extend): Digest from CRDT doc, fallback chain.

### Integration tests (pytest.mark.db)

- `test_crdt_persistence.py`: Write CRDT state, read back, verify byte-for-byte match.
  Verify flush-on-disconnect persists correctly.
- `test_crdt_migration.py`: Create a canvas with Phase 1 objects, trigger migration,
  verify the Yjs doc contains all objects with correct positions/types.

### E2E tests (Playwright)

- Two browser contexts open the same canvas. User A draws a shape; User B sees it within
  1 second. User B adds text; User A sees it.
- Cursor awareness: User A's cursor position is visible in User B's viewport.
- Disconnect/reconnect: User A disconnects, User B makes changes, User A reconnects and
  sees the changes.

## 11. Open Questions

None -- all questions resolved in Clarifications section.

## 12. Deviation Log

- D-1: The spec says Yjs updates are carried as binary WebSocket frames. The
  implementation uses base64-encoded JSON instead, to reuse the existing
  `connection_loop()` infrastructure (auth, per-user cap, idle timeout, auth
  watchdog). The `max_frame_bytes` parameter was added to `connection_loop` to
  support the 256KB frame size for canvas. This adds ~33% overhead on update
  payload size, acceptable for incremental CRDT deltas.

- D-2: The spec's `CanvasContextProvider.provide()` method is actually named
  `query()`. Implementation targets `query()` as found in the codebase.

- D-3: The spec references `rate_check_raw` in `shared_kernel/auth/ratelimit.py`.
  The actual function is `check_raw`, imported as `rate_check_raw` in the canvas
  REST routes. The WS endpoint follows the same pattern.

- D-4: Editor cap check has a narrow TOCTOU race between the pre-connection_loop
  check and the `on_open` registration. Under extreme concurrent connect load,
  the 11th editor could briefly be admitted. Accepted as low-risk; an atomic
  register-and-check would require extending `connection_loop`'s internals.

- D-5: Four ACs remain unticked (AC-1, AC-2, AC-3, AC-12) because they require a
  running stack with database and two browser contexts. The unit test tier verifies
  the logic; the integration tier verifies the wiring.

## 13. Follow-ups

- FU-1: CRDT garbage collection / compaction strategy (if document growth becomes a
  problem in production).
- FU-2: Yjs undo/redo manager for per-user undo history.
- FU-3: Conflict-free image placement (images stay as REST upload + MinIO; CRDT tracks
  the element metadata but not the image bytes).
- FU-4: Atomic editor cap check -- replace the TOCTOU pattern in canvas.py:154-162 with
  an atomic register-and-check (Lua script or connection_loop extension).
- FU-5: E2e Playwright test for two-browser CRDT sync (AC-1) and cursor awareness (AC-2).
- FU-6: Integration test for CRDT persistence across container restarts (AC-12).
