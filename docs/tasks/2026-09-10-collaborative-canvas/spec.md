---
type: feature
status: draft
created: 2026-09-10
requirements: [R13.01, R13.04, R13.06, R13.19, R13.20]
depends_on: []
---

# Collaborative Canvas

## 1. Summary

Add a real-time collaborative spatial canvas to each chatroom, allowing multiple users
(and guests) to place sticky notes, text blocks, images, freeform drawings, shapes and
connectors on a shared board. AI agents with the appropriate grant can read a
natural-language digest of the canvas content as part of their turn context, and in a
later phase write to it. The canvas appears as a resizable split-pane beside the chat
feed, preserving conversational context while providing spatial collaboration space.

The feature is named **Canvas** (not "Workspace") to avoid collision with the existing
`Workspace` entity (`Project -> Workspace -> Chatroom`).

## 2. Goals and Non-goals

**Goals**

- Multi-user real-time collaboration on a spatial canvas within a chatroom.
- AI agents can optionally "see" canvas content through a dedicated context provider.
- Split-pane UI: chat on the left, canvas on the right, with drag-to-resize and
  fullscreen toggle.
- Guests with chatroom access can read and write to the canvas.
- Canvas lifecycle follows the chatroom (cascade create/delete).
- Phased delivery: Phase 1 (snapshot-based), Phase 2 (real-time CRDT), Phase 3 (AI
  writes to canvas).

**Non-goals**

- Canvas is not a general-purpose drawing tool; it is a collaboration surface tied to a
  chatroom, not a standalone product.
- No canvas templates or pre-built layouts in Phase 1.
- No version history / undo across sessions in Phase 1 (in-session undo is provided by
  the canvas library).
- No canvas-to-canvas linking or cross-room canvases.
- No offline editing or conflict resolution for disconnected clients (CRDT handles
  temporary disconnections in Phase 2, but long-term offline is out of scope).
- No canvas export to PDF/PNG in Phase 1.
- AI agents cannot write to the canvas until Phase 3.
- No canvas search (full-text search over canvas object text is a follow-up).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | The existing `Workspace` entity creates a naming collision. What should the new feature be called? | **Canvas** | Clear spatial semantics, no collision with existing entities. The user confirmed this choice. |
| Q-2 | Can guests use the canvas? | **Read-write** | Guests who can send messages in the chatroom can also edit the canvas. This maximizes collaboration value for invite-link scenarios. Rate limiting and audit logging mitigate abuse. The user confirmed this choice. |
| Q-3 | What is the canvas lifecycle? | **Follows Chatroom** | 1:0..1 relationship. Deleting a chatroom cascade-deletes its canvas and all objects. The user confirmed this choice. |
| Q-4 | Should canvas events share the existing room WebSocket channel or use a dedicated one? | **Dedicated channel** in Phase 2 (`/ws/canvas/{canvasId}`); **shared room channel** in Phase 1 for lightweight snapshot events. | CRDT delta traffic is high-frequency and would pollute the chat event stream. Phase 1 only emits low-frequency snapshot events, so sharing the room channel is adequate. |
| Q-5 | Which canvas rendering library? | **Excalidraw** (MIT license, built-in Yjs binding, active community). | tldraw 2.x changed to a restrictive license (AGPL-like); fabric.js is too low-level. Excalidraw provides sticky notes, shapes, text, freeform drawing, connectors, and image embedding out of the box. |
| Q-6 | Where does canvas state persist? | **PostgreSQL** for the CRDT document (BYTEA column storing the Yjs encoded state); **MinIO** for uploaded images under a dedicated bucket prefix. | PostgreSQL for transactional consistency with chatroom lifecycle; MinIO for large binary objects, consistent with existing attachment storage. |
| Q-7 | Overlap with `2026-07-19-large-artifacts-silently-dropped` (in-progress, touches `turn_engine.py`)? | **Not a dependency.** | That dossier edits `turn_engine.py:1133-1136` (artifact upload path). This feature adds a new `_SystemBlock` entry around lines 915-938 and a new context provider call around line 2678. Disjoint regions; whoever builds second rebases. |
| Q-8 | Overlap with `2026-07-07-graphrag-two-axis-redesign` (approved, touches knowledge context in `turn_engine.py`)? | **Not a dependency.** | That blueprint targets knowledge retrieval (lines 2783-2784). This feature adds a system block in the block list (lines 915-938). Disjoint regions. |

## 4. Current State

### 4.1 Chatroom entity

The `Chatroom` dataclass (`contexts/conversation/domain/models.py:73`) owns messages,
agent bindings, guest sessions and activity sessions. It has no canvas concept today. The
`chatrooms` table (`contexts/conversation/infrastructure/tables.py:31`) has no
canvas-related column.

### 4.2 Agent context injection

The turn engine assembles system-prompt blocks via `_SystemBlocks.build()`
(`contexts/agents/application/runtime/turn_engine.py:904-938`). The block list currently
includes: `base_system`, `observer_note`, `memory`, `summaries`, `knowledge`, `skills`,
`activity`, `staged`, `notify`, `participant_note`. Each block is a `_SystemBlock` with a
`_BlockRole` (MEASURED_AND_RENDERED, MEASURED_ONLY, RENDERED_ONLY).

The `ActivityContextProvider` (`contexts/activities/application/activity_context_provider.py:106`)
is the reference implementation: it takes `db`, exposes `async def query(*, chatroom_id,
limit, resolve_labels) -> str | None`, is best-effort (never raises into the turn), and
checks a platform policy gate.

Agent grants on `ChatroomAgent` (`models.py:97`): `may_control_activities`,
`may_read_drafts`. Each follows a pattern: bool column on `chatroom_agents` table, domain
grant object, resolver in the runtime, gated tool or context injection.

### 4.3 Real-time infrastructure

WebSocket connections use `connection_loop()` (`shared_kernel/realtime/connection.py:180`)
with Redis pub/sub (`shared_kernel/realtime/pubsub.py:31`). Channel naming:
`ws:room:{room_id}`, `ws:user:{user_id}`, etc. Each context defines its own
`channels.py` with builder functions. The `Publisher` class emits typed events; the
`Subscriber` class consumes them. Fire-and-forget semantics (R13.20).

Frontend: `WsManager` singleton (`shared/transport/ws-manager.ts:433`) manages a
`Map<string, Channel>` keyed by path. Composables like `useChatroomSocket`
(`slices/conversation/composables/useChatroomSocket.ts`) subscribe to events and maintain
Pinia state.

Presence: `PresenceTracker` (`contexts/conversation/infrastructure/presence.py:97`)
uses Redis SETs with Lua-atomic refcounting for per-connection tracking.

### 4.4 File storage

MinIO via `MinioClient` (`shared_kernel/storage/minio_client.py:41`). Chat uploads use
`chat_uploads_bucket` with key `{project_id}/{chatroom_id}/{attachment_id}/{filename}`.
Presigned GET for download. The canvas would use its own bucket or prefix.

### 4.5 Frontend ChatroomView layout

`ChatroomView.vue` uses CSS Grid: 4 columns (agents rail | feed | resize handle |
presence rail), 4 rows (header | feed | typing | composer). The feed occupies column 2.
The right rail holds tabbed panels (People / Observer / Activity).
`useTransientSurfaces` manages mutual exclusion of overlay panels.

## 5. Design

### Options considered

**Option A -- Canvas as a new bounded context (`contexts/canvas/`)**: Full DDD
separation with its own domain models, facade, repositories, tables, services, and
infrastructure. Clean boundaries, independent evolution, no coupling to conversation
internals.

**Option B -- Canvas as a sub-module within `contexts/conversation/`**: Fewer files,
shared transaction scope, but muddies the conversation context's responsibilities and
makes the already-large context harder to navigate.

### Decision

**Option A -- new bounded context.** The canvas has its own aggregate root (`Canvas`),
its own real-time channel, its own storage lifecycle, and its own context provider for the
turn engine. These are the hallmarks of a separate bounded context. The conversation
context exposes `chatroom_id` and room access resolution; the canvas context consumes
them as read-only dependencies through the shared kernel's auth helpers, never importing
from `contexts/conversation/` directly.

The turn engine (`contexts/agents/`) integrates via the same interface as activities: a
provider class instantiated in `TurnEngine.__init__`, called before
`_SystemBlocks.build()`, yielding a block string or `None`.

## 6. Detailed Changes

### 6.1 Backend -- new context `contexts/canvas/`

```
contexts/canvas/
  domain/
    models.py          Canvas, CanvasObject (polymorphic), CanvasSnapshot
    canvas_digest.py   build_canvas_digest() -- pure function, natural-language summary
  application/
    canvas_service.py  CRUD, snapshot management, object ops
    canvas_context_provider.py  CanvasContextProvider for agent turns
  infrastructure/
    tables.py          canvases, canvas_objects, canvas_snapshots
    repositories/
      canvas_repo.py
    channels.py        canvas_channel(canvas_id)
  interfaces/
    facade.py          CanvasFacade -- read surface for other contexts
```

**Domain models:**

- `Canvas`: `id`, `chatroom_id` (unique FK), `created_at`, `deleted_at`,
  `crdt_state` (bytes, Yjs encoded document -- Phase 2), `expose_to_agents: bool`
  (default True, toggled by room creator).
- `CanvasObject`: `id`, `canvas_id` (FK), `kind` (enum: `note`, `text`, `image`,
  `shape`, `drawing`, `connector`), `content` (text for notes/text, MinIO path for
  images), `position_x`, `position_y`, `width`, `height`, `z_index`, `style` (JSONB),
  `created_by_user_id` / `created_by_guest_id`, `created_at`, `updated_at`.
  Phase 1 only; Phase 2 replaces individual object rows with CRDT state.
- `CanvasSnapshot`: `id`, `canvas_id` (FK), `snapshot_data` (JSONB -- full object list),
  `agent_digest` (text -- natural-language summary), `created_by_user_id`,
  `created_at`. Persisted on manual "save" or periodic auto-save (Phase 1); on CRDT
  checkpoint (Phase 2).

**Tables (Alembic migration):**

- `canvases`: `id` PK, `chatroom_id` FK->chatrooms CASCADE (UNIQUE), `expose_to_agents`
  bool default true, `crdt_state` BYTEA nullable, `created_at`, `deleted_at`.
- `canvas_objects`: `id` PK, `canvas_id` FK->canvases CASCADE, `kind` PG ENUM
  `canvas_object_kind`, `content` TEXT nullable, `minio_path` TEXT nullable,
  `position_x` FLOAT, `position_y` FLOAT, `width` FLOAT, `height` FLOAT, `z_index` INT,
  `style` JSONB, `created_by_user_id` FK->users SET NULL nullable,
  `created_by_guest_id` FK->guest_sessions SET NULL nullable, `created_at`, `updated_at`.
- `canvas_snapshots`: `id` PK, `canvas_id` FK->canvases CASCADE, `snapshot_data` JSONB,
  `agent_digest` TEXT, `created_by_user_id` FK->users SET NULL nullable, `created_at`.

Migration required: **yes**. Reversible: `DROP TABLE canvas_snapshots, canvas_objects,
canvases; DROP TYPE canvas_object_kind`.

### 6.2 Backend -- CanvasContextProvider

Following the `ActivityContextProvider` pattern
(`activity_context_provider.py:106-190`):

```python
class CanvasContextProvider:
    def __init__(self, db: AsyncSession) -> None: ...
    async def query(
        self, *, chatroom_id: uuid.UUID,
        resolve_labels: LabelResolver | None = None,
    ) -> str | None:
        # 1. Look up canvas for chatroom; return None if no canvas or expose_to_agents=False
        # 2. Load latest snapshot's agent_digest or build one from current objects
        # 3. Return formatted "[Canvas content]" block, capped at 2000 chars
        # Best-effort: catch all exceptions, log, return None
```

**Digest generation** (`canvas_digest.py`): Converts canvas objects to natural-language
description. Truncated to `_MAX_DIGEST_CHARS = 2000`. Example output:

```
The canvas contains 5 sticky notes, 2 images, and 1 text block.
- Note by User A: "API design requirements -- must support pagination..."
- Note by User B: "Deadline: 2026-09-15"
- Image: architecture-diagram.png (uploaded by User A)
- Text block: "Conclusion: adopt Option B for the auth flow"
- Note by Guest (visitor-7f3a): "Question about rate limiting"
```

### 6.3 Backend -- agent grant

Add `may_read_canvas: bool` to `ChatroomAgent` (`models.py:97`). New column on
`chatroom_agents` table. New `CanvasReadGrant` domain object following the
`DraftReadGrant` pattern (`models.py:117`). New resolver
`resolve_canvas_access()` in a `canvas_tools.py` module.

The grant gates context injection only (Phase 1-2). Phase 3 adds a `write_to_canvas`
tool gated on a separate `may_write_canvas` grant.

### 6.4 Backend -- turn engine integration

In `TurnEngine.__init__` (around line 1056):
```python
self._canvas_provider = CanvasContextProvider(db)
```

New delegate method `_canvas_context(chatroom_id) -> str | None` following
`_activity_context` (line 4363).

Call in `_run_locked()` before `_SystemBlocks.build()` (around line 2678):
```python
canvas_block = await self._canvas_context(chatroom_id)
```

Add `canvas_block: str | None` parameter to `_SystemBlocks.build()`, and a new
`_SystemBlock("canvas", _BlockRole.MEASURED_AND_RENDERED, text=canvas_block)` entry
after the `activity` block in the block list (around line 932).

For the headless path (`run_input_turn`), pass `canvas_block=None`.

### 6.5 Backend -- API endpoints

New router registered in `app/api/v1/` as `canvas_router`, prefix
`/api/chatrooms/{chatroom_id}/canvas`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Get canvas for chatroom (create-on-first-access) |
| PATCH | `/` | Update canvas settings (expose_to_agents) |
| DELETE | `/` | Delete canvas and all objects |
| GET | `/objects` | List all canvas objects |
| POST | `/objects` | Create a canvas object |
| PATCH | `/objects/{object_id}` | Update object position/content/style |
| DELETE | `/objects/{object_id}` | Delete object |
| POST | `/objects/batch` | Batch create/update/delete (for paste, undo) |
| POST | `/images` | Upload image to canvas (multipart, max 10 MB) |
| GET | `/snapshots` | List snapshots (paginated) |
| POST | `/snapshots` | Create snapshot (manual save) |

All endpoints verify room access via `resolve_room_access` + `ensure_can_read` (GET) or
`ensure_can_send` (mutations). Guest access uses the same `Principal` resolution as
chat messages.

`gen:api` rerun required: **yes**.

### 6.6 Backend -- WebSocket

**Phase 1**: Canvas events published on the existing room channel
(`ws:room:{chatroom_id}`):
- `canvas.object_created`, `canvas.object_updated`, `canvas.object_deleted`
- `canvas.snapshot_created`
- `canvas.settings_updated`

**Phase 2**: Dedicated endpoint `/ws/canvas/{canvas_id}` with its own
`canvas_channel(canvas_id)` (`ws:canvas:{canvas_id}`). Events:
- `canvas.delta` -- Yjs update binary (base64-encoded in JSON frame)
- `canvas.awareness` -- cursor positions, selections, user colors
- Presence subset: `canvas.user_joined`, `canvas.user_left`

The dedicated endpoint uses the same `connection_loop()` with
`authenticate_subprotocol`, room ACL check, and optional presence/awareness callbacks.

### 6.7 Frontend -- new slice `slices/canvas/`

```
slices/canvas/
  api/index.ts            API functions (generated client wrappers)
  components/
    CanvasPanel.vue       Split-pane wrapper, toolbar, fullscreen toggle
    CanvasRenderer.vue    Excalidraw integration (Phase 1: REST; Phase 2: Yjs)
    CanvasToolbar.vue     Object creation tools, snapshot save, settings
  composables/
    useCanvasSocket.ts    WS subscription for canvas events
    useCanvasState.ts     Canvas data management, optimistic updates
    useCanvasSplitPane.ts Drag-to-resize, fullscreen, collapse logic
  queries/index.ts        TanStack Query keys and prefetches
  types/index.ts          Canvas, CanvasObject, CanvasSnapshot types
  routes.ts               No new routes; canvas lives within ChatroomView
  index.ts                Public re-exports
```

### 6.8 Frontend -- ChatroomView integration

The CSS Grid in `ChatroomView.vue` gains a conditional fifth column for the canvas
panel. Layout states:

1. **Canvas closed** (default): Current 4-column layout, unchanged.
2. **Canvas open**: Feed column shrinks, canvas panel appears on the right of the feed.
   The right rail (People/Observer/Activity tabs) moves to overlay or collapses.
3. **Canvas fullscreen**: Canvas takes over the entire content area; chat is hidden.
   A mini-chat FAB shows unread message count and allows quick return.

Toggle button in `ChatroomHeader` (icon from `@heroicons/vue`). Canvas panel state
persisted in `localStorage` per chatroom.

The `useTransientSurfaces` composable is extended to manage canvas vs. right-rail
mutual exclusion at compact breakpoints (< 1280px).

### 6.9 Frontend -- Excalidraw integration

Excalidraw is loaded as a React component; Vue hosts it via a thin `createRoot` bridge
in `CanvasRenderer.vue`. This pattern avoids a full Vue-React interop layer.

**Phase 1**: Excalidraw runs in "view mode" for display and local editing. On "save",
the scene JSON is POSTed to `POST /canvas/snapshots`. Other users receive the update
via `canvas.snapshot_created` WS event and reload.

**Phase 2**: Excalidraw's `@excalidraw/excalidraw` package exposes a Yjs collaboration
provider. The frontend connects to `/ws/canvas/{canvasId}`, exchanges Yjs updates via
`canvas.delta` events, and the backend acts as a relay + persistence layer (periodic
snapshot of Yjs doc to `canvases.crdt_state`).

### 6.10 Deploy/config

- New MinIO bucket prefix: `canvas-images/{project_id}/{canvas_id}/{object_id}/{filename}`.
  Uses the existing `chat_uploads_bucket` (no new bucket needed); new key builder in
  `shared_kernel/storage/minio_client.py`.
- New env var: `CANVAS_IMAGE_MAX_BYTES` (default 10 MB).
- No new containers or services.

## 7. NFR Checklist

- [x] i18n -- all user-facing strings through `$t()`. New namespace `canvas` in
  `frontend/src/slices/canvas/locales/`.
- [x] Audit log -- `canvas.created`, `canvas.deleted`, `canvas.object_created`,
  `canvas.object_deleted`, `canvas.snapshot_created`, `canvas.settings_updated`.
  Follows the `AuditFacade.record()` pattern.
- [x] Tenant isolation -- every canvas endpoint verifies room access via
  `resolve_room_access()`, which checks org/project/room membership. Canvas ID is
  always resolved through its chatroom, never directly, preventing cross-tenant access.
- [x] Error handling UX -- loading state while canvas initializes, error boundary for
  Excalidraw crashes (React error boundary inside the Vue bridge), empty state with
  "Start collaborating" prompt when canvas has no objects.
- [x] Performance -- canvas objects paginated for large boards (> 500 objects);
  snapshot JSONB capped at 5 MB; CRDT state capped at 10 MB with a warning at 8 MB.
  Image uploads limited to 10 MB each, 50 images per canvas.

## 8. Security Considerations

**WebSocket authentication**: The dedicated canvas WebSocket endpoint (Phase 2) uses the
same `authenticate_subprotocol` ticket-based auth as the chatroom endpoint
(`ws_auth.py`). The ticket is single-use, short-lived, and chatroom-scoped.

**Room ACL enforcement**: Every canvas operation re-checks room access. The `authorize`
callback in `connection_loop()` periodically re-verifies ACL so a revoked user is
disconnected from both chat and canvas WebSockets.

**Guest access rate limiting**: Guest canvas mutations are rate-limited to prevent abuse:
max 60 object operations per minute per guest session. Exceeding the limit returns
HTTP 429.

**Image upload validation**: Canvas images go through the same MIME allowlist and AV scan
pipeline as chat attachments (`attachment_service.py`). Only `image/png`, `image/jpeg`,
`image/webp`, `image/svg+xml` are accepted. SVG is sanitized server-side (strip scripts,
event handlers) before storage.

**CRDT state integrity** (Phase 2): The backend validates Yjs update messages before
relay. Malformed updates are dropped and the connection is warned. A corrupted CRDT
document triggers a server-side reset to the last valid snapshot.

**Canvas digest injection**: The `CanvasContextProvider` sanitizes object text content
before including it in the agent's system prompt. Markdown-special characters are
escaped. The digest is capped at 2000 characters to prevent context stuffing.

**Tenant data isolation**: Canvas images use the same MinIO key structure as chat
attachments (`{project_id}/...`), inheriting the same bucket policies and lifecycle
rules.

## 9. Quality Notes

**Existing debt in touched files:**

- `turn_engine.py` is 4400+ lines. The new canvas context provider adds ~10 lines
  (instantiation + call + block). Do not attempt to refactor the engine in this task.
- `ChatroomView.vue` is ~1400 lines (template + script). The split-pane addition is
  localized to the CSS Grid definition and a conditional column. Do not restructure the
  component.
- `useChatroomSocket.ts` is ~750 lines. Canvas Phase 1 events add ~30 lines of event
  handlers. Phase 2 uses a separate composable.

**Patterns to follow:**

- Context provider: `ActivityContextProvider` (`activity_context_provider.py:106`).
- Agent grant: `may_read_drafts` on `ChatroomAgent` (`models.py:113`), `DraftReadGrant`
  (`models.py:117`), `resolve_draft_access` (`draft_tools.py:90`).
- WebSocket endpoint: `chatroom.py` for lifecycle; `workflow_runs.py` for a simpler
  example.
- Frontend composable: `useWorkflowRunSocket.ts` for a simple channel subscription.
- Frontend slice structure: `slices/activities/` for a feature that operates within
  chatrooms.
- MinIO key builder: `chat_upload_key()` in `minio_client.py:272`.
- Audit logging: `AuditFacade.record()` called from application services.

**Reuse inventory:**

| What | Where | Use for |
|------|-------|---------|
| `resolve_room_access()` | `app/api/v1/chatrooms.py` | All canvas endpoint ACL |
| `ensure_can_read()` / `ensure_can_send()` | Same file | Read vs. write gating |
| `connection_loop()` | `shared_kernel/realtime/connection.py:180` | Canvas WS endpoint |
| `Publisher` / `Subscriber` | `shared_kernel/realtime/pubsub.py` | Event pub/sub |
| `PresenceTracker` | `conversation/infrastructure/presence.py:97` | Canvas presence (Phase 2) |
| `MinioClient.put_object()` / `presigned_get()` | `shared_kernel/storage/minio_client.py` | Image upload/download |
| `safe_input_name()` | `shared_kernel/storage/sanitize.py` | Filename sanitization |
| `LabelResolver` | `activities/application/activity_context_provider.py:43` | Pseudonymized labels in digest |
| `WsManager.channel()` | `shared/transport/ws-manager.ts:433` | Frontend WS connection |
| `STabs`, `SDrawer`, `SButton` | `shared/ui/` | Canvas toolbar and panels |
| `useTransientSurfaces` | `conversation/composables/useTransientSurfaces.ts` | Panel mutual exclusion |
| `fetchWsTicket()` | `shared/transport/axios.ts` | WS auth ticket for canvas endpoint |

## 10. Risks and Rollback

| Risk | Impact | Mitigation |
|------|--------|------------|
| Excalidraw React-in-Vue bridge complexity | Rendering bugs, event handling mismatches | Isolate in a single component with strict props/events interface; React error boundary catches crashes |
| CRDT state grows unbounded (Phase 2) | Memory/storage bloat | 10 MB cap on CRDT state; periodic garbage collection of deleted objects; server-side compaction |
| High-frequency WS traffic under many concurrent editors (Phase 2) | Server load, Redis pub/sub pressure | Batch Yjs updates (50ms debounce); dedicated channel isolates from chat traffic; connection cap per canvas |
| Excalidraw bundle size (~1.5 MB gzipped) | Slower initial page load | Lazy-load canvas panel; code-split the Excalidraw chunk; only load when user opens canvas |
| Migration adds three tables | Rollback requires dropping tables | All new tables with no FK from existing tables pointing in; `CASCADE` on chatroom FK means no orphan risk. Fully reversible. |

## 11. Acceptance Criteria

### Phase 1 -- Snapshot-based collaboration

- [ ] AC-1: A chatroom has at most one canvas, created on first access via
  `GET /api/chatrooms/{chatroomId}/canvas`. The canvas is deleted when the chatroom is
  deleted (CASCADE).
- [ ] AC-2: Authenticated users and guests can create, move, resize, edit and delete
  canvas objects (sticky notes, text blocks, shapes, connectors, freeform drawings)
  through the REST API.
- [ ] AC-3: Users can upload images to the canvas (max 10 MB each, max 50 per canvas).
  Images are stored in MinIO and served via presigned URLs.
- [ ] AC-4: Object mutations publish events on the room WebSocket channel
  (`canvas.object_created`, `canvas.object_updated`, `canvas.object_deleted`). Other
  connected clients update their local state on receiving these events.
- [ ] AC-5: Users can manually save a snapshot. The snapshot includes all objects and a
  generated natural-language `agent_digest`.
- [ ] AC-6: The `CanvasContextProvider` injects a `[Canvas content]` block into the
  agent's system prompt when `expose_to_agents` is true and the agent has
  `may_read_canvas` grant. The block is capped at 2000 characters.
- [ ] AC-7: The canvas panel appears as a split-pane in `ChatroomView`. Users can
  drag-to-resize the boundary between chat and canvas, and toggle fullscreen.
- [ ] AC-8: The canvas panel lazy-loads; opening a chatroom without clicking the canvas
  toggle incurs no Excalidraw bundle download.
- [ ] AC-9: Every canvas mutation endpoint verifies room access. A user/guest who loses
  room access cannot read or write to the canvas.
- [ ] AC-10: Canvas CRUD operations emit audit events
  (`canvas.created`, `canvas.deleted`, `canvas.object_created`, etc.).
- [ ] AC-11: Guest canvas mutations are rate-limited to 60 operations per minute per
  session. Exceeding the limit returns HTTP 429.
- [ ] AC-12: The `may_read_canvas` grant is configurable per agent binding in
  `ChatroomSettingsView`, following the same UI pattern as `may_read_drafts`.
- [ ] AC-13: All user-facing strings use `$t()` with keys under the `canvas` i18n
  namespace.

### Phase 2 -- Real-time CRDT sync (future task)

- [ ] AC-14: Multiple users editing the same canvas see each other's changes in real
  time (< 500ms latency under normal conditions).
- [ ] AC-15: Cursor positions and selections are visible to other editors (awareness
  protocol).
- [ ] AC-16: The backend periodically persists the Yjs document state to
  `canvases.crdt_state`. On reconnect, clients hydrate from the persisted state.
- [ ] AC-17: A dedicated `/ws/canvas/{canvasId}` endpoint handles CRDT delta exchange,
  isolated from the chat WebSocket.

### Phase 3 -- AI writes to canvas (future task)

- [ ] AC-18: An agent with `may_write_canvas` grant can add objects to the canvas via a
  `write_to_canvas` built-in tool.
- [ ] AC-19: Canvas writes by agents are visually distinguished (agent avatar/color) and
  emit `canvas.object_created` events like user writes.

## 12. Test Plan

| AC | Level | Location |
|----|-------|----------|
| AC-1 | Integration | `tests/contexts/canvas/test_canvas_lifecycle.py` -- create, cascade delete |
| AC-2 | Integration | `tests/contexts/canvas/test_canvas_objects.py` -- CRUD ops, guest access |
| AC-3 | Integration | `tests/contexts/canvas/test_canvas_images.py` -- upload, size limit, MIME validation |
| AC-4 | Integration | `tests/contexts/canvas/test_canvas_ws.py` -- verify WS events on mutation |
| AC-5 | Integration | `tests/contexts/canvas/test_canvas_snapshots.py` -- save, digest generation |
| AC-6 | Unit + Integration | `tests/contexts/canvas/test_canvas_context_provider.py` -- provider output, grant gating, cap |
| AC-7 | E2E | `frontend/e2e/canvas/split-pane.spec.ts` -- open, resize, fullscreen, close |
| AC-8 | E2E | `frontend/e2e/canvas/lazy-load.spec.ts` -- verify no Excalidraw chunk without toggle |
| AC-9 | Integration | `tests/contexts/canvas/test_canvas_authz.py` -- access denied after room access revoked |
| AC-10 | Integration | `tests/contexts/canvas/test_canvas_audit.py` -- audit events emitted |
| AC-11 | Integration | `tests/contexts/canvas/test_canvas_rate_limit.py` -- guest rate limit enforced |
| AC-12 | E2E | `frontend/e2e/canvas/agent-grant.spec.ts` -- toggle grant in settings |
| AC-13 | E2E | `frontend/e2e/canvas/i18n.spec.ts` -- verify no raw strings |

## 13. SRS Delta

New subsection **S13.11 Collaborative Canvas** in `REQUIREMENTS.md`:

```
### 13.11 Collaborative Canvas

- **[R13.33]** A Chatroom may have at most one Canvas. The Canvas is created on demand
  (first access) and follows the chatroom's lifecycle: deleting the chatroom
  cascade-deletes the canvas, all its objects, and all stored images.

- **[R13.34]** Canvas object types: sticky note, text block, image, freeform drawing,
  shape, connector. Each object has a position, dimensions, z-index and style metadata.

- **[R13.35]** Canvas access mirrors chatroom access: any principal who can read the
  chatroom can view the canvas; any principal who can send messages (including guests
  when `allow_guest_links` is set) can create, edit and delete canvas objects.

- **[R13.36]** Canvas images are stored in MinIO under a dedicated key prefix within the
  existing chat-uploads bucket, following the same AV scan and MIME allowlist pipeline
  as chat attachments. Maximum image size: 10 MB. Maximum images per canvas: 50.

- **[R13.37]** Guest canvas mutations are rate-limited per session (default 60 operations
  per minute). Exceeding the limit returns HTTP 429 with problem type
  `/canvas/rate-limit-exceeded`.

- **[R13.38]** An AI agent bound to the chatroom may receive a natural-language digest of
  the canvas content as a system-prompt block, gated by two conditions: (a) the canvas's
  `expose_to_agents` flag is true (default), and (b) the agent's `may_read_canvas` grant
  is set on its chatroom binding. The digest is capped at 2 000 characters and is
  generated from the canvas objects, not from raw coordinate data.

- **[R13.39]** Canvas mutations publish events on the chatroom's WebSocket channel. Other
  connected clients update their local canvas state on receiving these events. In a later
  phase, a dedicated WebSocket channel carries CRDT deltas for real-time collaborative
  editing.

- **[R13.40]** Canvas snapshots persist the full object state and a generated
  `agent_digest`. Snapshots are created manually by users or automatically on periodic
  intervals. The most recent snapshot's digest is what the CanvasContextProvider serves
  to the agent turn.

- **[R13.41]** Every canvas endpoint and WebSocket connection verifies chatroom access.
  A principal whose room access is revoked is disconnected from both the chatroom and
  canvas WebSocket channels.
```

## 14. Open Questions

- **OQ-1**: Excalidraw's React-in-Vue hosting: should we use a dedicated npm package
  for the bridge (e.g. `veaury`), or a minimal `createRoot` wrapper? Decide at
  implementation time based on bundle size and maintenance burden.
- **OQ-2**: Phase 2 CRDT relay -- should the backend decode and validate Yjs updates, or
  act as a blind relay? Decoding adds safety (malformed update rejection) but requires
  a Python Yjs library (`pycrdt`). Decide when Phase 2 is scoped.
- **OQ-3**: Canvas object text searchability -- should canvas text be indexed in the
  chatroom's full-text search? Deferred to a follow-up.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: Phase 2 -- Real-time CRDT sync (AC-14 through AC-17). Separate dossier.
- FU-2: Phase 3 -- AI writes to canvas (AC-18, AC-19). Separate dossier.
- FU-3: Canvas export to PNG/PDF.
- FU-4: Canvas templates (pre-built layouts for common scenarios).
- FU-5: Full-text search over canvas object text.
- FU-6: Canvas version history (browse/restore past snapshots).
- FU-7: Canvas object comments / annotations.
