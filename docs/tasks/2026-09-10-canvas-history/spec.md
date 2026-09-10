---
type: feature
status: approved
created: 2026-09-10
requirements: [R13.42, R13.49]
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Version History

## 1. Summary

Allow users to browse and restore past canvas snapshots through a version history panel
accessible from the canvas toolbar. The panel shows a chronological timeline of saved
snapshots with metadata (author, timestamp, object count summary), supports read-only
preview of any past state, and lets users restore a historical snapshot. Restoring
auto-saves the current state before overwriting, so no work is lost. A per-canvas cap of
50 snapshots prevents unbounded growth, with the oldest auto-pruned when the cap is
exceeded.

Snapshots remain manually triggered ("save version") actions. In Phase 2 (CRDT), the
live CRDT document state is persisted automatically; snapshots become explicit named
checkpoints rather than the primary persistence mechanism.

## 2. Goals and Non-goals

**Goals**

- Version history panel accessible from the canvas toolbar, listing all snapshots in
  reverse chronological order.
- Snapshot metadata: author display name, timestamp, object count by kind (e.g., "3
  notes, 2 shapes, 1 image").
- Optional user-provided label on snapshot creation (one-line description).
- Read-only preview of a past snapshot without modifying the current canvas.
- Restore a past snapshot: auto-save current state, then replace current canvas state
  with the snapshot's data.
- Restore emits WebSocket events so other connected clients update.
- Per-canvas cap of 50 snapshots; oldest auto-pruned on new snapshot creation.

**Non-goals**

- Diff view between snapshots (showing what changed between two versions).
- Granular object-level undo across sessions (in-session undo is Excalidraw's domain).
- Automatic periodic snapshots (Phase 2 CRDT handles live persistence; snapshots are
  explicit user actions).
- Snapshot branching or forking (restoring is a linear replacement, not a branch).

## 3. Clarifications

- **Q-1**: Auto-snapshot policy? **Manual only.** CRDT persistence (Phase 2) replaces
  auto-save. Snapshots are an explicit user action ("save version"). Cap at 50 per canvas.

- **Q-2**: Preview implementation? **Read-only Excalidraw instance.** The preview renders
  the snapshot's `snapshot_data` in a non-interactive Excalidraw view (same renderer,
  `viewModeEnabled: true`). Simpler than building a custom renderer and guarantees visual
  fidelity with the editor.

## 4. Requirements

### 4.1 Existing requirements touched

- **[R13.49]** (extended): Canvas snapshots now support a user-provided label, retrieval
  by ID, and restoration. The existing snapshot creation and listing behavior is
  unchanged. A per-canvas cap of 50 snapshots enforces bounded growth.

### 4.2 New requirements (SRS Delta)

- **[R13.64]** A canvas snapshot may carry an optional label (max 200 characters) set at
  creation time. The label is user-facing text and must be sanitized (stripped of control
  characters and excessive whitespace).

- **[R13.65]** A user who can send messages in the chatroom may restore any snapshot of
  that room's canvas. Restoring auto-saves the current canvas state as a new snapshot
  (labeled "Auto-save before restore") before replacing the current state with the
  historical snapshot's `snapshot_data`. The restore operation emits a
  `canvas.snapshot_restored` WebSocket event on the room channel.

- **[R13.66]** Each canvas retains at most 50 snapshots. When creating a new snapshot
  would exceed the cap, the oldest snapshot (by `created_at`) is deleted before the new
  one is inserted.

## 5. Acceptance Criteria

- [ ] AC-1: The canvas toolbar shows a "History" button that opens the version history
  panel. Verified visually.
- [ ] AC-2: The version history panel lists all snapshots in reverse chronological order,
  showing author name, timestamp, object count summary, and label (if any).
- [ ] AC-3: Clicking a snapshot in the list opens a read-only preview (Excalidraw in
  view-only mode) of that snapshot's state.
- [ ] AC-4: Clicking "Restore" on a previewed snapshot replaces the current canvas state
  and auto-saves the pre-restore state as a new snapshot. Verified by checking that the
  auto-save snapshot appears in the history.
- [ ] AC-5: The restore operation emits a `canvas.snapshot_restored` WS event. Other
  connected clients see the restored state. Verified by a two-client test.
- [ ] AC-6: Snapshot creation accepts an optional label (max 200 chars). The label
  appears in the history panel.
- [ ] AC-7: Creating a 51st snapshot auto-prunes the oldest one. Verified by test.
- [ ] AC-8: `GET /canvas/snapshots/{snapshot_id}` returns the full `snapshot_data` for a
  snapshot belonging to the canvas. Returns 404 for non-existent or cross-canvas IDs.
- [ ] AC-9: `POST /canvas/snapshots/{snapshot_id}/restore` performs the auto-save +
  restore sequence atomically.

## 6. Detailed Changes

### 6.1 Backend -- Migration

Alembic migration to add `label` column (sa.String(200), nullable=True) to
`canvas_snapshots` table.

### 6.2 Backend -- Repository additions

`contexts/canvas/infrastructure/repositories/canvas_repo.py`:

- `get_snapshot(snapshot_id, canvas_id) -> CanvasSnapshot | None`: fetch one snapshot by
  ID, scoped to canvas.
- `count_snapshots(canvas_id) -> int`: count snapshots for pruning check.
- `delete_oldest_snapshot(canvas_id) -> None`: delete the snapshot with the earliest
  `created_at`.
- Update `create_snapshot` to accept `label` in values.

### 6.3 Backend -- Service additions

`contexts/canvas/application/canvas_service.py`:

- `get_snapshot(canvas_id, snapshot_id) -> CanvasSnapshot | None`: fetch with access
  check.
- `restore_snapshot(canvas_id, snapshot_id, ...) -> CanvasSnapshot`:
  1. Read the target snapshot's `snapshot_data`.
  2. Create a new auto-save snapshot from current state (label: "Auto-save before
     restore").
  3. Batch-delete all current objects.
  4. Batch-create objects from the historical snapshot's data.
  5. Emit `canvas.snapshot_restored` on room channel.
  6. Return the newly created auto-save snapshot.
- Update `create_snapshot` to enforce the 50-snapshot cap (prune before insert).
- Update `create_snapshot` to accept `label`.

### 6.4 Backend -- Facade additions

`contexts/canvas/interfaces/facade.py`: pass-through for `get_snapshot` and
`restore_snapshot`.

### 6.5 Backend -- API endpoints

`backend/app/api/v1/canvas.py`:

- `GET /canvas/snapshots/{snapshot_id}`: returns full `SnapshotOut` including
  `snapshot_data` (new field on the response model). Requires `ensure_can_read`.
- `POST /canvas/snapshots/{snapshot_id}/restore`: calls
  `facade.restore_snapshot(...)`. Requires `ensure_can_send`. Returns the auto-save
  snapshot.
- Update `POST /canvas/snapshots` to accept optional `label` in request body.
- Add `SnapshotDetailOut` response model with `snapshot_data` field.
- Add `SnapshotCreateIn` request model with optional `label` field.

### 6.6 Frontend -- History panel component

New file: `slices/canvas/components/CanvasHistory.vue`

- Slide-out panel (or sidebar panel) triggered by "History" toolbar button.
- Fetches snapshots via `GET /canvas/snapshots` (existing endpoint).
- Each row: author avatar/name, relative timestamp, object count summary, label.
- Click to preview: fetches `GET /canvas/snapshots/{id}`, renders in a read-only
  Excalidraw (`viewModeEnabled: true`) in a modal or inline preview area.
- Restore button: confirms via `SConfirmDialog`, calls
  `POST /canvas/snapshots/{id}/restore`.

### 6.7 Frontend -- Toolbar addition

Modify `slices/canvas/components/CanvasToolbar.vue`:

- Add "History" button (ClockIcon from @heroicons/vue).
- Add optional label input on the "Save" action (inline text field or prompt).

### 6.8 Frontend -- API and types

- Add `getSnapshot(chatroomId, snapshotId)` and `restoreSnapshot(chatroomId, snapshotId)`
  to `slices/canvas/api/index.ts`.
- Extend `CanvasSnapshot` type with optional `label` and `snapshot_data` fields.
- Add `canvas.snapshot_restored` WS event handler in `useCanvasSocket.ts`.

## 7. Existing Debt and Patterns

### Patterns to follow

- Snapshot CRUD: existing `list_snapshots`, `create_snapshot` in `canvas_repo.py:217-246`.
- Restore pattern: `batch_operate` in `canvas_service.py` for bulk object manipulation.
- Confirmation dialog: `SConfirmDialog` from `shared/ui/`.
- Panel UI: follow `ChatroomAgentSidebar.vue` or the settings panel pattern.

### Reuse inventory

| What | Where | Use |
|------|-------|-----|
| `CanvasRepository` snapshot methods | `canvas_repo.py:217-246` | Extend for get/prune |
| `batch_operate()` | `canvas_service.py:242` | Restore object replacement |
| `SConfirmDialog` | `shared/ui/SConfirmDialog.vue` | Restore confirmation |
| `Publisher` | `shared_kernel/realtime/pubsub.py:44` | WS event emission |
| `room_channel` | `conversation/infrastructure/channels.py:12` | WS channel |

## 8. Security Considerations

- Snapshot label is user input: strip control characters, enforce 200-char max at the
  Pydantic model boundary.
- Restore requires `ensure_can_send` (write access), not just `ensure_can_read`.
- Snapshot retrieval is scoped to the canvas (and therefore the chatroom): a snapshot ID
  from another canvas returns 404, not the data.
- Guest rate limiting ([R13.46]) applies to restore and snapshot creation.

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Restoring a large snapshot is slow | Low | Medium | Batch operations are already optimized; add a loading indicator |
| 50-snapshot cap too low for active canvases | Low | Low | Cap is configurable via env var; increase if needed |

## 10. Test Plan

### Unit tests

- `test_snapshot_prune.py`: creating 51st snapshot deletes oldest, count stays at 50.
- `test_snapshot_restore.py`: restore creates auto-save, replaces objects, emits event.
- `test_snapshot_label.py`: label sanitization, 200-char cap, optional field.

### Integration tests (pytest.mark.db)

- `test_snapshot_get_by_id.py`: fetch by ID, cross-canvas 404, non-existent 404.
- `test_snapshot_restore_e2e.py`: full restore sequence with object verification.

### E2E tests (Playwright)

- Open history panel, see snapshot list.
- Preview a snapshot (read-only view).
- Restore a snapshot, verify canvas updates and auto-save appears.

## 11. Open Questions

None.

## 12. Deviation Log

(Populated during implementation.)

## 13. Follow-ups

- FU-1: Diff view between snapshots (visual comparison of two versions).
- FU-2: Snapshot export (download a snapshot as a standalone file).
