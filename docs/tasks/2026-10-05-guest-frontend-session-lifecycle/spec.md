---
type: bugfix
status: implemented
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
| Q-8 | Does this depend on another dossier? | `depends_on: [2026-10-05-guest-room-read-and-identity]` (implemented). `2026-10-05-guest-session-backend-hardening`, which overlaps `guest_session_service.py` (F-15, F-16, F-21) and `ws/chatroom.py:77` (F-16), lists this dossier in its `depends_on` (written and approved 2026-10-05). | The prerequisite added `guestSessionId`, `viewerId` and the guest header gating this design builds on, and edited the same `ChatroomView.vue` regions. Ordering the hardening dossier after this one keeps the shared error-type change in one place. |

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
- New `resumeGuestSession(chatroomId): Promise<'resumed' | 'ended' | 'offline'>` for boot: sets the
  context and runs the guest refresh. Success restores the token. A failure **with** a response keeps
  the context and records the end reason exactly as `attemptRefresh` does (`'disabled'` for
  `conversation/guest-access-disabled`, otherwise `'expired'`), so the room renders the matching
  banner instead of the guard sending the guest to `/login`; on `'expired'` the room's guest hint is
  removed, so the banner is shown once and a later reload falls through to the normal sign-in path. A
  failure **without** a response keeps the context with no end reason (`'offline'`, per Q-6): the room
  shows its reconnecting state and retries `resumeGuestSession` on the browser's `online` event and
  with the transport's backoff until it resumes or gets a response. (Amended 2026-10-05 after review:
  the approved text cleared the context on every failure, which sent an offline guest to `/login`
  against Q-6 and AC-1.)
- A response interceptor maps problem type `conversation/guest-access-disabled` on any guest-context
  request (the ticket route included) to `guestSessionEnd = 'disabled'`.

### 7.2 Boot and session (`app/main.ts`, `identity/stores/session.ts`, `app/router.ts`)

- `main.ts`: after `session.hydrate()`, if not authenticated, call a new
  `restoreGuestSession(location.pathname)` exported from `@slices/conversation`. It matches
  `/chatrooms/:id` or `/c/:id`, canonicalises the id, checks the guest hint, and calls
  `resumeGuestSession`. The router installs only after it settles, so the guard sees the guest token.
- `router.ts` guard context: `hasGuestSession` is true while a guest token is held **or** a guest
  context is set for the target room, so a guest whose boot restore ended or is offline still reaches
  the room, where the banner or the reconnecting state explains what happened. The expired banner
  shown after a reload also offers a sign-in link for visitors who hold an account.
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
  `refresh` raises `GuestAccessDisabled` for links off, but only after the cookie has been matched to a
  session of this room: today the links check (`:209-210`) runs before the hash lookup (`:212-215`)
  and the route requires only a non-empty cookie (`guests.py:147-148`), so keeping that order would
  let any request with an arbitrary cookie value learn whether a room has guest links off, against
  Q-4's rationale. The rotation write happens only after both checks pass. (Amended 2026-10-05 after
  approval, from the `guest-session-backend-hardening` analysis; implementation-order correction, no
  change to Q-4's decision.)
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
  ([R13.32]). On refresh this holds only because the cookie is matched before the links check
  (§7.5); a test pins that an unknown cookie on a links-off room answers the same 404 as on a
  links-on room.
- Clearing the guest context whenever a user token is applied removes the F-9 path by which a guest
  cookie could replace a user session.
- Q-3's local clear leaves the account's refresh cookie valid by design; `hydrate` skipping under a
  guest context keeps it from being picked up mid-session.

## 8. Regression Test Plan

Written first, failing against current code:

1. `shared/transport/__tests__/axios.spec.ts`: guest refresh URL uses the canonical id; a network
   failure keeps the token and context; a 404 sets `guestSessionEnd = 'expired'` and keeps the
   context; a 403 `guest-access-disabled` sets `'disabled'`; `resumeGuestSession` keeps the context on
   every failure, records `'expired'` or `'disabled'` for a response, and records nothing for a
   network failure.
2. Identity session store (new test file): `applyTokens` and `clear` clear the guest context;
   `hydrate` is skipped under a guest context with a null token.
3. `app/__tests__`: boot restore (`restoreGuestSession`) resumes for `/chatrooms/:id` and `/c/:id`
   with a hint, does nothing without one, and runs only when the user refresh failed; a boot refresh
   answered 404 lands on the room's expired banner (not `/login`) and removes the hint; a boot refresh
   with no network lands on the room's reconnecting state and resumes when the network returns.
4. `GuestLandingView.test.ts`: confirm then `session.clear()` before entering as a guest; invalid-name
   422 and a zero-width name return to the form with a field error; 403 disabled shows the disabled
   state; Retry after a failed resume and after a failed own-account choice replays that action.
5. `ChatroomViewGuest.test.ts`: with a guest context and a null token, a 4401 close shows the expired
   banner; `guestSessionEnd = 'disabled'` shows the disabled banner; after a simulated reload
   (no `rejoinUrl`) the expired banner shows the reopen-the-link message.
6. Backend unit: `create_or_resume` and `update_display_name` raise `GuestDisplayNameInvalid` for a
   zero-width name; links off raises `GuestAccessDisabled` from create, refresh and the ticket route;
   a wrong link token still raises `GuestTokenInvalid` before the links check; an unknown or
   foreign refresh cookie on a links-off room raises `GuestTokenInvalid` (404), not
   `GuestAccessDisabled`, and rotates nothing.
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

- [x] AC-1: a guest who reloads the room, or opens it in a new tab via `/chatrooms/:id` or `/c/:id`,
  stays in the room as the same guest session while the refresh cookie is valid, including when the
  network is briefly down at reload (it resumes when the network returns); a signed-in user's reload is
  unaffected.
- [x] AC-2: a guest refresh that fails without a response keeps the session, and the socket recovers
  when the network returns; one that fails with a response shows the expired banner (or the disabled
  banner, AC-6) and stops the reconnect loop; tab focus never clears an ended guest session's room.
- [x] AC-3: after a guest signs in with an account in the same tab, refresh and socket tickets use the
  user endpoints; no guest cookie can replace the user session.
- [x] AC-4: a signed-in user choosing "Enter as Guest" confirms first; afterwards the tab has no
  account state and the room works as a guest; a reload, or signing in again, restores the account
  (Q-1's account-first boot), and tab focus does not.
- [x] AC-5: a name that is empty after normalisation is reported as a name error on the form (client
  and server), never as an invalid link.
- [x] AC-6: with guest links off, landing-page entry, guest refresh and the guest socket ticket each
  lead the guest to the "guest access has been disabled" state, including while the socket was
  reconnecting.
- [x] AC-7: Retry after a failed resume, a failed own-account choice, or a failed enroll repeats that
  action.
- [x] AC-8: a link whose room id has upper-case letters refreshes and resumes like a lower-case one.
- [x] AC-9: after a reload, a fully expired session lands on the room's expired banner (never `/login`),
  which tells the guest to reopen the shared link; within a page lifetime it offers Rejoin.
- [x] AC-10: backend and frontend lint, typecheck, tests, OpenAPI drift, build and the new e2e spec
  pass in CI.

## 11. SRS Delta

None. The design implements [R13.06b] and [R6.11] as written and stays within [R24.43].

## 12. Deviation Log

Appended by /build.

- **D-1.** §7.5 says the OpenAPI document gains the two problem types. Domain problem types are not
  enumerated in the OpenAPI document anywhere in this codebase (they are registered by
  `error_mapping.py` at runtime), so the exported spec is byte-identical to the committed one and
  `pnpm run gen:api` produces no diff. The guest ticket route's new `db` dependency is not part of the
  published contract either.
- **D-2.** §7.4's "the room closes its socket deliberately" needed a transport fix:
  `Channel.openSocket` scheduled a reconnect on a failed ticket even after `disconnect()` had paused
  the channel, so an ended session reconnect-looped anyway. The catch now retries only when the
  channel is neither paused nor closed (`ws-manager.ts`), with a test in `ws-manager.spec.ts`.
- **D-3.** §7.1's offline retry "with the transport's backoff" is implemented in `fetchWsTicket`:
  with a guest context and no token it runs the guest refresh first, so the socket's existing backoff
  is the retry loop; with an ended session it throws without a request. The `online` event handler
  lives in `ChatroomView` and calls `wsChannel.connect()`, which takes the same path. When a missing
  token comes back, the room re-reads its room, agent, member and message queries, since those ran
  without a bearer.
- **D-4.** Not in §7.2: `session.hydrate` also re-checks for a guest session after its refresh
  resolves, in both the success and the failure branch. The self-audit found that a focus hydrate in
  flight while a guest entered would, on its 401, `clear()` the new guest session, or on success
  install the account under it. Test-first in `identity/__tests__/session.test.ts`.
- **D-5.** Not in §7.4: the room's `viewerIsGuest` also holds while the tab holds the room's guest
  context, so a guest whose session ended (token null) is not shown the member header (settings,
  export, Back) and does not fire the members-only workspace reads.
- **D-6.** Structure, for testability: the boot sequence is `app/boot.ts` `restoreSessionAtBoot`
  (called from `main.ts`), and the guard body is the exported `guardRoute` in `router.ts`. The hint
  read, write and removal moved from the landing page into `conversation/utils/guestHint.ts`, which
  also holds `restoreGuestSession`. A read falls back to the key spelled as the link spelled the room
  id, so a hint written before canonicalisation is still found.
- **D-7.** §7.3 named the hint key, the guest context and the rejoin URL for F-22; the landing page
  also sends the canonical id in the session-create and enroll URLs, so one id is used throughout.
- **D-8.** The existing unit test `test_guest_links_disabled_raises` asserted the 404 that Q-4
  replaces; it now asserts `GuestAccessDisabled`.
- **D-9.** §8 item 7's "turn guest links off as the owner and see the disabled banner in the guest
  tab" reloads the guest tab after the owner's change, exercising the refresh path deterministically;
  a live socket learns of the change only at its next ticket or refresh, whose timing the e2e cannot
  control.
- **D-10.** (Agreed with the requester 2026-10-06, after `/code-review`.) §7.1 recorded an end for
  any answered guest refresh failure and §7.4 kept a 4401 close as a direct `markExpired`. Both ended
  live sessions wrongly: 4401 is the server's "re-handshake" close (`ws_auth.py:26`), and a 502
  during a deploy, a 429 or the two-tab cookie race also counted as an end (and at boot deleted the
  hint holding the browser id). Now only an answered 401 or 404 (expired) or 403 (disabled when the
  type is `guest-access-disabled`, expired otherwise) ends the session; any other answer is treated
  like no network. A 4401 close triggers a guest refresh whose answer decides. §8 item 5's 4401 test
  now mocks that refresh.
- **D-11.** (Agreed with the requester 2026-10-06.) Q-1's "the account always wins" did not hold for
  an account holder who once entered a room as a guest and reloads it offline: boot cannot tell why
  the account refresh failed, restores the guest context, and the guest session came back with the
  network. A guest context with neither a token nor an end is now a *pending* restore. In that state
  `hydrate` tries the account (keeping the pending restore on any failure), and `app/boot.ts`
  registers `preferAccountOverPendingGuest` with the transport, so every retry (the socket ticket
  path, the `online` event, tab focus) asks the account first and reloads the page into it when it
  answers. §7.2's "hydrate skips while a guest context is set" now holds for a live or ended guest
  session only.
- **D-12.** (Agreed with the requester 2026-10-06.) Q-4's route list gains the registered enrolment
  path: `GuestService.enroll` raises `GuestAccessDisabled` after the token check, so "Enter as
  <account>" shows the disabled state instead of an invalid link.
- **D-13.** From the same review: the response interceptor records `disabled` only for the guest
  socket ticket (a landing-page create for another room answered `guest-access-disabled` no longer
  ends the held room's session); a guest refresh whose context was replaced while it was in flight
  (a sign-in) leaves the token alone; the room's `online` listener is attached only while the view
  is active, so a KeepAlive-cached room is not reopened behind another route.
- **D-14.** §8 item 7's "has no account shell" is asserted through the room header's Settings button
  (shown to the member who owns the seeded room, hidden from an anonymous guest), with a signed-in
  baseline first so its absence is meaningful. The first CI run used the sidebar's "Main navigation",
  which a chatroom route collapses and makes inert: the check failed after the reload and its
  guest-phase counterpart passed whatever the session.

## 13. Follow-ups

- **FU-1.** The user refresh path also nulls the access token on a network error (`axios.ts:290`), so
  a member whose laptop wakes offline is signed out at the next focus hydrate although the refresh
  cookie is valid. Same fix shape as Q-6; separate dossier.
- **FU-2.** Pre-accept socket rejects lose their close codes (`ws/chatroom.py:62-64,77-79`); fold
  into `guest-session-backend-hardening` with F-16 (Q-4).
- **FU-3.** (Folded into §7.1 by the 2026-10-05 review amendment: a boot restore answered 404 now
  removes the hint.) Hints for rooms the browser never reloads into still outlive their cookies.
- **FU-4.** Guest dossier FU-14 (a rate-limit 429 is shown as cap-reached) is still open and touches
  the same `classifyError`; not in this dossier's findings.
- **FU-5.** A guest holding a live token whose room's links are turned off gets 403
  `conversation/forbidden-in-room` from ordinary room reads (the room access gate), not
  `guest-access-disabled`; it learns the reason only at its next refresh or socket ticket (up to the
  access-token lifetime if its socket stays open). Naming the reason on the room read path, or a
  server push on the links change, would close that window.
- **FU-6.** `useGuestSessionStore.clear()` has no caller (pre-existing dead code).
- **FU-7.** Found by the security audit, owned by `guest-session-backend-hardening`: guest session
  create and refresh raise `ChatroomNotFound` before the link or cookie check, so a caller can tell an
  existing room id from a missing one. Room ids are UUIDv4, so there is no practical enumeration.
- **FU-8.** Local Windows full-suite runs time out two pre-existing tests at 5 s under load
  (`AppShell.test.ts` "starts collapsed on an immersive route", `ChatroomView.test.ts` "reopening the
  export modal cancels the in-flight poller"); both pass in isolation. CI is authoritative.
- **FU-9.** From `/code-review`, owned by `guest-session-backend-hardening` (F-16's 4404): when the
  room is deleted under a connected guest, the ticket pre-check answers 404 `chatroom-not-found`,
  which the client does not treat as an end, so the socket retries on its backoff with no banner.
- **FU-10.** From `/code-review` (altitude): an ended guest's socket is stopped by view-level hooks
  in `ChatroomView` because `Channel` treats the terminal error from `fetchWsTicket` as retryable.
  Stopping retries in `Channel.openSocket` on a terminal error would let every consumer drop the
  hooks.
- **FU-11.** From `/code-review` (simplification): `useGuestSessionStore.sessionState` and
  `markDisabled` are pass-throughs to the transport's `guestSessionEnd`, and `GUEST_STORAGE_PREFIX`
  is used only by `utils/guestHint.ts`; the store could keep only the link token and `rejoinUrl`.
