---
type: feature
status: draft
created: 2026-09-10
requirements: []
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Version History

## 1. Summary

Allow users to browse and restore past canvas snapshots. The version history panel shows
a timeline of saved snapshots with metadata (who saved, when, object count), and users
can preview or restore any previous state. Restoring creates a new snapshot of the
current state before applying the historical one, so no work is lost.

## 2. Goals and Non-goals

**Goals**

- A version history panel accessible from the canvas toolbar.
- Timeline view of all snapshots with metadata (author, timestamp, object summary).
- Preview a past snapshot without modifying the current canvas.
- Restore a past snapshot (replaces current objects with the snapshot's state).
- Restoring auto-saves the current state as a new snapshot before overwriting.

**Non-goals**

- Diff view between snapshots (showing what changed).
- Granular object-level undo across sessions (in-session undo is Excalidraw's).
- Automatic periodic snapshots (manual save only in Phase 1; auto-save is a separate
  concern).

## 3. Acceptance Criteria

- [ ] AC-1: The canvas toolbar shows a "History" button that opens the version panel.
- [ ] AC-2: The version panel lists all snapshots in reverse chronological order.
- [ ] AC-3: Selecting a snapshot shows a read-only preview of that state.
- [ ] AC-4: Restoring a snapshot replaces the current canvas objects and auto-saves the
  pre-restore state.
- [ ] AC-5: The restore operation emits appropriate WebSocket events so other clients
  update.

## 4. Key Technical Decisions

- Restore is implemented as: snapshot current state, then batch-delete + batch-create
  from the historical snapshot's `snapshot_data`.
- Preview could be a read-only Excalidraw instance or a simplified rendering.

## 5. Detailed Changes

_To be filled during `/spec` analysis._

## 6. Test Plan

_To be filled during `/spec` analysis._
