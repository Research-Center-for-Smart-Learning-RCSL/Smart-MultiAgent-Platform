---
type: feature
status: draft
created: 2026-09-10
requirements: []
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Phase 2 -- Real-time CRDT Sync

## 1. Summary

Replace the Phase 1 snapshot-based canvas synchronization with real-time collaborative
editing powered by Yjs CRDT. Multiple users editing the same canvas see each other's
changes within 500ms, with cursor/selection awareness, a dedicated WebSocket endpoint
for delta exchange, and periodic backend persistence of the Yjs document state.

## 2. Goals and Non-goals

**Goals**

- Sub-500ms real-time collaborative editing on the spatial canvas.
- Cursor and selection awareness (user colors, positions) visible to all editors.
- Dedicated `/ws/canvas/{canvas_id}` WebSocket endpoint isolated from chat traffic.
- Backend persistence of Yjs document state to `canvases.crdt_state` for reconnect
  hydration.
- Graceful handling of temporary disconnections via CRDT merge on reconnect.

**Non-goals**

- Long-term offline editing (temporary disconnections only).
- Canvas-to-canvas linking or cross-room CRDT state sharing.
- Removing the REST object CRUD (kept for non-CRDT operations like image upload).

## 3. Acceptance Criteria

- [ ] AC-1: Multiple users editing the same canvas see each other's changes in real
  time (< 500ms latency under normal conditions). (from parent AC-14)
- [ ] AC-2: Cursor positions and selections are visible to other editors (awareness
  protocol). (from parent AC-15)
- [ ] AC-3: The backend periodically persists the Yjs document state to
  `canvases.crdt_state`. On reconnect, clients hydrate from the persisted state. (from
  parent AC-16)
- [ ] AC-4: A dedicated `/ws/canvas/{canvas_id}` endpoint handles CRDT delta exchange,
  isolated from the chat WebSocket. (from parent AC-17)
- [ ] AC-5: Phase 1 snapshot creation still works alongside CRDT state.

## 4. Key Technical Decisions

- Python Yjs library: `pycrdt` for server-side document validation (OQ-2 from parent).
- Frontend: Excalidraw's Yjs provider (`@excalidraw/excalidraw` Yjs integration or
  `y-excalidraw`).
- WebSocket uses the same `connection_loop()` pattern with `authenticate_subprotocol`
  and room ACL check.

## 5. Detailed Changes

_To be filled during `/spec` analysis._

## 6. Test Plan

_To be filled during `/spec` analysis._

## 7. Open Questions

- OQ-1: Should the backend decode and validate Yjs updates, or act as a blind relay?
  (carried from parent OQ-2). Decoding adds safety but requires `pycrdt`.
- OQ-2: How to handle the transition from Phase 1 object-based state to CRDT state
  for existing canvases with objects?
