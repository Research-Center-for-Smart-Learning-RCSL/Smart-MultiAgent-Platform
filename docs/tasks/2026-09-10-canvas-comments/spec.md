---
type: feature
status: draft
created: 2026-09-10
requirements: []
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Object Comments and Annotations

## 1. Summary

Allow users to attach comment threads to individual canvas objects. Each object can
have zero or more comments displayed in a popover when the object is selected. Comments
are lightweight text-only threads (no nested replies in this phase) that help teams
discuss specific elements on the canvas without cluttering the chat feed.

## 2. Goals and Non-goals

**Goals**

- Attach comment threads to any canvas object.
- View comments in a popover when selecting a commented object.
- Visual indicator (badge/dot) on objects that have comments.
- Comments include author identity, timestamp, and text.
- Real-time comment updates via existing WebSocket events.

**Non-goals**

- Nested/threaded replies (flat comment list per object in this phase).
- Mention/tagging users in comments.
- Comments on the canvas itself (only on objects).
- Comment resolution/status tracking.

## 3. Acceptance Criteria

- [ ] AC-1: A user can add a comment to any canvas object via right-click or a toolbar
  action.
- [ ] AC-2: Objects with comments show a visual indicator (comment count badge).
- [ ] AC-3: Selecting a commented object displays its comments in a popover.
- [ ] AC-4: Comments appear in real time for other connected users.
- [ ] AC-5: Deleting a canvas object deletes its associated comments (cascade).

## 4. Key Technical Decisions

- New `canvas_comments` table with FK to `canvas_objects` (CASCADE).
- Comments published on the room WS channel as `canvas.comment_created`.
- Consider whether comments should appear in the agent digest.

## 5. Detailed Changes

_To be filled during `/spec` analysis._

## 6. Test Plan

_To be filled during `/spec` analysis._
