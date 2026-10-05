---
type: bugfix
status: approved
created: 2026-10-05
requirements: [R5.04, R6.11, R6.12, R13.06, R13.06a, R13.06b, R13.07, R24.43]
depends_on: [2026-10-05-guest-room-read-and-identity]
---

# A guest session does not survive a reload, ends silently, leaks into a later sign-in, and breaks a signed-in user

## 1. Summary

An anonymous guest's session lives only in page memory, and the client treats every way it can end
as the same event or as no event at all. A reload sends the guest to a login page they cannot use
although their 7-day refresh cookie is valid. A background refresh that fails on a network blip
throws the session away and shows nothing. A guest who later signs in keeps routing refreshes and
socket tickets to the guest endpoints, and a signed-in user who enters a room as a guest leaves the
whole app shell running on a guest token. On the landing page, a name of invisible characters is
reported as a dead link, Retry does nothing after a failed resume, and a guest cannot tell "the
owner turned guest access off" from "your session expired". This dossier fixes findings F-7, F-8,
F-9, F-10, F-18, F-19, F-20 and F-22 of `docs/audits/2026-10-05-guest-anonymous-session/findings.md`.

## 2. Observed vs Expected

Citations are against `main` at `c5114a13`; the audit's line numbers have drifted by up to 15 lines
in `axios.ts`, `guests.py` and `ChatroomView.vue` since it was written, with no behavioral change.

- **Observed**
  - **F-7 (reload).** The access token and the guest context are module refs, memory only
    (`frontend/src/shared/transport/axios.ts:28,31`); `setGuestContext` is called only from the
    landing page (`GuestLandingView.vue:101`). Boot gates the router on `session.hydrate()`
    (`frontend/src/app/main.ts:74-80`), which knows only the user refresh endpoint
    (`frontend/src/slices/identity/stores/session.ts:61-78`). It fails, `clear()` runs, and the guard
    sends the guest to login (`frontend/src/app/guards.ts:28-30`). The guest refresh cookie is
    path-scoped to `/api/guest/{chatroom_id}` (`backend/app/api/v1/guests.py:123`), so nothing on the
    user path ever presents it.
  - **F-8 (silent strand).** `attemptRefresh` nulls the token on any failure, network errors included
    (`axios.ts:277-297`, null at `:290`); `refreshAccessToken`, which the socket's proactive refresh
    uses (`ws-manager.ts:385-421`), never calls `onUnauthorized` (`axios.ts:305-308`). The room's close
    handler returns early once `isGuestSession` is false (`ChatroomView.vue:1037-1041`), and
    `isGuestSession` derives from the now-null token (`axios.ts:81-84`), so `markExpired` never runs.
    The next ticket request carries no bearer and is not refresh-eligible (`axios.ts:204-208,319-323`),
    so the socket reconnect-loops (`ws-manager.ts:234-237`); the next tab focus runs `hydrate()`, which
    no longer sees a guest token and calls `clear()` (`main.ts:83-85`, `session.ts:70-77`).
  - **F-9 (context survives sign-in).** `clearGuestContext` has one caller (`frontend/src/app/router.ts:93`);
    `session.login`, `applyTokens` and `clear` never call it (`session.ts:19-31,51-59`). Refresh and
    socket tickets route to the guest endpoints whenever the context is set (`axios.ts:282-285,320`).
    A guest who reaches `/login` and signs in gets 404 on every socket ticket
    (`guests.py:240-241`), and at the first user-token expiry the guest cookie silently replaces the
    user's session.
  - **F-10 (signed-in user enters as guest).** `chooseGuest` only shows the form
    (`GuestLandingView.vue:165-168`); `enterChatroom` then calls `setAccessToken(guestJWT)` while
    `session.me` stays set (`:94-111`). `isAuthenticated` is `me !== null` (`session.ts:16`), so the
    full shell remains while every request carries the guest token, and `hydrate` is skipped while a
    guest token is held (`session.ts:70`).
  - **F-18 (invisible name).** The form trims with JavaScript `trim()`, which keeps U+200B
    (`GuestLandingView.vue:68-72,283`); the backend accepts it at the Pydantic layer (`guests.py:70-72`),
    `normalise_label` reduces it to `None` (`backend/shared_kernel/labels.py:49-62`), and
    `create_or_resume` raises `GuestTokenInvalid` (`guest_session_service.py:106-108`), mapped to 404
    (`backend/contexts/conversation/interfaces/error_mapping.py:59-63`). The landing page classifies
    404 as `invalid` (`GuestLandingView.vue:87-92`), a dead end with no action (`:312-324`).
  - **F-19 (Retry).** The transient-error Retry calls `doEnroll` (`GuestLandingView.vue:341`), a form
    submit; `doResume` (`:140-158`) and `chooseOwnAccount` (`:170-177`) never set the name field, so
    validation fails invisibly and nothing happens.
  - **F-20 (disabled vs expired).** Guest refresh with links off raises `GuestTokenInvalid`
    (`guest_session_service.py:209-210`), byte-identical to an expired or rotated cookie (`:213-215`).
    The guest ticket route checks neither room access nor the links flag (`guests.py:234-251`). A
    socket refused before accept reaches the browser as 1006, not 4403
    (`backend/app/api/ws/chatroom.py:77-79`, `backend/shared_kernel/realtime/connection.py:220-224`).
    `onUnauthorized` maps every guest failure to `markExpired` and navigates away to the rejoin URL
    (`router.ts:86-97`), so the in-room banners (`ChatroomView.vue:205-229`) are not shown on the
    HTTP path either.
  - **F-22 (case).** The refresh URL and the localStorage hint key use the route parameter verbatim
    (`GuestLandingView.vue:22,60-66,101`, `axios.ts:284`); the cookie path is the canonical lower-case
    UUID (`guests.py:123,164`), and cookie path matching is case-sensitive.
- **Expected**
  - [R13.06b]: "Silent refresh extends the session indefinitely while the cookie survives. On full
    expiry, the guest must re-enter via the guest link." Guest dossier AC-16 (silent refresh) and the
    §6 Phase 2 `axios.ts` bullet ("or if no JWT exists but a guest refresh cookie might",
    `docs/tasks/2026-09-04-guest-anonymous-session/spec.md:409-416`).
  - Guest dossier AC-20: an inline "Session expired" banner with a way back when the session cannot
    be refreshed; AC-22: "Guest access has been disabled" when the owner turns links off.
  - [R6.11] and guest dossier Q-4 and AC-23: a signed-in visitor is offered anonymous entry or entry
    with their account; D-6 names the token-overwrite breakage and says Phase 3 would replace the
    guard that prevented it.
  - Guest dossier §6 Phase 2 states: a validation error is not an invalid link; the template's
    transient state is retryable.
  - [R24.43]: the guest link token is never written to persistent client storage.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How does a reload or a new tab recover a live guest session? | Boot tries the user refresh first. If it fails, the URL is a room (`/chatrooms/:id` or `/c/:id`), and this browser holds the guest hint for that room (`localStorage['smap:guest:'+id]`), boot calls that room's guest refresh and, on success, restores the guest token and context before the router installs. | The account always wins, which is the safe precedence on a shared device. The hint already exists for resume (`GuestLandingView.vue:48-66`) and is the only durable sign that this browser was a guest there; the cookie itself is httpOnly and path-scoped, so a request is the only way to learn whether it is valid. Chosen over guest-first (a teacher who once tested as a guest would reload into the guest for up to 7 days) and over trying every room URL (an extra request for every unauthenticated visitor). |
| Q-2 | After a reload the link token is gone. What does "rejoin" do on full expiry? | Not persisted. Within a page lifetime the expired banner keeps its Rejoin button; after a reload it says to reopen the link the teacher shared, with no button. | [R24.43] forbids persisting the link token; sessionStorage would still be persistence and would need an SRS change. [R13.06b] already says the guest must re-enter via the link. |
| Q-3 | What happens to a signed-in user who chooses "Enter as Guest"? | A confirm dialog, then the tab's user state is cleared locally (`session.clear()`, no server logout) before the guest session is created. Reloading or signing in returns to the account. | Lets a teacher test the student path without being signed out elsewhere; the account's refresh cookie survives, and Q-1's account-first boot brings it back on reload. Chosen over a server logout (signs out every tab) and over removing the option (contradicts [R6.11]). |
| Q-4 | How does a guest learn "access disabled" rather than "expired"? | New problem type `conversation/guest-access-disabled` (403) from guest session create/resume, guest refresh and the guest ticket route when the room's guest links are off; the ticket route also gains a room check. The client shows the disabled banner (in the room) or a disabled state (on the landing page). The pre-accept socket close is unchanged. | The HTTP paths are where a reconnecting guest actually fails (F-20), and each caller already proves it holds a valid link, cookie or guest token, so naming the reason discloses nothing new ([R13.32]). Converting the pre-accept close to accept-then-close touches the region `guest-session-backend-hardening` (F-16) must also change; left to that dossier. |
| Q-5 | How is an invisible-only name reported? | New problem type `conversation/guest-display-name-invalid` (422) from `create_or_resume` and `update_display_name` when `normalise_label` yields nothing; the landing form mirrors the rule client-side and shows a field error on either signal. | Backend stays authoritative (the same rule labels agent prompts, [R13.34]); the client mirror avoids a round trip in the common case. |
| Q-6 | Which failures end a guest session on the client? | Only a refresh that receives a response (expired, rotated, missing cookie: 404; disabled: 403). A refresh that gets no response keeps the token and the context and is retried by the existing schedulers. | A laptop waking before Wi-Fi returns must not lose a valid 7-day cookie (F-8). The user refresh path keeps today's behavior (FU-1). |
| Q-7 | Where does the guest context get cleared? | Whenever a user token is applied (`applyTokens`, so `login` and Google completion) and in `session.clear()`; also replaced by the landing page on a new guest entry. Not cleared when a guest session ends, so the room can still show why. | Ties the context to the token it belongs to (F-9). Identity already imports `@shared/transport`, so no boundary moves. |
| Q-8 | Does this depend on another dossier? | `depends_on: [2026-10-05-guest-room-read-and-identity]` (implemented). `2026-10-05-guest-session-backend-hardening` is not yet written; it overlaps `guest_session_service.py` (F-15, F-16, F-21) and `ws/chatroom.py:77` (F-16), so it should list this dossier in its `depends_on`. | The prerequisite added `guestSessionId`, `viewerId` and the guest header gating this design builds on, and edited the same `ChatroomView.vue` regions. Ordering the hardening dossier after this one keeps the shared error-type change in one place. |

## 4. Reproduction

Preconditions: a room with guest links on, its guest link, and one browser.

1. Open the link, enter "Alice", enter the room. Press F5: the app lands on `/login` (F-7).
2. Repeat step 1 with an upper-case room id in the link: the room works until the first refresh
   (4 hours, or force it by deleting the access token in devtools), then expires (F-22).
3. Enter as a guest; take the network offline at the proactive refresh (exp minus 60 s; shorten the
   guest access TTL in settings to reproduce quickly), then reconnect: a reconnecting pill forever,
   no banner; focusing the tab wipes the room (F-8).
4. As a guest, follow any route to `/login` and sign in with a real account. Open a room: the socket
   loops on `/api/guest/ws-ticket` 404s (F-9).
5. Signed in as a member, open a guest link and choose "Enter as Guest", then open the sidebar's
   projects: 403s and empty lists under the full shell (F-10).
6. On the landing form, paste a zero-width space as the name and submit: "link is no longer valid"
   (F-18).
7. As a returning guest, block the network and press "Enter Chatroom", restore it and press Retry:
   nothing happens (F-19).
8. As a connected guest, turn guest links off in the room settings: after the watchdog closes the
   socket the guest sees the expiry path, or a reconnecting pill if the socket was already down,
   never "guest access has been disabled" (F-20).

## 5. Root Cause Analysis

1. The guest session's state was designed for one page lifetime: token, context and link token are
   all memory-only, and boot hydration was never taught the second refresh endpoint the guest dossier
   planned for. **Root cause for F-7.**
2. "The guest session is over" is inferred from the token's claims (`isGuestSession`) rather than
   recorded, so the moment the token is nulled the client forgets it was a guest. Every consumer that
   should react (the close handler, `hydrate`, the router) checks the derived flag, and the refresh
   function conflates "no network" with "no session". **Root cause for F-8; aggravates F-20.**
3. The guest context is an independent ref with no owner: nothing ties its lifetime to the guest
   token, so it survives into a user session, and nothing clears the user session when a guest token
   is installed. **Root cause for F-9 and F-10.**
4. The backend reuses one error, `GuestTokenInvalid` (404), for a bad link, links turned off, a dead
   cookie and an invalid name, so the client cannot render the state the guest is actually in.
   **Root cause for F-18 and the HTTP half of F-20.** (Guest dossier FU-16 already named the
   overloading.)
5. Two local defects: the Retry button is bound to one of three actions (**F-19**), and the room id
   is never canonicalised before it becomes part of a cookie path (**F-22**).

## 6. Blast Radius and Sibling Suspects

- **Blast radius.** Every anonymous guest who reloads, sleeps a laptop, or switches to an account in
  the same tab; every signed-in teacher who tests the student path. On school devices reloads are
  routine, so F-7 alone would end most guest sessions in a lesson.
- **Sibling suspects.**
  - `useIdleLogout.ts:155` calls `refreshAccessToken` but is gated on `session.isAuthenticated`
    (`:92`): **cleared**, inert for guests.
  - `useImpersonation.ts:36,44` calls `setAccessToken` directly: **cleared** for this dossier
    (admin-only; it does not pass through `applyTokens`), but it must not leave a guest context set.
    Q-7's clearing in `applyTokens` does not cover it; the implementation adds a `clearGuestContext()`
    beside those two calls.
  - The user refresh path also nulls the token on a network error (`axios.ts:290`): **confirmed**,
    same pattern, out of scope (FU-1).
  - `createGuestSession` sends any bearer the tab holds (`frontend/src/slices/conversation/api/index.ts:543-554`):
    **cleared** once Q-3 clears the user state first.
  - The pre-accept socket close (`ws/chatroom.py:62-64,77-79`): **confirmed** that codes are lost;
    deferred to `guest-session-backend-hardening` (Q-4).
  - `GuestTokenInvalid` raised for a deleted project or workspace (F-16): **confirmed** same
    overloading, owned by the hardening dossier.
  - `docs/UI/07-conversation.md:1237-1302` describes the pre-anonymous-session landing flow
    (registered enroll on mount, `requiresAuth: true`): **confirmed stale**; rewritten here because
    this dossier changes those states.

## 7. Fix Design

### 7.1 Transport (`frontend/src/shared/transport/axios.ts`)

- `setGuestContext(chatroomId)` stores the canonical form (lower-case) of the id; a shared
  `canonicalRoomId()` helper is used by every caller that builds a guest URL or storage key.
- New `guestSessionEnd: Ref<'expired' | 'disabled' | null>`, exported read-only with a reset
  function. It is the recorded fact that the guest session in this tab ended and why; it is reset by
  `setGuestContext` and `clearGuestContext`.
- `attemptRefresh`, guest branch: a failure with a response nulls the token and sets
  `guestSessionEnd` to `'disabled'` for problem type `conversation/guest-access-disabled`, otherwise
  `'expired'`; it keeps the guest context. A failure without a response (`!error.response`) keeps the
  token and returns false. The user branch is unchanged.
- New `resumeGuestSession(chatroomId): Promise<'resumed' | 'none' | 'offline'>` for boot: sets the
  context, runs the guest refresh, and on anything but success clears the context again. It does not
  set `guestSessionEnd` (a stale hint at boot is not a session ending).
- A response interceptor maps problem type `conversation/guest-access-disabled` on any guest-context
  request (the ticket route included) to `guestSessionEnd = 'disabled'`.

### 7.2 Boot and session (`app/main.ts`, `identity/stores/session.ts`, `app/router.ts`)

- `main.ts`: after `session.hydrate()`, if not authenticated, call a new
  `restoreGuestSession(location.pathname)` exported from `@slices/conversation`. It matches
  `/chatrooms/:id` or `/c/:id`, canonicalises the id, checks the guest hint, and calls
  `resumeGuestSession`. The router installs only after it settles, so the guard sees the guest token.
- `session.hydrate`: skip while a guest context is set (not only while a guest token is held), so a
  focus re-hydrate cannot wipe an ended guest session's room or swap in a user session mid-guest-session
  (which Q-3 makes possible, because the account cookie survives).
- `session.applyTokens` and `session.clear`: call `clearGuestContext()`.
- `router.ts` `onUnauthorized`: for a guest context, do nothing beyond what `attemptRefresh` recorded
  (no `clearGuestContext`, no navigation to the rejoin URL); the room renders the banner. The guest
  store's `markExpired`/`markDisabled` become derived from `guestSessionEnd` (§7.4).

### 7.3 Landing page (`GuestLandingView.vue`)

- Q-3: `chooseGuest` for an authenticated session opens `useConfirmDialog` (new strings); on confirm,
  `session.clear()` then the form. `enterChatroom` asserts no user session remains.
- `classifyError`: `ValidationError` with problem type `conversation/guest-display-name-invalid`
  returns to the form with a field error; problem type `conversation/guest-access-disabled` goes to a
  new `disabled` state using the existing `conversation.guest.guestDisabled` message; network and
  other transient errors stay `error`.
- The zod schema adds a refine mirroring `normalise_label`'s rule (drop Unicode category C except
  U+200D and U+FE0F, then trim); the submit button uses the same predicate.
- F-19: record the last attempted action (`enroll`, `resume`, `own-account`) and have Retry replay it.
- F-22: the hint key, `setGuestContext` and the rejoin URL use the canonical id.

### 7.4 Room view (`ChatroomView.vue`, `stores/guestSession.ts`)

- The guest store's `sessionState` derives from `guestSessionEnd` (`'active'` when null), so every
  path that ends the session (socket close, background refresh, request refresh, ticket) shows the
  same banner. `markExpired`/`markDisabled` stay as setters for the socket close codes and write the
  transport ref.
- The close handler (`:1037-1041`) keys on "this tab holds a guest context for this room" instead of
  `isGuestSession`.
- When `guestSessionEnd` is set, the room closes its socket deliberately (no reconnect loop) and the
  composer is disabled.
- The expired banner shows Rejoin when `rejoinUrl` is known, otherwise the new "reopen the link"
  message (Q-2). The watch that resets the state to active on `live` (`:1043-1047`) is removed: a
  live socket no longer implies the session is good once it was recorded as ended.

### 7.5 Backend

- `contexts/conversation/domain/errors.py`: `GuestAccessDisabled` and `GuestDisplayNameInvalid`;
  `interfaces/error_mapping.py` maps them to 403 `conversation/guest-access-disabled` and 422
  `conversation/guest-display-name-invalid`.
- `guest_session_service.py`: `create_or_resume` raises `GuestAccessDisabled` for links off (`:103-104`,
  after the token check, so only a link holder learns it) and `GuestDisplayNameInvalid` for an empty
  normalised name (`:106-108`); `update_display_name` raises `GuestDisplayNameInvalid` (`:76-78`);
  `refresh` raises `GuestAccessDisabled` for links off (`:209-210`).
- `guests.py` `guest_ws_ticket`: before minting, load the principal's room through the facade and
  raise `ChatroomNotFound` or `GuestAccessDisabled` as appropriate.
- The OpenAPI document gains the two problem types; `pnpm run gen:api` is rerun.

### 7.6 Existing debt (record, do not imitate)

- `GuestLandingView.test.ts` has four render-only tests (guest dossier FU-11); the states this dossier
  changes get real tests.
- `ws-manager.ts` swallows ticket errors into an unconditional reconnect (`:234-237`); left as is, the
  transport ref now carries the reason instead.
- The two e2e specs the guest dossier lists (`guest-access.spec.ts`, `guest-session-lifecycle.spec.ts`)
  were never written although its ACs 12-23 are ticked; this dossier adds the lifecycle one.
- `docs/UI/07-conversation.md` §5 is stale (§6); rewritten.

### 7.7 Patterns to follow

- Problem types and error branching: `@shared/errors` classes and `ApiError.type`, never the generated
  `ApiError` (frontend gate 13); exemplar `classifyError` in the landing page.
- Confirm dialogs: `useConfirmDialog` (frontend gate 5).
- Boot ordering: `main.ts:64-80` comment; nothing that changes auth state may run after the router
  installs and before the first navigation resolves.
- Backend domain errors and their mapping: `contexts/conversation/domain/errors.py` with
  `interfaces/error_mapping.py`; route tests patch the facade as in
  `backend/tests/unit/test_guest_roster_events.py`.
- e2e against the compose stack: `frontend/e2e/05-chatroom-live.spec.ts` (seeded room via
  `fixtures/seed`), guest link from `GET /api/chatrooms/{id}/guest-link` (`chatrooms.py:1236-1256`).

### 7.8 Reuse inventory

`setGuestContext`/`clearGuestContext`/`getGuestChatroomId`, `attemptRefresh` coalescing
(`refreshInFlight`), `refreshHttp`, `GUEST_STORAGE_PREFIX` and `readStored` in the landing page,
`useGuestSessionStore` (`rejoinUrl`, `sessionState`), `useConfirmDialog`, `@shared/errors`
(`ValidationError`, `NetworkError`), `normalise_label` and `MAX_GUEST_LABEL`, the existing i18n keys
`sessionExpired`, `guestDisabled`, `rejoin`, `retry`, `networkError`, `chooseEntry`, `enterAsGuest`,
`enterAsUser`. New keys (both `en.json` and `zh-TW.json`): the confirm dialog's title and message, the
invalid-name field error, and the reopen-the-link message.

### 7.9 Security considerations

- Boot restore sends a guest refresh only for the room in the URL and only when this browser holds
  that room's hint; the cookie is path-scoped, so no other room's cookie is presented. The account
  always wins (Q-1).
- No new persistent storage: the hint already exists and holds no token; the link token stays in
  memory ([R24.43], Q-2).
- `guest-access-disabled` is returned only to callers that already proved possession of a valid link
  (after the HMAC check), a refresh cookie, or a guest token, so it reveals nothing to an outsider
  ([R13.32]).
- Clearing the guest context whenever a user token is applied removes the F-9 path by which a guest
  cookie could replace a user session.
- Q-3's local clear leaves the account's refresh cookie valid by design; `hydrate` skipping under a
  guest context keeps it from being picked up mid-session.

## 8. Regression Test Plan

Written first, failing against current code:

1. `shared/transport/__tests__/axios.spec.ts`: guest refresh URL uses the canonical id; a network
   failure keeps the token and context; a 404 sets `guestSessionEnd = 'expired'` and keeps the
   context; a 403 `guest-access-disabled` sets `'disabled'`; `resumeGuestSession` clears the context
   on failure.
2. Identity session store (new test file): `applyTokens` and `clear` clear the guest context;
   `hydrate` is skipped under a guest context with a null token.
3. `app/__tests__`: boot restore (`restoreGuestSession`) resumes for `/chatrooms/:id` and `/c/:id`
   with a hint, does nothing without one, and runs only when the user refresh failed.
4. `GuestLandingView.test.ts`: confirm then `session.clear()` before entering as a guest; invalid-name
   422 and a zero-width name return to the form with a field error; 403 disabled shows the disabled
   state; Retry after a failed resume and after a failed own-account choice replays that action.
5. `ChatroomViewGuest.test.ts`: with a guest context and a null token, a 4401 close shows the expired
   banner; `guestSessionEnd = 'disabled'` shows the disabled banner; after a simulated reload
   (no `rejoinUrl`) the expired banner shows the reopen-the-link message.
6. Backend unit: `create_or_resume` and `update_display_name` raise `GuestDisplayNameInvalid` for a
   zero-width name; links off raises `GuestAccessDisabled` from create, refresh and the ticket route;
   a wrong link token still raises `GuestTokenInvalid` before the links check.
7. e2e `frontend/e2e/26-guest-session-lifecycle.spec.ts` against the compose stack: enter as a guest,
   reload, still in the room with the name; open a second tab on `/c/:id`, in the room; turn guest
   links off as the owner and see the disabled banner in the guest tab; a signed-in member choosing
   "Enter as Guest" confirms and then has no account shell.

## 9. Risks and Rollback

- **Boot latency.** A guest reload makes two sequential refresh calls (user, then guest); bounded to
  room URLs with a hint.
- **Cookie rotation across tabs.** Two tabs refreshing the same guest session at once race on the
  rotated cookie (F-21, hardening dossier); a losing tab now shows the expired banner instead of
  failing silently.
- **Q-3 local clear.** A teacher who enters as a guest and later navigates away stays a guest in that
  tab until they reload or sign in; the confirm dialog says so.
- **API contract.** Two new problem types; existing clients treating 404 as an invalid link keep
  working for every other cause.
- **Rollback.** No migration. Revert the commits.

## 10. Acceptance Criteria

- [ ] AC-1: a guest who reloads the room, or opens it in a new tab via `/chatrooms/:id` or `/c/:id`,
  stays in the room as the same guest session while the refresh cookie is valid; a signed-in user's
  reload is unaffected.
- [ ] AC-2: a guest refresh that fails without a response keeps the session, and the socket recovers
  when the network returns; one that fails with a response shows the expired banner (or the disabled
  banner, AC-6) and stops the reconnect loop; tab focus never clears an ended guest session's room.
- [ ] AC-3: after a guest signs in with an account in the same tab, refresh and socket tickets use the
  user endpoints; no guest cookie can replace the user session.
- [ ] AC-4: a signed-in user choosing "Enter as Guest" confirms first; afterwards the tab has no
  account state and the room works as a guest; a reload, or signing in again, restores the account
  (Q-1's account-first boot), and tab focus does not.
- [ ] AC-5: a name that is empty after normalisation is reported as a name error on the form (client
  and server), never as an invalid link.
- [ ] AC-6: with guest links off, landing-page entry, guest refresh and the guest socket ticket each
  lead the guest to the "guest access has been disabled" state, including while the socket was
  reconnecting.
- [ ] AC-7: Retry after a failed resume, a failed own-account choice, or a failed enroll repeats that
  action.
- [ ] AC-8: a link whose room id has upper-case letters refreshes and resumes like a lower-case one.
- [ ] AC-9: after a reload, a fully expired session's banner tells the guest to reopen the shared link;
  within a page lifetime it offers Rejoin.
- [ ] AC-10: backend and frontend lint, typecheck, tests, OpenAPI drift, build and the new e2e spec
  pass in CI.

## 11. SRS Delta

None. The design implements [R13.06b] and [R6.11] as written and stays within [R24.43].

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** The user refresh path also nulls the access token on a network error (`axios.ts:290`), so
  a member whose laptop wakes offline is signed out at the next focus hydrate although the refresh
  cookie is valid. Same fix shape as Q-6; separate dossier.
- **FU-2.** Pre-accept socket rejects lose their close codes (`ws/chatroom.py:62-64,77-79`); fold
  into `guest-session-backend-hardening` with F-16 (Q-4).
- **FU-3.** The guest hint in localStorage is never removed and outlives the cookie; a stale hint
  costs one failed guest refresh at boot. Consider deleting it when a boot restore returns `none`.
- **FU-4.** Guest dossier FU-14 (a rate-limit 429 is shown as cap-reached) is still open and touches
  the same `classifyError`; not in this dossier's findings.
