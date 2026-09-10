---
type: feature
status: draft
created: 2026-09-10
requirements: []
depends_on: [2026-09-10-collaborative-canvas]
---

# Full-text Search over Canvas Object Text

## 1. Summary

Index the text content of canvas objects (notes, text blocks) so that users can search
for canvas content alongside chat messages. Search results link back to the canvas
with the matching object highlighted or scrolled into view.

## 2. Goals and Non-goals

**Goals**

- Canvas object text is indexed for full-text search.
- Search results include canvas matches alongside chat message matches.
- Clicking a canvas search result opens the canvas panel and highlights/scrolls to the
  matching object.
- Index updates as objects are created, updated, or deleted.

**Non-goals**

- Image content search (OCR or visual similarity).
- Search across canvases in different chatrooms.
- Advanced query syntax specific to canvas content.

## 3. Acceptance Criteria

- [ ] AC-1: Text from canvas notes and text blocks appears in chatroom search results.
- [ ] AC-2: Clicking a canvas search result opens the canvas and scrolls to the object.
- [ ] AC-3: Deleted canvas objects are removed from the search index.
- [ ] AC-4: Search results distinguish canvas matches from chat message matches.

## 4. Key Technical Decisions

- Determine whether to extend the existing chat search infrastructure or build a
  parallel index.
- Carried from parent OQ-3.

## 5. Detailed Changes

_To be filled during `/spec` analysis._

## 6. Test Plan

_To be filled during `/spec` analysis._
