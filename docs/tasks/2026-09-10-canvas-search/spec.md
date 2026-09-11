---
type: feature
status: implemented
created: 2026-09-10
requirements: [R13.42, R13.43]
depends_on: [2026-09-10-collaborative-canvas]
---

# Full-text Search over Canvas Object Text

## 1. Summary

Add PostgreSQL full-text search indexing to canvas objects so users can find notes and
text blocks by content. A new `GET /api/chatrooms/{chatroom_id}/canvas/search?q=...`
endpoint returns matching canvas objects with relevance ranking and highlighted snippets,
independent of the existing chatroom message search. Clicking a result in the frontend
opens the canvas panel and scrolls/highlights the matching object. The index updates
automatically via a database trigger as objects are created, updated, or deleted.

## 2. Goals and Non-goals

**Goals**

- Canvas notes and text blocks are indexed for PostgreSQL full-text search (tsvector +
  GIN index).
- Dedicated search endpoint returning ranked results with `ts_headline` snippets.
- Automatic index updates via trigger on INSERT/UPDATE/DELETE of `canvas_objects`.
- Frontend search input in the canvas toolbar with result highlighting.
- Search respects chatroom access control (same ACL as canvas read).

**Non-goals**

- Image content search (OCR or visual similarity).
- Search across canvases in different chatrooms.
- Advanced query syntax (FTS operators exposed to users).
- Unified search mixing canvas and message results (separate endpoints; UI may combine
  later).
- Indexing shape/drawing/connector objects (no text content).

## 3. Clarifications

- **Q-1**: Unified vs separate search endpoint? **Separate.** New
  `GET /api/chatrooms/{chatroom_id}/canvas/search?q=...` endpoint. Results are canvas
  objects only. Keeps canvas and message search independent, each with their own ranking
  and snippet format. A unified UI layer may combine results in a future follow-up.

- **Q-2**: Which objects are indexed? **Only objects with non-null `content`**: `note` and
  `text` kinds. The `content_tsv` tsvector column populates only when
  `content IS NOT NULL`.

- **Q-3**: Language config? **`english`**, matching the existing message search at
  `message_repo.py:283`. Multilingual search is out of scope (same limitation as message
  search).

## 4. Requirements

### 4.1 New requirements (SRS Delta)

- **[R13.62]** Canvas objects with non-null text content are indexed for full-text search
  via a PostgreSQL tsvector column (`content_tsv`) maintained by a trigger. The index
  uses the `english` text search configuration. Only `note` and `text` object kinds
  produce index entries; other kinds have a null tsvector.

- **[R13.63]** Canvas search is scoped to a single chatroom's canvas and respects the
  same access control as canvas read ([R13.44]). Results are ranked by `ts_rank_cd` and
  include `ts_headline` snippets. The endpoint returns at most 50 results per query.

## 5. Acceptance Criteria

- [x] AC-1: A search query against a canvas returns matching notes and text blocks,
  ranked by relevance. Verified by integration test (`test_trigger_updates_tsv_on_content_change`,
  `test_regconfig_cast_executes`) and unit test (`TestCanvasSearchOrdering`).
- [x] AC-2: Search results include highlighted snippets (`ts_headline`). Verified by
  integration test (`test_search_returns_snippet_with_mark_tags`) and unit test
  (`TestCanvasSearchSnippet`).
- [x] AC-3: Creating or updating a canvas object with text content automatically updates
  the search index. Verified by integration test (`test_trigger_populates_content_tsv_on_insert`,
  `test_trigger_updates_tsv_on_content_change`).
- [x] AC-4: Deleting a canvas object removes it from search results. Verified by
  integration test (`test_deleted_object_not_in_search`).
- [x] AC-5: Search respects chatroom access -- a user without room access gets 403.
  Verified by endpoint structure: `resolve_room_access + ensure_can_read` applied identically
  to all other canvas read endpoints. Not executed against a running stack (same limitation
  as other canvas dossiers).
- [x] AC-6: The frontend canvas toolbar has a search input. Typing a query shows results
  in a dropdown/panel. Not executed against a running stack (same limitation as other canvas
  dossiers).
- [x] AC-7: Clicking a search result scrolls/highlights the matching object on the
  canvas. Not executed against a running stack (same limitation as other canvas dossiers).
  See D-1 for the scroll/select mechanism's known limitation.
- [x] AC-8: Objects without text content (image, shape, drawing, connector) do not
  appear in search results even if they have metadata. Verified by integration test
  (`test_search_excludes_non_text_objects`).

## 6. Detailed Changes

### 6.1 Backend -- Migration

New Alembic migration:

1. Add `content_tsv` column (`TSVECTOR`, nullable) to `canvas_objects` table.
2. Create GIN index: `CREATE INDEX ix_canvas_objects_content_tsv ON canvas_objects
   USING GIN (content_tsv)`.
3. Create trigger function and trigger:
   ```sql
   CREATE FUNCTION canvas_objects_tsv_trigger() RETURNS trigger AS $$
   BEGIN
     IF NEW.content IS NOT NULL THEN
       NEW.content_tsv := to_tsvector('english', NEW.content);
     ELSE
       NEW.content_tsv := NULL;
     END IF;
     RETURN NEW;
   END
   $$ LANGUAGE plpgsql;

   CREATE TRIGGER canvas_objects_tsv_update
     BEFORE INSERT OR UPDATE OF content ON canvas_objects
     FOR EACH ROW EXECUTE FUNCTION canvas_objects_tsv_trigger();
   ```
4. Backfill existing rows:
   `UPDATE canvas_objects SET content_tsv = to_tsvector('english', content) WHERE content IS NOT NULL`.

### 6.2 Backend -- Table definition

Add to `contexts/canvas/infrastructure/tables.py`:

```python
sa.Column("content_tsv", TSVECTOR, nullable=True),
```

### 6.3 Backend -- Repository

Add to `CanvasRepository`:

- `search(canvas_id, query, limit=50) -> list[tuple[CanvasObject, float, str]]`:
  follows `MessageRepository.search()` at `message_repo.py:243-302` exactly:
  - `plainto_tsquery('english', query)` for safe query parsing.
  - `ts_rank_cd(content_tsv, tsq)` for ranking.
  - `ts_headline('english', content, tsq)` for snippets.
  - WHERE: `canvas_id = :canvas_id AND content_tsv @@ tsq`.
  - ORDER BY rank DESC, created_at DESC.
  - LIMIT 50.

### 6.4 Backend -- Service

Add to `CanvasService`:

- `search_objects(canvas_id, query, limit=50)` -- delegates to repository, returns
  `list[SearchResult]` with `object`, `rank`, `snippet`.

### 6.5 Backend -- Facade

Add to `CanvasFacade`:

- `search_objects(canvas_id, query, limit=50)` -- pass-through.

### 6.6 Backend -- API endpoint

Add to `app/api/v1/canvas.py`:

- `GET /api/chatrooms/{chatroom_id}/canvas/search?q=...&limit=50`:
  - `resolve_room_access` + `ensure_can_read` (same ACL as other canvas reads).
  - Get canvas via `get_by_chatroom` (return empty results if no canvas).
  - Call `facade.search_objects(canvas.id, q, limit)`.
  - Return `list[CanvasSearchResult]` Pydantic model: `object_id`, `kind`, `content`,
    `snippet`, `rank`, `position_x`, `position_y`.

### 6.7 Frontend -- Search composable

New: `slices/canvas/composables/useCanvasSearch.ts`

- TanStack Query wrapping the search endpoint with debounced input (300ms).
- Returns `{ query, results, isSearching }`.

### 6.8 Frontend -- Search UI

Modify `slices/canvas/components/CanvasToolbar.vue`:

- Add search input (MagnifyingGlassIcon from @heroicons/vue).
- Expandable search bar on click.
- Results dropdown showing matching objects with snippets.

New: `slices/canvas/components/CanvasSearchResults.vue`

- List of search result cards showing object kind icon, snippet with highlights, and
  position indicator.
- Click handler emits `select-object` event with object_id and position.

Modify `slices/canvas/components/CanvasPanel.vue`:

- Handle `select-object` event: scroll Excalidraw viewport to the object's position and
  highlight/select it via `excalidrawAPI.scrollToContent()` or
  `excalidrawAPI.updateScene({ appState: { selectedElementIds: ... } })`.

## 7. Existing Debt and Patterns

### Patterns to follow

- **FTS implementation**: `MessageRepository.search()` at `message_repo.py:243-302` is
  the canonical pattern. Use `plainto_tsquery`, `ts_rank_cd`, `ts_headline` with the
  `english` config. Cast the config literal explicitly (`sa.cast(sa.literal("english"),
  REGCONFIG)`) per the backend CLAUDE.md warning about `plainto_tsquery(varchar,
  varchar)` overload.
- **Search API**: `app/api/v1/search.py:38-69` for endpoint structure and response model.
- **Trigger migration**: Follow the `content_tsv` trigger pattern from migration 0017.

### Reuse inventory

| What | Where | Use |
|------|-------|-----|
| `MessageRepository.search()` | `message_repo.py:243-302` | FTS query pattern |
| `plainto_tsquery` + `ts_rank_cd` | same | Ranking and safe query parsing |
| `REGCONFIG` type cast | backend CLAUDE.md note | Prevent varchar overload error |
| `resolve_room_access` | `conversation/application/access.py` | ACL check |
| `CanvasRepository` | `canvas_repo.py:64` | Extend with search method |
| `MagnifyingGlassIcon` | `@heroicons/vue/24/outline` | Search input icon |

## 8. Security Considerations

- Search query input is sanitized by `plainto_tsquery` (no raw SQL injection via FTS
  operators).
- Access control enforced at the API layer (same `resolve_room_access` as other canvas
  endpoints).
- Search results include only `content` text and position -- no minio_path or style data
  in the search response to minimize information leakage.

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| GIN index bloat on large canvases | Low | Low | Only text objects indexed; max 500 objects per canvas |
| Trigger overhead on batch_operate | Low | Low | Trigger fires per-row; batch_operate is already sequential |

## 10. Test Plan

### Unit tests

- Search returns matching objects ranked by relevance.
- Search returns empty for non-matching queries.
- Search excludes objects with null content (image, shape, drawing, connector).
- Search respects canvas_id scope (no cross-canvas leakage).

### Integration tests (pytest.mark.db)

- Insert objects with text, verify tsvector trigger populates content_tsv.
- Update object content, verify index updates.
- Delete object, verify it no longer appears in search.
- `plainto_tsquery` with the `REGCONFIG` cast executes without error (the failure mode
  `backend/CLAUDE.md` warns about).

### E2E tests

- Type a search query in the canvas toolbar, verify results appear.
- Click a search result, verify the canvas scrolls to the object.

## 11. Open Questions

None.

## 12. Deviation Log

- D-1: The spec proposed `excalidrawAPI.scrollToContent()` or `selectedElementIds` for
  scroll-to-object. The implementation uses `updateScene` with computed `scrollX/scrollY`
  from the object's `position_x/position_y` and sets `selectedElementIds`. The
  `scrollToContent` call was removed as redundant (it scrolls to all elements, not the
  target). The `selectedElementIds` mapping depends on Excalidraw element IDs matching
  canvas object UUIDs, which may not hold with CRDT-based sync; the position-based scroll
  works regardless. Full visual verification requires a running stack. Confirmed by
  code review: Excalidraw uses nanoid-style IDs stored in Yjs, not backend UUIDs, so
  `selectedElementIds` is a no-op. The position-based scroll is the working fallback.
- D-2: `content` field removed from `CanvasSearchResult` response model. The spec
  listed it but the frontend only renders the snippet; shipping the full object content
  wastes bandwidth on potentially large text payloads. Found by code review.

## 13. Follow-ups

- FU-1: Unified search UI combining message and canvas results.
- FU-2: Multilingual search config (beyond `english`).
- FU-3: Search highlighting overlay on the Excalidraw canvas (beyond scroll-to).
- FU-4: Map backend canvas object UUIDs to Excalidraw element IDs for accurate
  selection highlighting on search result click (D-1).
