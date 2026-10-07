---
type: bugfix
status: in-progress
created: 2026-10-07
requirements: [R13.33, R13.55]
depends_on: [2026-10-07-room-roster-completeness]
---

# Show participants' display names on canvas cursors

## 1. Summary

On the room canvas every other editor's cursor is labelled with a name taken from the
editor's own access token. A member's or registered guest's token carries neither a display
name nor an email, so their cursor reads as a 36-character user UUID to everyone else on the
canvas. Anonymous guests are labelled correctly, because the guest token carries the name it
was issued with, but that name is set only when the canvas socket connects, so after a guest
renames itself the cursor keeps the old name until the socket reconnects. This dossier fixes
finding F-3 and the canvas part of F-15 of
`docs/audits/2026-10-07-display-name-resolution/findings.md`.

## 2. Observed vs Expected

- **Observed** — `setLocalAwareness` builds the cursor name as
  `String(claims.display_name ?? claims.email ?? userId).split('@')[0]` from the access token
  (`frontend/src/slices/canvas/composables/useYjsProvider.ts:64-73`). A member access token
  carries neither claim (`backend/shared_kernel/auth/jwt.py:78-90`; no caller passes one
  through `extra`, `backend/contexts/identity/application/auth_service.py:360, 760`,
  `backend/contexts/identity/application/impersonation_service.py:81`), so `name` is the full
  `sub`. The server passes awareness through unchanged (`backend/app/api/ws/canvas.py:254-262`)
  and the renderer shows `user.name` as the collaborator's `username`
  (`frontend/src/slices/canvas/components/CanvasRenderer.vue:165-185`). The guest token does
  carry `display_name` (`jwt.py:205`), but `setLocalAwareness` runs only when the canvas
  socket reports connected (`useYjsProvider.ts:145-150`), so a rename
  (`backend/app/api/v1/guests.py:188-211`) never reaches the cursor of an open canvas. The
  email fallback in the same expression would put an email's local part on a surface other
  room members read if a token ever carried it.
- **Expected** — "Each connected editor's cursor position, selected elements, display name,
  and assigned color are broadcast to all other editors on the same canvas via the Yjs
  awareness protocol" ([R13.55]). The login email is confined to the model context and never
  reaches a surface other room members read ([R13.33]).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does a cursor's name come from? | Both ends. Each client broadcasts its own display name in awareness and re-broadcasts it when the name changes; each viewer labels a cursor with the room roster's name for the cursor's user id, falling back to the broadcast name, then to the truncated id. | Requester's choice. Broadcasting a real name meets [R13.55] as written; preferring the roster on the viewer side makes renames follow the roster and stops a client from labelling itself with someone else's name as long as it reports its own id. Viewer-only resolution was rejected because awareness would keep carrying a non-name; sender-only was rejected because the label would be whatever the sending client claims. |
| Q-2 | How does the canvas slice get names, given it may import no other slice (`frontend/eslint.config.js:35`)? | The room view passes them in: `CanvasPanel` gains props for the viewer's own display name and an id-to-name map built from the room roster, which `ChatroomView.vue` already holds (`userNames`, `guestViewerName`). | Keeps the one-way slice dependency (conversation imports canvas, `eslint.config.js:37`); no canvas code reaches into the conversation slice. |
| Q-3 | Should this depend on `2026-10-07-room-roster-completeness`? | Yes, `depends_on: [2026-10-07-room-roster-completeness]`. | Logical prerequisite: the roster names only people who have posted until that dossier lands, so the viewer-side lookup would miss most canvas editors in a class. Overlap prerequisite too: both edit `ChatroomView.vue`'s roster handling. |

## 4. Reproduction

1. A room with members A ("Alice") and B ("Bob"), both with display names.
2. A and B open the room canvas. Each sees the other's cursor labelled with a 36-character
   UUID.
3. An anonymous guest "Carol" joins and opens the canvas; members see "Carol". Carol renames
   herself to "Caroline" from the participant list; her cursor keeps "Carol" on every other
   editor's screen until her canvas socket reconnects.

## 5. Root Cause Analysis

1. **Root cause:** the sender takes its cursor name from access-token claims
   (`useYjsProvider.ts:65-69`), and the member token has no name claim, so the expression
   falls through to the user id.
2. The receiver trusts the broadcast name as is (`CanvasRenderer.vue:177`) and has no other
   name source.
3. The broadcast is set once per connection (`useYjsProvider.ts:150`) and nothing re-runs it
   when the sender's name changes.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every canvas editor sees every member and registered guest as a UUID;
  every guest rename is stale on open canvases. No data is persisted (awareness is
  ephemeral, [R13.55]).
- **Sibling suspects**:
  - Other readers of name claims from the access token: the room view reads
    `accessTokenClaims.display_name` only for an anonymous guest's own name
    (`frontend/src/slices/conversation/views/ChatroomView.vue:713-718`), where the claim
    exists — cleared.
  - Canvas comment authors (`CanvasCommentPopover.vue:146`): the component is not mounted
    (audit, refuted candidate; FU-8 there) — cleared for now.
  - Canvas history and template authorship: `CanvasHistory.vue` and the template picker show
    labels and timestamps, not author names (`canvas/components`) — cleared.

## 7. Fix Design

1. **Sender broadcasts a real name.** `useYjsProvider` takes the viewer's display name as a
   reactive input instead of reading token claims: `name` is that display name, or the
   eight-character truncated id when the account has none, and never the email. A `watch`
   on the input re-runs `setLocalAwareness` when the name changes, so a rename reaches every
   other editor through the next awareness update.
2. **Viewer prefers the roster.** `CanvasRenderer` takes an id-to-name map and labels each
   collaborator with `names[user.userId] ?? user.name ?? userId.slice(0, 8)`; it re-renders
   collaborators when the map changes as well as on awareness changes.
3. **The room view supplies both.** `ChatroomView.vue` passes its existing `userNames` map
   (which, with `2026-10-07-room-roster-completeness`, names every present participant) and
   the viewer's own name (`session.me.display_name` for an account, `guestViewerName` for an
   anonymous guest) to `LazyCanvasPanel`, and `CanvasPanel` forwards them.

The fix replaces the name source rather than adding claims to the member access token:
putting the display name in the token would make renames wait for a refresh and grow every
token for one consumer.

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- `frontend/src/slices/canvas/__tests__/useYjsProvider.test.ts` (or the provider's existing
  test): with a member token (no name claims) and a display name input of "Alice", the local
  awareness `user.name` is "Alice"; it fails today because the name is the token's `sub`.
  Changing the input to "Alicia" updates the local awareness state; it fails today because
  awareness is set only on connect. With no display name, `user.name` is the eight-character
  id and never contains an email.
- `frontend/src/slices/canvas/__tests__/CanvasRenderer.test.ts` (or equivalent): a remote
  awareness state with `userId: "u-1"` and `name: "spoof"` is rendered with the map's name
  for `u-1`; it fails today because the broadcast name is used as is. Updating the map
  re-renders the label without an awareness change.

## 9. Risks and Rollback

- The canvas panel gains two props; any other mount of `CanvasPanel` must pass them, which
  the type check enforces.
- A client that lies about its `userId` in awareness can still borrow another participant's
  roster name; awareness is client-asserted and unverified by the server (FU-1).
- Rollback: revert; no schema, API or data change.

## 10. Acceptance Criteria

- [ ] AC-1: the regression tests in §8 fail before the fix and pass after.
- [ ] AC-2: a member or registered guest with a display name appears on other editors'
  canvases under that name, never as a UUID.
- [ ] AC-3: an account without a display name appears as its eight-character id, and no
  cursor label ever contains an email or a part of one.
- [ ] AC-4: after a participant renames themselves (guest rename, or member rename with
  `2026-10-07-room-roster-completeness` in place), other editors' cursor labels show the new
  name without the canvas reconnecting.
- [ ] AC-5: frontend lint (including the slice boundary rules), typecheck, tests and build
  pass in CI.

## 11. SRS Delta

None. [R13.55] already requires the display name in awareness.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** Awareness frames are relayed without checking that a state's `userId` matches
  the sending connection's principal (`backend/app/api/ws/canvas.py:254-262`), so a client
  can impersonate another participant's cursor. Route to `check-security`.
