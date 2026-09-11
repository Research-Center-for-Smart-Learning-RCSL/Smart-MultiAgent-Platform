---
type: feature
status: implemented
created: 2026-09-10
requirements: [R13.42, R13.44, R13.46]
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Object Comments

## 1. Summary

Allow users to attach comment threads to individual canvas objects. Each object can have
zero or more comments displayed in a popover when the object is selected. Comments are
flat text threads (no nested replies in this phase) that help teams discuss specific
canvas elements without cluttering the chat feed. A visual badge on commented objects
indicates comment presence and count. Comments are delivered in real time via WebSocket
events on the room channel. Guest users may post comments, subject to the existing canvas
rate limit ([R13.46]).

Comments are not included in the agent digest. Adding agent visibility for comments is a
separate concern to be evaluated independently.

## 2. Goals and Non-goals

**Goals**

- Attach comment threads to any canvas object (note, text, shape, image, drawing,
  connector).
- Comment count badge on objects that have comments, visible in the canvas editor.
- Popover panel showing all comments for the selected object, with author identity,
  relative timestamp, and text.
- Create, edit, and delete comments with appropriate access control.
- Real-time comment delivery via WebSocket events on the room channel.
- Cascade delete: deleting a canvas object deletes its associated comments.
- Guest users can comment, rate-limited at 60 ops/min ([R13.46]).

**Non-goals**

- Nested/threaded replies (flat comment list per object in this phase).
- Mention/tagging users in comments (@-mentions).
- Comments on the canvas itself (only on individual objects).
- Comment resolution or status tracking (open/resolved).
- Rich-text or markdown formatting in comments (plain text only).
- Comments visible to AI agents (not included in the canvas digest).

## 3. Clarifications

- **Q-1**: Flat or threaded comments? **Flat.** Each comment is a top-level entry on an
  object. Threaded replies are a follow-up if demand warrants it.

- **Q-2**: Should comments appear in the agent digest? **No.** Comments are discussion
  meta-content, not canvas spatial content. Including them would inflate the digest and
  blur the distinction between canvas state and conversation about it.

- **Q-3**: Comment editing? **Yes, by the author only.** An edit replaces the comment
  text and updates `updated_at`. No edit history is stored (lightweight design).

## 4. Requirements

### 4.1 Existing requirements touched

None. Comments are a new capability that extends the canvas without modifying existing
requirements.

### 4.2 New requirements (SRS Delta)

- **[R13.67]** A canvas object may have zero or more comments. Each comment carries
  plain text content (max 2000 characters), an author (user or guest), and a timestamp.
  Deleting a canvas object cascade-deletes all its comments.

- **[R13.68]** Any principal who can send messages in the chatroom can create, edit (own
  only), and delete (own only) canvas object comments. Room creators and org admins may
  delete any comment. Guest comment mutations are rate-limited per [R13.46].

- **[R13.69]** Comment mutations (create, edit, delete) publish events on the chatroom's
  room WebSocket channel (`canvas.comment_created`, `canvas.comment_updated`,
  `canvas.comment_deleted`). Connected clients update the comment list in real time.

- **[R13.70]** Comment text is sanitized server-side before storage: control characters
  stripped, content capped at 2000 characters, validated at the Pydantic model boundary.
  No HTML or markdown rendering; comments display as plain text.

## 5. Acceptance Criteria

- [ ] AC-1: A user can add a comment to any canvas object via a "Comment" action
  (context menu or selection toolbar). Verified visually. (Not executed: needs running
  stack with browser.)
- [ ] AC-2: Objects with comments show a comment count badge overlay. Verified visually.
  (Not executed: needs running stack with browser.)
- [ ] AC-3: Selecting a commented object and opening comments displays all comments in a
  popover, ordered by creation time (oldest first). (Not executed: needs running stack.)
- [ ] AC-4: Comments appear in real time for other connected users (WS event). Verified
  by a two-client test. (Not executed: needs running stack with two browser sessions.)
- [x] AC-5: Deleting a canvas object deletes its comments (cascade). FK ON DELETE CASCADE
  on `canvas_objects.id`; verified by schema design.
- [x] AC-6: A user can edit their own comment (text replacement). The updated text and
  `updated_at` are reflected for all clients. Verified by unit test and code inspection.
- [x] AC-7: A user can delete their own comment. Room creator/admin can delete any
  comment. Verified by code: `_is_comment_author` + `is_room_creator` check.
- [x] AC-8: Guest users can create comments, subject to the 60 ops/min rate limit.
  Exceeding the limit returns 429. Reuses existing `_enforce_guest_rate_limit`.
- [x] AC-9: Comment text is capped at 2000 characters. Longer input is rejected at the
  API boundary with 400. Pydantic `Field(max_length=2000)` on `CommentIn`.
- [x] AC-10: `GET /canvas/objects/{object_id}/comments` returns all comments for the
  object, paginated. Implemented with `limit`/`offset` params.
- [x] AC-11: Comments are NOT included in the agent canvas digest. `build_canvas_digest`
  only reads `CanvasObject`; comments are a separate table with no digest integration.

## 6. Detailed Changes

### 6.1 Backend -- Migration

New Alembic migration adding `canvas_object_comments` table:

```
canvas_object_comments:
  id              UUID PK (gen_random_uuid)
  canvas_id       UUID FK -> canvases(id) CASCADE
  object_id       UUID FK -> canvas_objects(id) CASCADE
  content         TEXT NOT NULL
  created_by_user_id    UUID FK -> users(id) SET NULL, nullable
  created_by_guest_id   UUID FK -> guest_sessions(id) SET NULL, nullable
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  deleted_at      TIMESTAMPTZ nullable (soft delete)
```

Index on `(object_id, deleted_at)` for efficient listing.

### 6.2 Backend -- Domain model

`contexts/canvas/domain/models.py`: add `CanvasComment` dataclass:

```python
@dataclass(frozen=True)
class CanvasComment:
    id: uuid.UUID
    canvas_id: uuid.UUID
    object_id: uuid.UUID
    content: str
    created_by_user_id: uuid.UUID | None
    created_by_guest_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
```

### 6.3 Backend -- Table definition

`contexts/canvas/infrastructure/tables.py`: add `canvas_object_comments` SA Core table
definition with columns matching the migration.

### 6.4 Backend -- Repository

`contexts/canvas/infrastructure/repositories/canvas_repo.py` (or a dedicated
`canvas_comment_repo.py`): add comment CRUD methods:

- `list_comments(object_id, canvas_id, limit, offset) -> Sequence[CanvasComment]`
- `create_comment(values) -> CanvasComment`
- `update_comment(comment_id, canvas_id, content) -> CanvasComment | None`
- `delete_comment(comment_id, canvas_id) -> bool` (soft delete)
- `count_comments_by_object(object_ids) -> dict[uuid.UUID, int]` (for badge counts,
  batched)

### 6.5 Backend -- Service

`contexts/canvas/application/canvas_service.py` or a new `comment_service.py`: add
comment operations with access control, audit emission, and WS publishing.

- `create_comment(...)`: validate ownership check (can send in room), sanitize text,
  create, publish `canvas.comment_created`.
- `update_comment(...)`: verify author or admin, update text, publish
  `canvas.comment_updated`.
- `delete_comment(...)`: verify author or admin/creator, soft-delete, publish
  `canvas.comment_deleted`.

### 6.6 Backend -- API endpoints

`backend/app/api/v1/canvas.py` (add to existing router):

- `GET /canvas/objects/{object_id}/comments` -- list comments, paginated. Requires
  `ensure_can_read`.
- `POST /canvas/objects/{object_id}/comments` -- create comment. Requires
  `ensure_can_send`. Body: `{ content: string }`.
- `PATCH /canvas/comments/{comment_id}` -- edit comment. Requires author or admin.
  Body: `{ content: string }`.
- `DELETE /canvas/comments/{comment_id}` -- soft-delete comment. Requires author or
  admin/creator.
- `GET /canvas/objects/comment-counts` -- batch fetch comment counts for all objects on
  the canvas. Returns `{ object_id: count }` map. Used by the frontend for badge
  rendering without N+1 requests.

Request/response models:
- `CommentIn(BaseModel)`: `content: str` (max 2000 chars, validated by Pydantic
  `max_length`).
- `CommentOut(BaseModel)`: `id`, `object_id`, `content`, `created_by_user_id`,
  `created_by_guest_id`, `created_at`, `updated_at`.

### 6.7 Frontend -- Comment popover component

New file: `slices/canvas/components/CanvasCommentPopover.vue`

- Renders as a floating panel positioned near the selected object.
- Shows existing comments (scrollable list, oldest first).
- Input field at the bottom for new comments.
- Edit/delete actions on own comments (inline edit, confirm delete via
  `SConfirmDialog`).
- Triggered when user selects a commented object and clicks "Comments" or uses a
  keyboard shortcut.

### 6.8 Frontend -- Comment badge overlay

Modify `slices/canvas/components/CanvasRenderer.vue`:

- Fetch comment counts via `GET /canvas/objects/comment-counts`.
- Render a small badge (comment count) on each Excalidraw element that has comments.
  Implementation depends on Excalidraw's custom rendering hooks (may require an overlay
  div positioned via element coordinates).

### 6.9 Frontend -- API and types

- Add comment API functions to `slices/canvas/api/index.ts`.
- Add `CanvasComment` type to `slices/canvas/types/index.ts`.
- Add `canvas.comment_created/updated/deleted` WS event handlers in
  `useCanvasSocket.ts`.

### 6.10 Frontend -- i18n

Add comment-related keys to `slices/canvas/locales/en.json` and `zh-TW.json`:
`canvas.comments`, `canvas.addComment`, `canvas.editComment`, `canvas.deleteComment`,
`canvas.noComments`, `canvas.commentPlaceholder`.

## 7. Existing Debt and Patterns

### Patterns to follow

- Comment CRUD: follow the canvas object CRUD pattern in `canvas_service.py` and
  `canvas.py` (same auth, audit, WS emission).
- Soft delete: follow `deleted_at` pattern used elsewhere in the codebase.
- Guest rate limiting: reuse `_enforce_guest_rate_limit` from `canvas.py:166`.
- Popover UI: follow existing popover/dropdown patterns in `shared/ui/`.

### Reuse inventory

| What | Where | Use |
|------|-------|-----|
| `_enforce_guest_rate_limit` | `canvas.py:166` | Guest comment rate limit |
| `Publisher` | `shared_kernel/realtime/pubsub.py:44` | WS event emission |
| `room_channel` | `conversation/infrastructure/channels.py:12` | WS channel |
| `ensure_can_read/send` | `conversation/application/access.py` | ACL |
| `SConfirmDialog` | `shared/ui/SConfirmDialog.vue` | Delete confirmation |
| `audit.emit` | `shared_kernel/audit/__init__.py` | Audit trail |

## 8. Security Considerations

- Comment content is user-generated text: strip control characters, enforce 2000-char max
  at Pydantic boundary. No HTML rendering; display as plain text (inherently XSS-safe).
- Edit/delete authorization: author-only for own comments, admin/creator for any. Checked
  server-side, never client-only.
- Object ID scoping: comments are scoped to the canvas via `canvas_id` FK. Cross-canvas
  object IDs return 404.
- Cascade delete: FK `ON DELETE CASCADE` ensures no orphaned comments.
- Guest rate limiting: same 60 ops/min as other canvas mutations.

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Comment badge rendering on Excalidraw elements | Medium | Medium | May need overlay div approach if Excalidraw lacks custom rendering hooks; investigate during implementation |
| High comment volume on popular objects | Low | Low | Pagination on the API; frontend loads first 50, "load more" for the rest |

## 10. Test Plan

### Unit tests

- `test_comment_crud.py`: create, edit (own only), delete (own + admin), list, sanitize.
- `test_comment_cascade.py`: deleting object deletes comments.
- `test_comment_access.py`: non-author edit/delete rejected, guest rate limiting.
- `test_comment_counts.py`: batch count endpoint returns correct per-object counts.

### Integration tests (pytest.mark.db)

- `test_comment_persistence.py`: full CRUD cycle against PostgreSQL.
- `test_comment_cascade_db.py`: FK cascade verification.

### E2E tests (Playwright)

- Add comment to an object, verify badge appears.
- Second user sees the comment in real time.
- Edit and delete own comment.

## 11. Open Questions

None.

## 12. Deviation Log

- D-1: Comment badge is rendered as a per-object button list below the Excalidraw canvas
  rather than as an overlay positioned on element coordinates. Excalidraw does not expose
  element screen coordinates or custom rendering hooks in a way that allows reliable overlay
  positioning, especially across pan/zoom. The button list approach is simpler, works at all
  zoom levels, and will be replaced by an Excalidraw-native solution when CRDT sync (Phase 2)
  introduces a custom rendering layer.

- D-2: AC-1 through AC-4 are unticked. All four require a running stack with browser
  sessions. The comment UI (popover, badge, WS invalidation) is implemented and passes
  lint/typecheck/build, but no visual verification has been performed. Docker was not
  available during this build session.

- D-3: Self-audit found that the original `update_comment` and `delete_comment` route
  handlers compared `created_by_user_id` to the principal's user ID, which always fails for
  guest-authored comments (guests set `created_by_guest_id`). Fixed by introducing
  `_is_comment_author()` that checks the correct field based on `principal.is_guest`.
  Same fix applied to the frontend `isOwnComment` check.

## 13. Follow-ups

- FU-1: Nested/threaded replies on comments.
- FU-2: @-mention users in comments with notification.
- FU-3: Comment resolution/status tracking (open/resolved).
- FU-4: Include comment counts or summaries in agent digest (opt-in).
- FU-5: Hardcoded English relative timestamps in CanvasCommentPopover (`formatTime`).
  Should use i18n keys for "just now", "m ago", "h ago", "d ago".
- FU-6: Comment badge overlay positioned on Excalidraw element coordinates instead of a
  button list. Depends on Excalidraw custom rendering API or the Phase 2 CRDT layer.
