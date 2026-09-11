---
type: feature
status: in-progress
created: 2026-09-10
requirements: [R13.42, R13.43]
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Export to PNG/SVG

## 1. Summary

Allow users to export the current canvas state as a PNG image or SVG file directly from
the browser. The export leverages Excalidraw's built-in `exportToBlob()` and
`exportToSvg()` utilities, requiring no server-side rendering. A dropdown menu in the
canvas toolbar offers format and resolution options, and the file downloads to the user's
device. This is a frontend-only feature with no backend changes.

## 2. Goals and Non-goals

**Goals**

- Export canvas to PNG at 1x or 2x resolution from the canvas toolbar.
- Export canvas to SVG from the canvas toolbar.
- Exported output includes all visible object types (notes, text, shapes, drawings,
  connectors, images) at their current positions and sizes.
- Export respects the current theme (light/dark background).
- Export includes padding around the canvas content (Excalidraw's default export padding).

**Non-goals**

- Server-side rendering or Arq-job-based export pipeline.
- PDF export (would require a server-side SVG-to-PDF conversion; deferred).
- Batch export of multiple canvases or snapshots.
- Export with custom branding, watermarks, or headers/footers.
- Export of canvas history/snapshots (only the current visible state).
- Scheduled or automated exports.

## 3. Clarifications

- **Q-1**: Client-side or server-side export? **Client-side.** Excalidraw 0.18.x
  provides `exportToBlob()` (PNG) and `exportToSvg()` (SVG) APIs that run entirely in the
  browser. No backend infrastructure needed. PNG + SVG are supported natively; PDF is
  deferred because it would require server-side conversion.

- **Q-2**: Resolution options? **1x and 2x for PNG.** 1x produces a pixel-for-pixel
  screenshot; 2x produces a retina-quality image at double the dimensions. SVG is
  resolution-independent, so no scale option is needed.

## 4. Requirements

### 4.1 Existing requirements touched

None. This feature adds UI capability without changing any existing behavior.

### 4.2 New requirements (SRS Delta)

None. Canvas export is a client-side convenience feature that does not alter the
platform's data model, access control, or persistence behavior. No SRS entry warranted.

## 5. Acceptance Criteria

- [ ] AC-1: A user can export the current canvas as a PNG file (1x resolution) from the
  toolbar. The file downloads to the user's device with a meaningful filename
  (`canvas-{chatroomName}-{timestamp}.png`).
- [ ] AC-2: A user can export the current canvas as a PNG file at 2x resolution.
- [ ] AC-3: A user can export the current canvas as an SVG file from the toolbar.
- [ ] AC-4: Exported images include all object types at correct positions and sizes.
  Verified by visual inspection of an export containing at least one of each object type.
- [ ] AC-5: Export respects the current theme (light background in light mode, dark in
  dark mode).
- [ ] AC-6: The export button is disabled (or hidden) when the canvas has no objects.
- [ ] AC-7: Export works while the canvas is in fullscreen mode.

## 6. Detailed Changes

### 6.1 Frontend -- Export composable

New file: `slices/canvas/composables/useCanvasExport.ts`

- `useCanvasExport(excalidrawApi: Ref<ExcalidrawAPI | null>)` composable.
- `exportPng(scale: 1 | 2)`: calls `excalidrawApi.getSceneElements()` and
  `exportToBlob({ elements, appState, scale })`. Triggers a browser download via
  `URL.createObjectURL()` + a temporary `<a>` element.
- `exportSvg()`: calls `exportToSvg({ elements, appState })`. Serializes the SVG
  element to a string, creates a Blob, and triggers download.
- `filename(ext: string)`: generates `canvas-{chatroomName}-{YYYYMMDD-HHmmss}.{ext}`.
- Returns `{ exportPng, exportSvg, isExporting }`.

### 6.2 Frontend -- CanvasToolbar export dropdown

Modify `slices/canvas/components/CanvasToolbar.vue`:

- Replace the current placeholder export area with a dropdown menu containing:
  - "Export as PNG (1x)" -- calls `exportPng(1)`
  - "Export as PNG (2x)" -- calls `exportPng(2)`
  - "Export as SVG" -- calls `exportSvg()`
- Dropdown uses the existing `SDropdown` or a simple popover pattern.
- The export button shows `ArrowDownTrayIcon` from `@heroicons/vue/24/outline`.

### 6.3 Frontend -- CanvasRenderer API exposure

Modify `slices/canvas/components/CanvasRenderer.vue`:

- Expose the `excalidrawApi` ref to the parent via `defineExpose({ excalidrawApi })` or
  a provide/inject pattern, so `useCanvasExport` can access the Excalidraw API for
  `getSceneElements()` and `getAppState()`.

### 6.4 Frontend -- CanvasPanel wiring

Modify `slices/canvas/components/CanvasPanel.vue`:

- Get `excalidrawApi` from `CanvasRenderer` via a template ref.
- Create `useCanvasExport(excalidrawApi)`.
- Pass `exportPng` and `exportSvg` to `CanvasToolbar` as event handlers.
- Disable export when `objects.length === 0`.

### 6.5 Frontend -- i18n

Add keys to `slices/canvas/locales/en.json` and `zh-TW.json`:
- `canvas.export`, `canvas.exportPng`, `canvas.exportPng2x`, `canvas.exportSvg`,
  `canvas.exporting`.

### 6.6 Frontend -- Vite config

No changes needed. `exportToBlob` and `exportToSvg` are part of the `@excalidraw/excalidraw`
package already in the `excalidraw` manual chunk.

## 7. Existing Debt and Patterns

### Patterns to follow

- **Download trigger**: The artifact sandbox blocks `<a download>` for viewers, but
  canvas export runs in the main app (not an artifact), so the standard
  `URL.createObjectURL` + click pattern works. Follow the chat export download pattern
  in `slices/conversation/composables/useChatExport.ts` if one exists, otherwise use
  the standard browser download technique.
- **Toolbar dropdown**: Follow existing dropdown patterns in the UI kit (`SDropdown` or
  a `Popover`-based menu).
- **Composable shape**: Follow the existing canvas composables
  (`useCanvasState.ts`, `useCanvasSplitPane.ts`) for naming and return shape.

### Reuse inventory

| What | Where | Use |
|------|-------|-----|
| `exportToBlob()` | `@excalidraw/excalidraw` | PNG export |
| `exportToSvg()` | `@excalidraw/excalidraw` | SVG export |
| `ArrowDownTrayIcon` | `@heroicons/vue/24/outline` | Export button icon |
| `useCanvasState` | `slices/canvas/composables/` | Canvas metadata (name for filename) |

## 8. Security Considerations

- **SVG export content**: The exported SVG contains the canvas elements as SVG markup.
  Since this is a download (not rendered inline), there is no XSS risk from the export
  itself. The SVG is generated by Excalidraw's own serializer, not from user-supplied
  raw SVG.
- **No server upload**: The export stays entirely client-side. No canvas content is sent
  to the server as part of the export flow.
- **Filename sanitization**: The chatroom name used in the filename is sanitized to
  remove path separators and special characters.

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Large canvas export fails or is slow | Low | Low | Excalidraw handles large scenes; warn user if > 1000 elements |
| Image elements missing from export | Medium | Medium | Excalidraw's export handles embedded images; verify in AC-4 |
| Theme mismatch in export | Low | Low | Pass current `appState.theme` to export functions |

## 10. Test Plan

### Unit tests

- `test_useCanvasExport.ts`: Mock `excalidrawApi`, verify `exportPng` calls
  `exportToBlob` with correct scale, verify `exportSvg` calls `exportToSvg`, verify
  filename generation.

### Manual verification

- Export a canvas with all object types in both light and dark themes. Verify all objects
  appear correctly in both PNG and SVG outputs.
- Export at 1x and 2x, verify dimensions differ by 2x.
- Export an empty canvas -- button should be disabled.
- Export in fullscreen mode -- should work identically.

## 11. Open Questions

None.

## 12. Deviation Log

(Populated during implementation.)

## 13. Follow-ups

- FU-1: PDF export via server-side SVG-to-PDF conversion (WeasyPrint or similar).
- FU-2: Export selected objects only (not the full canvas).
- FU-3: Copy canvas to clipboard as PNG (for pasting into other apps).
