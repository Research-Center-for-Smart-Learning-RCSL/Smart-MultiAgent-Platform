---
type: feature
status: draft
created: 2026-09-10
requirements: []
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Export to PNG/PDF

## 1. Summary

Allow users to export the current canvas state as a PNG image or PDF document. The
export captures all visible objects at their current positions, respects the canvas
viewport or exports the full canvas bounds, and downloads the result to the user's
device.

## 2. Goals and Non-goals

**Goals**

- Export canvas to PNG at configurable resolution (1x, 2x).
- Export canvas to PDF (single page, canvas bounds).
- Export triggered from the canvas toolbar.
- Include all visible object types (notes, text, shapes, drawings, connectors, images).

**Non-goals**

- Server-side rendering (export is client-side via Excalidraw's export utilities).
- Batch export of multiple canvases.
- Export with custom branding or watermarks.
- Export of canvas history/snapshots (only current state).

## 3. Acceptance Criteria

- [ ] AC-1: A user can export the current canvas as a PNG file from the toolbar.
- [ ] AC-2: A user can export the current canvas as a PDF file from the toolbar.
- [ ] AC-3: Exported images include all object types at correct positions and sizes.
- [ ] AC-4: Export respects the current theme (light/dark).

## 4. Key Technical Decisions

- Leverage Excalidraw's built-in `exportToBlob` / `exportToSvg` utilities.
- Client-side only; no backend endpoint needed.

## 5. Detailed Changes

_To be filled during `/spec` analysis._

## 6. Test Plan

_To be filled during `/spec` analysis._
