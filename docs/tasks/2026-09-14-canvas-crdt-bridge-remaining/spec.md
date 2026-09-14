---
type: feature
status: implemented
created: 2026-09-14
requirements: [R13.45, R13.47, R13.56, R13.57, R13.58, R13.60, R13.61]
depends_on: []
---

# Canvas REST-CRDT Bridge: Image Upload, Agent Writes, Save-as-Template

## 1. Summary

Three canvas features remain non-functional after the Excalidraw CRDT migration because
they operate on the REST `canvas_objects` table instead of the Yjs CRDT document that
Excalidraw renders from. This spec bridges the three remaining features using the
`CrdtRelay.inject_elements()` primitive and the `yjs-update` broadcast pattern already
proven by snapshot restore and search (implemented 2026-09-14).

The three features are: image upload (server-side injection into CRDT + WS-based file
delivery to Excalidraw), agent canvas write tools (switch from REST to CRDT-only), and
save-as-template (read from CRDT state instead of REST objects).

## 2. Goals and Non-goals

**Goals**

- Images uploaded via the toolbar appear on the Excalidraw canvas in real time for all
  connected editors.
- AI agent create/update/delete canvas tools produce visible changes in Excalidraw.
- Save-as-template captures the actual Excalidraw drawing (from CRDT), not an empty REST
  object set.

**Non-goals**

- Bidirectional REST-CRDT sync. The REST `canvas_objects` table becomes a legacy store;
  new writes go to CRDT only. Existing REST objects are not migrated (the one-time
  migration on first WS connect already handles this per [R13.54]).
- Template apply (restored separately via snapshot restore's `inject_elements(replace=true)`
  pattern in the same session; already working).
- Comment threads on canvas objects (requires a rethink of the FK model; deferred).
- Excalidraw-native image paste/drag-drop (handled by Excalidraw internally, no backend
  involvement).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should uploaded image data reach the Excalidraw frontend? | WS event + presigned URL | Backend emits `canvas.image_added` with fileId + presigned URL. Frontend fetches, converts to dataURL, calls `excalidrawApi.addFiles()`. Keeps image data in MinIO; avoids bloating the CRDT doc (10 MB cap per [R13.51]). |
| Q-2 | Should agent writes dual-write to REST + CRDT, or CRDT-only? | CRDT-only | Clean break. Agent reads already work from CRDT via `CanvasContextProvider`. Dual-write adds complexity and divergence risk with no consumer for the REST side. |
| Q-3 | Should saved templates store Excalidraw element format or legacy REST shape? | Excalidraw native format | Store raw Excalidraw elements directly. Template apply uses `inject_elements()`. Legacy templates (REST shape) still work via the conversion code in snapshot restore. |
| Q-4 | Any dependency on existing non-implemented dossiers? | None | All 8 canvas dossiers are `implemented`. The 3 active non-canvas dossiers (`graphrag-two-axis-redesign`, `large-artifacts-silently-dropped`, `member-groups-and-room-visibility-isolation`) do not overlap with canvas files. |

## 4. Current State

The canvas has two parallel data stores that are not synchronized bidirectionally:

- **REST objects** (`canvas_objects` table): written by the Phase 1 API endpoints, queried
  by legacy search/comments/agents. Since the Excalidraw migration, user-facing drawing
  happens entirely in CRDT; the REST table is stale or empty for new canvases.
- **CRDT state** (`canvases.crdt_state` + in-memory `CrdtRelay`): the Yjs doc that
  Excalidraw renders. The source of truth for all visible canvas content.

The bridge primitive `CrdtRelay.inject_elements()` (`crdt_relay.py:206-243`) already
handles server-side writes into the CRDT doc, including doc loading when no editors are
connected. The broadcast pattern (emit `yjs-update` to `canvas_channel(canvas_id)`) is
proven by snapshot restore (`canvas_service.py:462-464`).

**Image upload** (`canvas.py:520-599`): uploads to MinIO, creates a REST `canvas_objects`
row with `kind=IMAGE` and `minio_path`. Returns a presigned URL. Excalidraw never sees the
image because nothing injects it into the CRDT doc or registers the file data with
Excalidraw's `addFiles()` API.

**Agent tools** (`contexts/agents/application/runtime/canvas_tools.py`):
- `_build_create_tool` (line 98-176): calls `CanvasFacade.create_object()` at line 133.
- `_build_update_tool` (line 179-250): calls `CanvasFacade.update_object()` at line 214.
- `_build_delete_tool` (line 253-309): calls `CanvasFacade.delete_object()` at line 275.
All three operate exclusively on REST objects.

**Save-as-template** (`canvas_templates.py:294-336`): delegates to
`CanvasTemplateService.create_from_canvas()` (`template_service.py:204-241`), which reads
from `self._canvas_repo.list_objects(canvas_id)` at line 215. Since REST objects are empty
for CRDT-era canvases, saved templates contain no elements.

## 5. Design

### Options considered

**Option A -- Extend inject_elements for all three features**: use the existing
`CrdtRelay.inject_elements()` for image upload and agent creates, add a
`CrdtRelay.update_element()` and `CrdtRelay.delete_element()` for agent updates/deletes,
and read from `extract_elements_for_digest()` for save-as-template. Each feature follows
the same persist-and-broadcast pattern proven by snapshot restore.

**Option B -- Build a generic REST-to-CRDT sync layer**: intercept all REST writes at the
repository level and automatically mirror them into CRDT. Handles future features
automatically but is over-engineered for three specific call sites and doubles every write.

### Decision

Option A. Each call site has specific needs (image upload requires file delivery, agent
update requires per-field merge, template save is read-only). A generic layer would paper
over those differences without simplifying them. Three focused integration points are
easier to audit and test.

## 6. Detailed Changes

### Backend

**`contexts/canvas/application/crdt_relay.py`**

Add two helper methods to `CrdtRelay`:

- `update_element(canvas_id, element_id, fields, *, crdt_state)` -- loads the doc, finds
  the element in the Y.Map by id, merges `fields` into it, persists, returns state bytes.
- `delete_element(canvas_id, element_id, *, crdt_state)` -- sets `isDeleted: true` on the
  element, persists, returns state bytes.

Both reuse the lock-acquire + persist + dirty-flag pattern from `inject_elements`.

**`contexts/agents/application/runtime/canvas_tools.py`**

- `_build_create_tool._invoke` (line 133): replace `facade.create_object()` with
  `relay.inject_elements()`. Build the Excalidraw element dict from tool args using the
  `_EXCALIDRAW_ELEMENT_KINDS` mapping. Persist to DB and broadcast via
  `Publisher(canvas_channel(canvas_id))`.
- `_build_update_tool._invoke` (line 214): replace `facade.update_object()` with
  `relay.update_element()`. Map tool args to Excalidraw element fields. Persist and
  broadcast.
- `_build_delete_tool._invoke` (line 275): replace `facade.delete_object()` with
  `relay.delete_element()`. Persist and broadcast.

The tools still need `canvas_id` to address the CRDT doc; derive it from the
facade's `get_or_create(chatroom_id)` as they already do.

**`app/api/v1/canvas.py` (upload_image, lines 520-599)**

After the MinIO upload and presigned URL generation:
1. Build an Excalidraw image element: `type: "image"`, `fileId: str(object_id)`,
   `x: 0`, `y: 0`, `width: 400`, `height: 300`, plus standard Excalidraw props.
2. Call `relay.inject_elements(canvas_id, [element], crdt_state=...)`.
3. Persist `crdt_state` to DB.
4. Broadcast `yjs-update` to `canvas_channel(canvas_id)`.
5. Broadcast `canvas.image_added` to `room_channel(chatroom_id)` with
   `{ fileId, url (presigned), mimeType, width, height }`.

**`contexts/canvas/application/template_service.py` (create_from_canvas, line 215)**

Replace `self._canvas_repo.list_objects(canvas_id)` with:
1. Try `relay.extract_elements_for_digest(canvas_id)` if relay has the doc.
2. Fall back to loading `crdt_state` from DB and extracting elements.
3. Store the raw Excalidraw element list as `template_data.elements`.

Template apply already works via `inject_elements(replace=true)` (same pattern as
snapshot restore).

**Migration required:** No. All changes operate on existing columns and in-memory state.

### API contract

- `upload_image` response unchanged (`CanvasObjectOut` with `image_url`).
- New WS event `canvas.image_added` on the room channel: `{ fileId: string, url: string, mimeType: string, width: number, height: number }`.
- Agent tool input/output schemas unchanged (the tools accept the same args, the CRDT
  injection is transparent).
- `save_as_template` response unchanged, but `template_data` now contains
  `{ elements: [...] }` in Excalidraw format instead of `{ objects: [...] }` in REST
  format.
- `gen:api` rerun: not required (no REST endpoint signature changes).

### Frontend

**`src/slices/canvas/components/CanvasRenderer.vue`**

Add image file registration in `onYjsChange`: when a remote update adds an element with
`type: "image"` and a `fileId`, check whether Excalidraw already has that file registered.
If not, fetch the image URL from a new query or from a WS event cache.

**`src/slices/canvas/composables/useCanvasSocket.ts`**

Subscribe to `canvas.image_added` events. On receipt, fetch the presigned URL, convert to
dataURL via `fetch()` + `FileReader`, and call `excalidrawApi.addFiles([{ id: fileId,
dataURL, mimeType, created: Date.now() }])`.

**`src/slices/canvas/components/CanvasPanel.vue`**

Re-enable the upload image button in the toolbar. Pass the Excalidraw API ref to the
socket composable so it can call `addFiles()`.

**`src/slices/canvas/components/CanvasToolbar.vue`**

Add back the upload image (PhotoIcon) button.

**i18n keys to restore:** `uploadImage` in both `en.json` and `zh-TW.json`.

### Deploy/config

No changes. MinIO bucket, Vault paths, and compose config are unchanged.

## 7. NFR Checklist

- [x] i18n -- `uploadImage` key already exists in locale files (was removed, will be
  restored verbatim).
- [x] Audit log -- agent tool invocations already emit audit events; the CRDT injection
  is a transparent backend change. Image upload already emits `canvas.object_created`.
- [x] Tenant isolation -- all three features go through
  `resolve_room_access()` / `ensure_can_send()` before reaching the CRDT layer.
- [x] Error handling UX -- image upload failure shows toast (existing handler). Agent tool
  errors propagate through the orchestration error surface. Template save shows
  success/error toast.
- [x] Performance -- `inject_elements` acquires a per-canvas async lock, serializing
  concurrent writes. Image fetch for `addFiles` is lazy (only when an image element
  appears). No N+1 risk; each operation is a single doc mutation.

## 8. Security Considerations

**Image upload:** the presigned URL in the `canvas.image_added` WS event has the same
lifetime and access scope as the existing presigned URLs in `CanvasObjectOut.image_url`.
The WS connection is already authenticated and scoped to the chatroom. No new attack
surface.

**Agent writes to CRDT:** agents already have `may_write_canvas` permission checks
(`canvas_tools.py:108-112`). The CRDT injection replaces the REST write but the
authorization gate is unchanged.

**Template data:** storing raw Excalidraw elements does not introduce XSS risk; template
application writes to the Yjs doc (never rendered as HTML). Text content in elements is
rendered by Excalidraw's own sanitized canvas rendering.

## 9. Quality Notes

**Existing debt:**
- `canvas_tools.py` imports `CanvasFacade` and constructs it with a fresh DB session per
  tool invocation. The CRDT bridge will need to also import `CrdtRelay` and
  `CanvasRepository` (for `get_crdt_state`). This is acceptable given the tool's existing
  structure but should not proliferate further.
- `template_service.py:215` reads REST objects with no fallback; the CRDT read should
  follow the same fallback chain as `create_snapshot` (`crdt_relay.py:337-362`).

**Patterns to follow:**
- Snapshot restore (`canvas_service.py:392-489`): the canonical pattern for server-side
  CRDT injection + persist + broadcast.
- `_migrate_from_objects` (`crdt_relay.py:83-128`): the element shape reference for
  converting REST kinds to Excalidraw types.

**Reuse inventory:**
- `CrdtRelay.inject_elements()` (`crdt_relay.py:206-243`)
- `CrdtRelay.extract_elements_for_digest()` (`crdt_relay.py:245-256`)
- `_EXCALIDRAW_ELEMENT_KINDS` mapping (`crdt_relay.py:29-36`)
- `Publisher` + `canvas_channel()` broadcast pattern
- `useToast()`, `useCanvasSocket()` on the frontend

## 10. Risks and Rollback

**Risk: image file delivery race.** The CRDT update (containing the image element) and
the `canvas.image_added` WS event travel on different channels (canvas WS vs room WS).
The element may appear before the file data is registered, causing a brief placeholder
icon. Mitigation: Excalidraw shows a loading placeholder for unresolved fileIds, which is
acceptable UX. The file registration follows within milliseconds.

**Risk: agent CRDT writes when no editor connected.** `inject_elements` handles this by
loading from `crdt_state` in DB. After injection, the updated state is persisted. When an
editor later connects, `get_or_load` reads the persisted state. No data loss.

**Rollback:** no migration involved. Reverting the code changes restores the REST-only
behavior. CRDT state persisted by the new code remains valid and readable by the existing
WS handler.

## 11. Acceptance Criteria

- [ ] AC-1: Upload an image via the toolbar button; the image appears on the Excalidraw
  canvas within 2 seconds for all connected editors. (Code complete; needs running stack)
- [ ] AC-2: An AI agent with `may_write_canvas` creates a text element via the canvas
  create tool; the text appears on the Excalidraw canvas in real time. (Unit: inject_elements called with correct element; needs running stack)
- [ ] AC-3: An AI agent updates an existing element's position via the canvas update tool;
  the element moves on the Excalidraw canvas. (Unit: update_element called with mapped fields)
- [ ] AC-4: An AI agent deletes an element via the canvas delete tool; the element
  disappears from the Excalidraw canvas. (Unit: delete_element called)
- [ ] AC-5: Save-as-template on a canvas with Excalidraw drawings produces a template
  that, when applied to a new canvas, reproduces those drawings. (Unit: template_data has elements key; needs running stack)
- [x] AC-6: Image upload respects the existing 10 MB file size limit and 50 image cap
  per canvas ([R13.45]). (Existing guards unchanged: canvas.py:539,557-562)
- [x] AC-7: Agent canvas writes respect `may_write_canvas` permission; an agent without
  the grant receives a tool error. (Existing resolve_canvas_write gate unchanged; unit tests pass)
- [x] AC-8: The CRDT doc does not exceed the 10 MB cap ([R13.51]) after image element
  injection (image binary data stays in MinIO, not in the doc). (Unit: test_image_element_stays_small passes, doc < 1KB)

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1 | Integration (manual via `run` skill) | Open canvas in two tabs, upload image in one, verify it appears in both |
| AC-2 | Unit + integration | `tests/unit/test_canvas_write_tools.py` -- mock relay, verify `inject_elements` called; manual: trigger agent in chatroom with canvas open |
| AC-3 | Unit | `tests/unit/test_canvas_write_tools.py` -- verify `update_element` called with correct fields |
| AC-4 | Unit | `tests/unit/test_canvas_write_tools.py` -- verify `delete_element` called |
| AC-5 | Unit + manual | `tests/unit/test_canvas_templates.py` -- verify template_data contains Excalidraw elements; manual: save template, apply to new canvas |
| AC-6 | Unit (existing) | `tests/unit/test_canvas_*` -- existing size/count limit tests still pass |
| AC-7 | Unit (existing) | `tests/unit/test_canvas_write_tools.py` -- existing permission tests still pass |
| AC-8 | Unit | `tests/unit/test_crdt_relay.py` -- inject an image element, verify doc size excludes binary data |

## 13. SRS Delta

Amend [R13.57] and [R13.60]-[R13.61]:

```
[R13.57] Three built-in agent canvas tools (create, update, delete). Tools operate on the
         CRDT document via `CrdtRelay.inject_elements()` / `update_element()` /
         `delete_element()` and broadcast changes to connected editors in real time.
         REST `canvas_objects` are not written.

[R13.60] Applying a canvas template injects its elements into the CRDT document via
         `inject_elements(replace=true)`, replacing existing content. Legacy templates
         (REST object format) are converted to Excalidraw element format on apply.

[R13.61] Save-as-template reads Excalidraw elements from the CRDT document (in-memory
         relay or persisted `crdt_state`). Template data stores raw Excalidraw element
         format.
```

Add new requirement:

```
[R13.71] Image upload injects an Excalidraw image element into the CRDT document and
         broadcasts a `canvas.image_added` event with a presigned MinIO URL. The frontend
         registers the image binary via `excalidrawApi.addFiles()`. Image binary data is
         never stored in the CRDT document.
```

## 14. Open Questions

None. All blocking questions resolved in section 3.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

No deviations. The implementation matches the spec exactly.

## 16. Follow-ups

- FU-1: Comment threads on canvas objects require a new addressing model (Excalidraw
  element IDs instead of REST object UUIDs as FK targets). Separate spec.
- FU-2: REST `canvas_objects` table cleanup migration -- once all features are bridged,
  the table is dead weight. Should be assessed after this spec is implemented and stable.
- FU-3: Agent canvas tools currently accept REST-model args (`kind`, `position_x`, etc.).
  Consider accepting Excalidraw-native args (`type`, `x`, `y`) in a future version to
  reduce the mapping layer.
