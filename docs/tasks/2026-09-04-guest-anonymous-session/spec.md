---
type: feature
status: implemented
created: 2026-09-04
requirements: [R5.04, R6.02, R6.11, R6.12, R13.05, R13.06, R13.07, R13.33, R24.43]
depends_on: []
---

# Anonymous Guest Sessions for Chatroom Access

## 1. Summary

Replace the current guest-link flow, which requires full account registration and email
verification (7+ screen transitions), with a lightweight anonymous session: click
link, enter display name, enter chatroom. The guest token in the URL serves as the
authentication credential; the server issues a chatroom-scoped JWT without creating a
user account.

The work is divided into three implementation phases, each intended for a separate
session. Phase boundaries are marked throughout; acceptance criteria are grouped
accordingly.

## 2. Goals and Non-goals

**Goals**

- Zero-registration guest access via guest links (2 steps: click link, enter name).
- Anonymous guest sessions scoped to a single chatroom, backed by a `guest_sessions`
  table and a chatroom-scoped JWT.
- Browser-persistent guest identity per room (localStorage), so returning guests are
  recognised.
- 4-hour access JWT with silent refresh via an httpOnly cookie, matching the existing
  user auth pattern.
- 50-guest cap per chatroom (deployment-configurable).
- Full audit trail for guest actions (messages, presence, activities).
- All existing guest UX issues resolved: brand animation bypass, settings gear hiding,
  "welcome back" rejoin flow, display name editability, session expiry guidance.

**Non-goals**

- Changing the regular (non-guest) user registration/login flow.
- Guest-to-registered-user upgrade or account linking.
- Guest access to features outside the chatroom (export, settings write, observers,
  workflow backstage).
- Anonymous access without a guest link (the room's `allow_guest_links` flag remains the
  gate).
- Removing the `chatroom_guests` table or the registered-user guest path (it continues
  to work for users already enrolled; new guest-link visitors use the anonymous path).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should a returning guest (same link, same browser) be recognised as the same person? | Remember per room | localStorage stores a `browser_id` per chatroom. Returning guest keeps display name and message-history continuity. Guest can clear localStorage to start fresh. |
| Q-2 | How long should a guest session last? | 4 h access JWT, auto-refresh | Access JWT has a 4-hour TTL. A guest-specific httpOnly refresh cookie (7-day TTL, path-scoped) enables silent refresh as long as the cookie survives. On full expiry, guest clicks the link again. |
| Q-3 | Should there be a concurrent-guest cap per chatroom? | 50 (configurable) | Deployment-level setting `limits.max_guests_per_chatroom` (default 50). Prevents abuse on public links while supporting classroom/workshop scenarios. |
| Q-4 | What happens when a registered user clicks a guest link? | Anonymous guest session | Guest links always create anonymous sessions, regardless of login state. The page detects the existing login and offers a choice: "Enter as Guest" (anonymous) or "Enter as [display_name]" (use existing account, current registered-guest path). This preserves both paths without forcing either. |
| Q-5 | Overlap dependency with `2026-07-07-graphrag-two-axis-redesign` or `2026-07-19-large-artifacts-silently-dropped`? | None | Neither touches auth, guest, conversation access, or frontend routing. No file overlap. |
| Q-6 | How are the three phases delivered? | Stacked PRs, remote CI | Each phase is a separate branch + PR. Branch chain: `main` -> `guest-anon-phase1` (PR-1) -> `guest-anon-phase2` (PR-2) -> `guest-anon-phase3` (PR-3). Merge order: PR-3 into PR-2, PR-2 into PR-1, PR-1 into `main`. All tests run on remote CI only, never locally. |

## 4. Current State

### Entry flow (7+ steps)

1. `GuestLandingView.vue:22-27`: unauthenticated visitor is redirected to `{ name: 'root' }`
   with `?next=/g/{id}/{token}`.
2. `Landing.vue:84-98`: plays a ~2.7 s brand animation (`LandingIntro.vue:70`,
   `BODY_MS = 2700`), then redirects to `identity.login` with `?redirect=`.
3. `LoginView.vue`: visitor must log in or register.
4. `RegisterView.vue:82-88`: registration posts email + password + captcha, then
   redirects to login with `?pendingVerify=1`.
5. `auth_service.py:333-334`: password login rejects unverified accounts
   (`AccountNotVerified`).
6. Visitor checks email, clicks verification link, returns to login, logs in.
7. Redirect chain returns to `/g/{id}/{token}` -> `GuestLandingView` shows display-name
   form.
8. `enrollGuest()` posts to `POST /api/guest/{chatroom_id}/{guest_token}/enroll`
   (`guests.py:34`), which requires `current_principal`.
9. On success, `history.replaceState(null, '', '/c/{chatroomId}')` strips the token
   (R24.43) and navigates to the chatroom.

### Guest identity

- R5.04 defines Guest as "a registered Individual account".
- `chatroom_guests` table (`tables.py:157-172`): composite PK `(chatroom_id, user_id)`,
  both FK. `display_name VARCHAR(100)` nullable. Enrollment uses
  `ON CONFLICT DO NOTHING` (`chatroom_repo.py:866`), so display name is immutable after
  first enrollment.

### Auth infrastructure

- `Principal` (`permissions.py:104-108`): `(user_id, is_admin, email_verified)`. Cannot
  represent a non-user entity.
- `AuthMiddleware` (`auth.py:33-77`): extracts Bearer JWT, verifies via Vault Transit
  (RS256), fetches user profile from identity context, constructs `Principal`.
- JWT claims (`jwt.py:78-90`): `sub` = user_id, `sid` = session_id,
  `token_use = "access"`, `rol` = `"user"` | `"admin"`.
- WebSocket auth (`ws_auth.py:106-136`): single-use ticket system. Client calls
  `POST /api/auth/ws-ticket` (HTTP, bearer-authed) to get an opaque 30 s ticket,
  passes it as WS subprotocol `ticket.<id>`. Server does `GETDEL` from Redis, verifies
  the stashed JWT, constructs Principal.
- Token refresh (`ws_auth.py:139-157`): in-socket `{"type":"refresh","access_token":"..."}`
  re-verified with full JWT check.

### Message authorship

- `messages.sender_type`: PG ENUM `message_sender_type` with values `"user"`,
  `"agent"`, `"system"` (`tables.py:206`).
- `messages.sender_id`: bare UUID, **no FK constraint** (`tables.py:209`). Display-name
  resolution happens at read time via `prefer_guest_label()` (`author_labels.py:12-14`)
  and participant lists.
- `MessageOut` (`messages.py:95-105`) returns raw `sender_id`; the frontend resolves
  display names from room participant data.

### Audit

- `AuditEvent.actor_user_id` is nullable (`audit.py:107`). The `audit_logs` column is
  also nullable (`audit.py:46`).

### Access control

- `resolve_room_access()` (`access.py:108-128`): fetches project roles and checks
  `guests.is_guest()` against `chatroom_guests` table.
- `_satisfies_room_flags()` (`access.py:183-198`): if `allow_guest_links` and
  `access.is_guest`, grants read.
- `ensure_can_read()` (`access.py:200+`): enforces the room flags.

### Settings gear

- `ChatroomHeader.vue:61-69`: gear icon rendered unconditionally, no guest gate.
  Clicking navigates to settings view; backend returns 403 for writes but guests
  reach the page.

## 5. Design

### Options considered

**Option A -- Ephemeral guest accounts (phantom user rows)**

Create a minimal `users` row (synthetic email `guest-{uuid}@guest.smap.local`,
no password, status `guest`) so the existing `Principal`/`sender_id`/audit
infrastructure works unchanged.

Trade-offs: minimal code changes, but pollutes the `users` table with disposable
rows that need periodic cleanup. The `uq_users_email_active` unique constraint
requires synthetic email values. Blurs the boundary between real accounts and
disposable guest tokens. The identity context would need to handle a user status
it was never designed for, and every query against `users` (admin panels, search,
analytics) would need to filter out guest rows.

**Option B -- Separate guest identity system (chosen)**

New `guest_sessions` table in the conversation context. Guest JWT carries
`token_use: "guest_access"` with chatroom-scoped claims. `Principal` extended with
`is_guest` and `chatroom_id` fields. New `"guest"` value in the
`message_sender_type` PG ENUM.

Trade-offs: more initial work (auth middleware branch, sender type migration,
participant resolution), but clean separation between real users and anonymous
guests. No `users`-table pollution. Guest lifecycle is self-contained in the
conversation context. The auth extension is additive (new fields default to
`False`/`None`), so all existing code paths are unaffected.

### Decision

Option B. The `users` table is a core identity artifact shared across every bounded
context; inserting disposable rows there violates the DDD boundary and creates a
maintenance burden (cleanup jobs, query filters, admin-panel exclusions). Option B
keeps guest identity in the conversation context where it belongs and extends the auth
layer additively. The one-time cost of adding a sender type and widening `Principal` is
paid once; the ongoing cost of phantom-user hygiene would be paid forever.

### Phase structure

```
Phase 1 -- Backend core          (separate session, separate branch + PR)
  guest_sessions table, guest auth endpoint, guest JWT, extended Principal,
  WS auth for guests, guest sender type, access-check shortcut, audit path,
  guest refresh endpoint, cap enforcement.

Phase 2 -- Frontend direct entry (separate session, branches from Phase 1)
  GuestLandingView rewrite, brand-animation bypass, guest JWT lifecycle,
  localStorage browser_id, rejoin detection, route guard updates, WS ticket
  acquisition for guests.

Phase 3 -- UX polish             (separate session, branches from Phase 2)
  Settings gear hiding, session expiry banner, display-name update,
  guest-link-disabled mid-session handling, WebSocket reconnect guidance,
  logged-in user choice (guest vs own account), i18n for all new strings.
```

### Delivery workflow

Stacked PRs with remote CI verification (Q-6):

```
main ─── guest-anon-phase1 ─── guest-anon-phase2 ─── guest-anon-phase3
              PR-1                  PR-2                  PR-3
              (base: main)          (base: phase1)        (base: phase2)
```

Merge order: PR-3 → PR-2 → PR-1 → `main`. Each PR targets its predecessor
as base until the inner PR merges, at which point GitHub retargets.

All tests (backend `pytest`, frontend `pnpm test`/`pnpm lint`/`pnpm typecheck`,
E2E) run on remote CI only. Local runs are not part of the verification gate.

## 6. Detailed Changes

### Phase 1 -- Backend core

**Migration**

- Add `"guest"` to PG ENUM `message_sender_type` (`ALTER TYPE ... ADD VALUE`).
- Create `guest_sessions` table:

  | Column | Type | Constraints |
  |--------|------|-------------|
  | `id` | `UUID` | PK, `gen_random_uuid()` |
  | `chatroom_id` | `UUID` | FK `chatrooms.id` CASCADE, NOT NULL |
  | `display_name` | `VARCHAR(100)` | NOT NULL |
  | `browser_id` | `TEXT` | NULL (opaque client-generated ID for rejoin matching) |
  | `refresh_token_hash` | `TEXT` | NOT NULL, UNIQUE |
  | `last_seen_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` |
  | `created_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` |

  Index: `(chatroom_id, browser_id)` where `browser_id IS NOT NULL` for rejoin lookup.
  No FK to `users` -- this is the entire point.

**`shared_kernel/auth`**

- `permissions.py`: extend `Principal` with two optional fields:

  ```
  is_guest: bool = False
  chatroom_id: uuid.UUID | None = None
  ```

  All existing construction sites pass neither field, so defaults apply. Guest
  principals carry `user_id` set to `guest_session_id` (the UUID from
  `guest_sessions.id`), `is_admin=False`, `email_verified=False`, `is_guest=True`,
  `chatroom_id=<scoped room>`.

- `jwt.py`: add `sign_guest_token()` producing a JWT with:

  | Claim | Value |
  |-------|-------|
  | `iss` | config issuer |
  | `aud` | config audience |
  | `sub` | `str(guest_session_id)` |
  | `token_use` | `"guest_access"` |
  | `rol` | `"guest"` |
  | `chatroom_id` | `str(chatroom_id)` |
  | `display_name` | normalised display name |
  | `iat`, `nbf`, `exp` | standard; TTL from `settings.jwt.guest_access_ttl_seconds` (default 14400 = 4 h) |
  | `jti` | `str(uuid4())` |

  Add `verify_guest_token()` that validates these claims and returns a
  `GuestClaims` dataclass.

- `context.py`: no changes; `RequestContext.principal` already holds `Principal`.

**`app/api/middleware/auth.py`**

- After extracting the Bearer token, check `token_use`. If `"guest_access"`, call
  `verify_guest_token()`, construct `Principal(user_id=claims.guest_session_id,
  is_admin=False, email_verified=False, is_guest=True,
  chatroom_id=claims.chatroom_id)`. Skip the identity-context profile lookup (no user
  row exists). Set `ctx.session_id = None` (guests have no identity-context session).

**`app/config/settings.py`**

- `JwtSection`: add `guest_access_ttl_seconds: int = 14400` (4 h).
- `LimitsSection`: add `max_guests_per_chatroom: int = 50`.
- `guest_refresh_ttl_seconds: int = 604800` (7 days) in `JwtSection`.

**`contexts/conversation/application/guest_session_service.py`** (new)

Service with two public methods:

1. `create_or_resume(chatroom_id, guest_token, display_name, browser_id, remote_ip,
   request_id)`:
   - Validate chatroom exists, constant-time compare `guest_token`, check
     `allow_guest_links`.
   - If `browser_id` provided, look up `guest_sessions` by `(chatroom_id, browser_id)`.
     If found, update `display_name` (if changed) and `last_seen_at`, reuse the session.
   - Otherwise, enforce guest cap: `SELECT COUNT(*) FROM guest_sessions WHERE
     chatroom_id = ? AND last_seen_at > now() - interval '24 hours'`. If >= limit,
     raise `GuestCapReached`.
   - Insert new `guest_sessions` row. Generate a refresh token (32 random bytes,
     base64url), store its Argon2 hash in `refresh_token_hash`.
   - Emit `guest.session.created` or `guest.session.resumed` audit event.
   - Sign and return `(access_jwt, refresh_token, guest_session_id, is_resuming,
     display_name)`.

2. `refresh(chatroom_id, refresh_token)`:
   - Look up session by chatroom_id where the refresh_token_hash matches.
   - Verify `allow_guest_links` is still enabled.
   - Update `last_seen_at`.
   - Issue a new access JWT. Optionally rotate the refresh token (token rotation
     pattern, same as user sessions).
   - Return `(access_jwt, new_refresh_token, guest_session_id)`.

**`contexts/conversation/infrastructure/repositories/guest_session_repo.py`** (new)

Repository implementing the storage for `guest_sessions`. Methods:
`create`, `find_by_browser_id`, `find_by_id`, `count_active(chatroom_id, since)`,
`update_last_seen`, `update_display_name`, `find_by_refresh_hash`.

**`contexts/conversation/infrastructure/tables.py`**

Add the `guest_sessions` table definition.

**`contexts/conversation/interfaces/facade.py`**

Expose `create_or_resume_guest_session()` and `refresh_guest_session()`.

**`app/api/v1/guests.py`**

New endpoints (alongside the existing `enroll` endpoint, which is kept for backward
compatibility):

- `POST /api/guest/{chatroom_id}/{guest_token}/session` -- public (no
  `current_principal`). Accepts `{ display_name: str, browser_id?: str }`. Returns
  `{ access_token, refresh_token, guest_session_id, display_name, is_resuming }`.
  Sets the refresh token as an httpOnly cookie:
  `smap_guest_refresh_{chatroom_id}`, `Path=/api/guest/{chatroom_id}`,
  `HttpOnly`, `Secure`, `SameSite=Strict`, max-age = `guest_refresh_ttl_seconds`.

- `POST /api/guest/{chatroom_id}/refresh` -- public (no `current_principal`). Reads
  the refresh cookie, validates, returns `{ access_token }` and rotates the cookie.

**`shared_kernel/realtime/ws_auth.py`**

- `mint_ws_ticket()`: accept an optional `is_guest` flag. When True, stash the guest
  access token the same way.
- `authenticate_subprotocol()`: after verifying the JWT, check `token_use`. If
  `"guest_access"`, construct a guest `Principal`. Skip user-profile lookup.
- `refresh_principal()`: handle `"guest_access"` tokens the same way.

- New endpoint `POST /api/guest/ws-ticket` -- requires a valid guest access JWT in
  Bearer header (validated by the middleware's guest branch). Mints a WS ticket the
  same way as the regular endpoint.

**`contexts/conversation/application/access.py`**

- `resolve_room_access()`: add early return for guest principals. If
  `principal.is_guest` and `principal.chatroom_id == chatroom_id`, return
  `RoomAccess(is_guest=True, ...)` without querying `chatroom_guests` or project roles.
  If `chatroom_id` does not match, raise `Forbidden`.

**`contexts/conversation/application/message_service.py`**

- `send_message()`: accept `sender_type=SenderType.GUEST` with
  `sender_id=guest_session_id`. The existing code already handles nullable sender_id
  and doesn't enforce FK, so no structural change is needed beyond accepting the new
  enum value.

**`app/api/v1/messages.py`**

- The route handler for `POST /messages` currently reads `principal.user_id` as
  `sender_user_id`. For guest principals, this is `guest_session_id`. Pass
  `sender_type = SenderType.GUEST if principal.is_guest else SenderType.USER`.

**Audit events**

- Guest actions use `actor_user_id = guest_session_id`. The column is nullable and
  carries no FK constraint (`audit.py:46`), so any UUID works. The `metadata` dict
  carries `{"guest": true, "chatroom_id": "..."}` for filterability.

### Phase 2 -- Frontend direct entry

**`slices/conversation/views/GuestLandingView.vue`** (rewrite)

- Remove the `if (!session.isAuthenticated)` redirect to root (`line 22-27`).
- The component is now a standalone enrollment form, independent of login state.
- On mount:
  1. Read `browser_id` from `localStorage` key `smap:guest:{chatroomId}`.
  2. If present, pass it to the session endpoint for rejoin detection.
  3. If `is_resuming` in response, show "Welcome back, {display_name}" with option
     to change name.
  4. If not resuming, show the display-name form.
- On submit: call `POST /api/guest/{chatroomId}/{guestToken}/session`.
- On success:
  1. Store `{ browser_id, guest_session_id }` in localStorage.
  2. Store the access JWT in the transport layer (same in-memory ref as regular JWT).
  3. `history.replaceState(null, '', '/c/{chatroomId}')` (R24.43 preserved).
  4. Navigate to `conversation.chatroom`.
- Error states remain: `idle`, `enrolling`, `invalid`, `error`, plus new `cap_reached`
  (429 from guest cap).

**`slices/conversation/routes.ts`**

- `conversation.guest` route: meta stays `{ requiresAuth: false, layout: 'auth' }`.
  No changes needed.

**`app/router.ts` / `guards.ts`**

- `authGuard`: for `conversation.chatroom` route, allow access when the transport
  layer holds a valid guest JWT (check `token_use === "guest_access"` in the stored
  token). Add a `meta.allowGuestSession: true` flag to the chatroom route.

**`app/views/Landing.vue`**

- In `forwardAfterIntro()` (`line 84-98`): if `nextTarget` starts with `/g/`,
  skip the animation and forward immediately. The guest link is a functional URL,
  not a brand-discovery path.

**`shared/transport/axios.ts`**

- `attemptRefresh()`: detect guest context. If the current JWT is a guest token
  (or if no JWT exists but a guest refresh cookie might), call
  `POST /api/guest/{chatroomId}/refresh` instead of `/api/auth/refresh`. The
  httpOnly cookie is sent automatically (`withCredentials: true`).
- Store `chatroomId` alongside the access token when a guest JWT is active, so the
  refresh call knows the path.

**`shared/transport/ws-manager.ts`**

- `fetchWsTicket()`: if guest context, call `POST /api/guest/ws-ticket` instead of
  `/api/auth/ws-ticket`.
- `runTokenRefresh()`: if guest, use the guest refresh endpoint.

**`slices/conversation/api/index.ts`**

- Add `createGuestSession(chatroomId, guestToken, displayName, browserId?)` calling
  the new `POST /api/guest/{chatroomId}/{guestToken}/session` endpoint.

**i18n keys** (en + zh-TW)

- `conversation.guest.welcomeBack`: "Welcome back, {name}"
- `conversation.guest.changeName`: "Change name"
- `conversation.guest.capReached`: "This chatroom has reached its guest limit.
  Please try again later."
- `conversation.guest.sessionExpired`: "Your guest session has expired. Click the
  invite link to rejoin."

### Phase 3 -- UX polish

**Settings gear hiding**

- `ChatroomHeader.vue`: gate the settings gear icon with
  `v-if="!viewerIsGuest"`. The `viewer_is_guest` flag is already available from
  `ChatroomOut` and used for export gating (`ChatroomView.vue:26`).

**Session expiry banner**

- `useChatroomSocket.ts` or a new `useGuestSession` composable: track access JWT
  expiry. When < 5 min remaining and refresh fails, show an inline banner:
  "Your session is expiring. [Extend session]" (button triggers manual refresh
  via the guest token link stored in memory). If fully expired, show
  "Session expired. [Rejoin]" linking back to `/g/{chatroomId}/{guestToken}`.

- Store the `guestToken` in a Pinia store (memory-only, not persisted) during
  enrollment so the rejoin link can be reconstructed.

**Display name update**

- `guest_session_service.py`: `update_display_name(guest_session_id, new_name)`.
- `PUT /api/guest/session/{guest_session_id}/display-name` (requires valid guest
  JWT matching the session). Returns 204.
- Frontend: small edit icon next to the guest's own name in the participant list
  or header.

**Logged-in user choice** (Phase 3, not Phase 2)

- When `GuestLandingView` detects `session.isAuthenticated`, show a choice card:
  "Enter as Guest" (anonymous session) vs "Enter as {account_display_name}" (use
  existing registered-guest enrollment). The second option calls the existing
  `enrollGuest()` endpoint.

**WebSocket reconnect guidance**

- When WS auth fails with close code `4401` (auth failure) and the session was
  a guest session, show a specific message: "Guest session expired" instead of
  a generic reconnection spinner. Include a link to rejoin.

**Guest-link-disabled mid-session**

- The existing mid-socket re-auth (`_ROOM_REAUTH_EVERY_N_TICKS = 2` at 30 s ticks)
  already tears down the socket if `allow_guest_links` is toggled off. No backend
  change needed. Frontend: detect the `4403` close code and show "Guest access
  has been disabled by the room owner." instead of a generic error.

## 7. NFR Checklist

- [x] i18n -- all new user-facing strings listed in Phase 2 and 3 sections, through
  `$t()`.
- [x] Audit log -- `guest.session.created`, `guest.session.resumed`,
  `guest.session.refreshed` events. Message send uses `actor_user_id =
  guest_session_id` with `metadata.guest = true`.
- [x] Tenant isolation -- guest JWT is scoped to a single `chatroom_id`. The
  `resolve_room_access()` shortcut rejects any chatroom_id mismatch. No
  org/project access is possible.
- [x] Error handling UX -- `idle`, `enrolling`, `invalid`, `error`, `cap_reached`
  states in the enrollment form. Session expiry banner and reconnect guidance
  in Phase 3.
- [x] Performance -- guest cap query uses an index on `(chatroom_id, last_seen_at)`.
  Rejoin lookup uses a partial index on `(chatroom_id, browser_id)`.
  `COUNT(*)` with a 24 h window is bounded by the cap itself.

## 8. Security Considerations

**New unauthenticated endpoint.** `POST /api/guest/{chatroom_id}/{guest_token}/session`
is public. Mitigations:

- `guest_token` is 192-bit random (`secrets.token_bytes(24)`, base64url). Brute-force
  is infeasible.
- Constant-time comparison (`hmac.compare_digest`) prevents timing attacks (existing
  pattern from `guest_service.py:55`).
- Rate limiting: apply the same per-IP rate limit as the login endpoint.
- The chatroom_id is a UUID; existence is not leaked on token mismatch (existing
  pattern: `GuestTokenInvalid` on both missing room and bad token,
  `guest_service.py:58-60`).

**Guest JWT scope.** The `chatroom_id` claim hard-scopes every guest JWT to one room.
The auth middleware and `resolve_room_access()` enforce this. A guest JWT cannot
access any other chatroom, any API endpoint that requires org/project roles, or
admin endpoints.

**Refresh token.** Stored as Argon2 hash in `guest_sessions.refresh_token_hash`
(same pattern as user sessions, `sessions.refresh_token_hash`). The raw token
travels only in the httpOnly cookie, never in a response body after initial
issuance.

**Token in URL.** The `guest_token` is in the URL path and therefore in browser
history and potentially in Referer headers. This is accepted (existing design,
R24.43 strips it post-consumption). The guest_token is a room-level shared
secret, not a per-session credential; knowing it grants only the ability to
create a guest session, which is the intended use.

**Guest cap.** The 50-guest cap prevents resource exhaustion via mass anonymous
session creation against a single room.

**Sender type integrity.** The `"guest"` sender type in messages prevents a guest
from impersonating a `"user"` sender. The `send_message` handler sets
`sender_type` based on `principal.is_guest`, not from the request body.

## 9. Quality Notes

**Existing debt**

- `GuestLandingView.vue`: the `if (!session.isAuthenticated)` redirect at lines
  22-27 runs in `<script setup>` (synchronous, non-lifecycle), which is fragile
  but works because `router.replace` is async. Phase 2 removes it entirely.
- `guest_service.py`: the `ON CONFLICT DO NOTHING` on enrollment (`chatroom_repo.py:866`)
  silently drops display-name updates. Kept for the registered-guest path; the anonymous
  path uses the new `guest_sessions` table where updates are explicit.

**Patterns to follow**

- JWT signing/verification: follow `jwt.py:65-102` and `jwt.py:104-145` (Vault
  Transit RS256).
- Refresh token hashing: follow `auth_service.py`'s Argon2 pattern for
  `sessions.refresh_token_hash`.
- Auth middleware branching: model on the existing `impersonated_by` check in
  `auth.py:52-60` -- a conditional path within the same middleware, not a
  separate middleware.
- WS ticket: follow `ws_auth.py:77-89` (`mint_ws_ticket`) and
  `ws_auth.py:106-136` (`authenticate_subprotocol`).
- Rate limiting: follow the pattern in `app/api/v1/auth.py` (per-IP sliding
  window via Redis).
- Label normalisation: `normalise_label()` from `shared_kernel.labels`
  (`guest_service.py:73`).
- Route meta + guard: follow `requiresAuth` pattern in `guards.ts:22-29`.

**Reuse inventory**

| What | Where | Use for |
|------|-------|---------|
| `normalise_label()` | `shared_kernel.labels` | Guest display name normalisation |
| `hmac.compare_digest` | `guest_service.py:55` | Guest token comparison |
| `SAuthCard` | `shared/ui` | Guest enrollment form card |
| `SFormField`, `SInput`, `SButton` | `shared/ui` | Form controls |
| `SLoadingSpinner` | `shared/ui` | Enrollment loading state |
| `ApiError` / `isProblemWithType` | `shared/errors` | Error classification in frontend |
| `useToast()` | `shared/composables` | Display name update confirmation |
| `VaultClient.sign_jwt` | `shared_kernel/infra/vault.py:331` | JWT signing |
| `audit.emit()` | `shared_kernel/audit.py:115` | Audit event recording |

## 10. Risks and Rollback

**Migration reversibility.** Adding a value to a PG ENUM (`ALTER TYPE ... ADD VALUE`)
is irreversible in a transaction. However, `"guest"` can remain in the enum
harmlessly if rolled back -- no existing code path produces it. The
`guest_sessions` table can be dropped cleanly (`DROP TABLE`).

**Backward compatibility.** The existing `POST /api/guest/{chatroom_id}/{guest_token}/enroll`
endpoint is preserved. Registered users who already enrolled via `chatroom_guests`
continue to access rooms as before. The new anonymous path is additive.

**Auth middleware branch.** The guest JWT branch in `AuthMiddleware` skips the
identity-context profile lookup. If a bug in the branch construction leaks a
guest principal into a non-chatroom endpoint, the endpoint will fail on missing
org/project roles (safe failure mode). Defense in depth: add an explicit
`@require_registered_user` decorator to sensitive endpoints (admin, key management)
that rejects `principal.is_guest`.

**Guest session accumulation.** `guest_sessions` rows accumulate. Mitigation: an Arq
periodic task purges sessions with `last_seen_at` older than 30 days. This is
a Phase 1 deliverable (cleanup worker).

## 11. Acceptance Criteria

### Phase 1 -- Backend core

- [x] AC-1: `POST /api/guest/{chatroom_id}/{guest_token}/session` creates a
  `guest_sessions` row and returns `{ access_token, refresh_token, guest_session_id,
  display_name, is_resuming: false }` with a 204-compatible httpOnly refresh cookie.
  No user row is created.
- [x] AC-2: The returned `access_token` is a valid JWT with `token_use: "guest_access"`,
  `rol: "guest"`, `chatroom_id`, and `display_name` claims. TTL = configured
  `guest_access_ttl_seconds`.
- [x] AC-3: Passing a `browser_id` that matches an existing session for the same
  chatroom returns `is_resuming: true` and reuses the existing `guest_session_id`.
- [x] AC-4: When the active guest count for a chatroom reaches the configured cap,
  the endpoint returns 429 with problem type `/guest/cap-reached`.
- [x] AC-5: `POST /api/guest/{chatroom_id}/refresh` with a valid refresh cookie
  returns a new `access_token` and rotates the refresh cookie.
- [x] AC-6: The auth middleware constructs a `Principal` with `is_guest=True` and
  `chatroom_id` for guest JWTs, without calling the identity context.
- [x] AC-7: A guest principal can open a WebSocket to their scoped chatroom via the
  ticket flow (`POST /api/guest/ws-ticket` -> subprotocol auth). Connection to
  any other chatroom is rejected with close code 4403.
- [x] AC-8: A guest can send a message; the resulting `messages` row has
  `sender_type = 'guest'` and `sender_id = guest_session_id`.
- [x] AC-9: Guest actions produce `audit_logs` entries with
  `actor_user_id = guest_session_id` and `metadata @> '{"guest": true}'`.
- [x] AC-10: An Arq periodic task deletes `guest_sessions` rows where `last_seen_at`
  is older than 30 days.
- [x] AC-11: The existing `POST /api/guest/{chatroom_id}/{guest_token}/enroll`
  endpoint continues to work for registered users.

### Phase 2 -- Frontend direct entry

- [x] AC-12: An unauthenticated visitor clicking a guest link sees the display-name
  form directly, without passing through Landing.vue's brand animation or the
  login page.
- [x] AC-13: After entering a display name and submitting, the visitor enters the
  chatroom within one navigation (no intermediate pages).
- [x] AC-14: The guest token is stripped from the browser URL after enrollment
  (R24.43).
- [x] AC-15: A returning guest (same browser, same chatroom) sees "Welcome back,
  {name}" with an option to change the display name.
- [x] AC-16: The guest's access JWT is refreshed silently before expiry using the
  httpOnly cookie. No user interaction required.
- [x] AC-17: The guest's WebSocket connection is established via the guest ticket
  endpoint and remains connected across JWT refreshes.
- [x] AC-18: When the guest cap is reached, the enrollment form shows a clear
  "chatroom is full" message (not a generic error).

### Phase 3 -- UX polish

- [x] AC-19: The settings gear icon is hidden for guests.
- [x] AC-20: When a guest's session expires and cannot be refreshed, an inline
  banner shows "Session expired" with a rejoin link.
- [x] AC-21: A guest can update their display name from the participant list.
- [x] AC-22: When `allow_guest_links` is toggled off while a guest is connected,
  the guest sees "Guest access has been disabled" (not a generic error).
- [x] AC-23: When a logged-in user clicks a guest link, they see a choice between
  anonymous guest entry and entering with their registered account.

## 12. Test Plan

All tests run on remote CI only (Q-6). Local test runs are not part of the
verification gate.

### Phase 1

| AC | Level | Location |
|----|-------|----------|
| AC-1 | Integration (db) | `tests/integration/conversation/test_guest_session_service.py` |
| AC-2 | Unit | `tests/unit/shared_kernel/auth/test_jwt.py` (new guest token tests) |
| AC-3 | Integration (db) | Same as AC-1 |
| AC-4 | Integration (db) | Same as AC-1 (cap enforcement) |
| AC-5 | Integration (db) | `tests/integration/conversation/test_guest_refresh.py` |
| AC-6 | Unit | `tests/unit/app/middleware/test_auth.py` (guest JWT branch) |
| AC-7 | Wiring | `tests/wiring/ws/test_guest_ws.py` |
| AC-8 | Integration (db) | `tests/integration/conversation/test_message_service.py` |
| AC-9 | Integration (db) | `tests/integration/audit/test_guest_audit.py` |
| AC-10 | Unit | `tests/unit/workers/test_guest_cleanup.py` |
| AC-11 | Integration (db) | Existing enrollment tests pass unchanged |

### Phase 2

| AC | Level | Location |
|----|-------|----------|
| AC-12..AC-18 | Component + E2E | `frontend/src/slices/conversation/__tests__/GuestLandingView.test.ts` (component), `frontend/e2e/guest-access.spec.ts` (E2E against compose stack) |

### Phase 3

| AC | Level | Location |
|----|-------|----------|
| AC-19 | Component | `frontend/src/slices/conversation/__tests__/ChatroomHeader.test.ts` |
| AC-20..AC-22 | E2E | `frontend/e2e/guest-session-lifecycle.spec.ts` |
| AC-23 | Component | `frontend/src/slices/conversation/__tests__/GuestLandingView.test.ts` |

## 13. SRS Delta

**Amend glossary (line 68):**

> **Guest** | ~~A registered Individual who has been invited into a specific Chat Room
> via a link. Has no permissions outside that room.~~ An anonymous or registered
> visitor who has been granted access to a specific Chat Room via a guest link. An
> anonymous guest holds a chatroom-scoped session without a user account; a registered
> user may also enter via a guest link using their existing account. Guests have no
> permissions outside that room.

**Amend [R5.04] (line 171):**

> **[R5.04]** ~~`Guest` is a registered Individual account that has been granted access
> to a specific Chat Room via an invite link.~~ `Guest` is a per-Chat-Room access grant
> obtained via a guest link. A guest may be (a) an anonymous visitor who holds a
> chatroom-scoped session backed by a `guest_sessions` row and a guest JWT
> (`token_use: "guest_access"`) without a user account, or (b) a registered Individual
> who enrolls via the legacy `chatroom_guests` path. Guest status is per-Chat-Room; a
> registered user who is a Guest in Room A may be a full member elsewhere. Anonymous
> guest sessions are scoped to a single chatroom; the JWT carries a `chatroom_id` claim
> enforced by the auth middleware and the room access check.

**Amend [R6.02] (line 219) -- add exception:**

> **[R6.02]** Email verification: user receives a verification token link; account is in
> `pending` state until verified. Unverified accounts cannot create Orgs/Projects nor
> accept ~~Guest invites~~ project/org invites. **Exception:** anonymous guest-link
> access ([R5.04a]) requires no account and no email verification; the guest token in
> the URL is the sole credential.

**Amend [R6.11] (line 263):**

> **[R6.11]** Chat Room Guest links (section 13): a URL token that anyone can open.
> ~~if not logged in, user is redirected to register; after registration, they join the
> room as Guest.~~ The visitor enters a display name and joins the room immediately as
> an anonymous guest. No registration or login is required. A visitor who is already
> logged in is offered a choice between anonymous guest entry and entering with their
> registered account.

**Amend [R13.06] (line 702):**

> **[R13.06]** Opening the URL ~~without login lands on the registration page with the
> token preserved; after sign-up + email verification the user is auto-joined as
> Guest.~~ presents a display-name form. On submission, the server validates the guest
> token, creates an anonymous guest session, and issues a chatroom-scoped JWT. The
> visitor enters the chatroom without registration or email verification. Browser
> identity persistence (via `localStorage browser_id`) enables returning guests to
> resume their session.

**New [R13.06a]:**

> **[R13.06a]** Anonymous guest sessions are capped at a deployment-configurable limit
> per chatroom (default 50). Active sessions are counted by `last_seen_at` within a
> 24-hour window. When the cap is reached, new enrollment returns HTTP 429 with
> problem type `/guest/cap-reached`.

**New [R13.06b]:**

> **[R13.06b]** A guest session issues a 4-hour access JWT (`token_use: "guest_access"`)
> and a 7-day httpOnly refresh cookie scoped to the chatroom's refresh endpoint path.
> Silent refresh extends the session indefinitely while the cookie survives. On full
> expiry, the guest must re-enter via the guest link.

**Amend [R24.43] (line 2008) -- extend:**

> **[R24.43]** Guest-link URLs contain the token in the path. The frontend strips
> tokens from Router history URLs after consumption (replace-state to
> `/c/<chatroom_id>`) so browsers don't leak tokens via `Referer` or history. The
> guest token is retained in memory (Pinia store) for the duration of the session to
> enable session-expiry rejoin links; it is never written to `localStorage` or any
> persistent client storage.

## 14. Open Questions

- **OQ-1: Guest message deletion.** The permission matrix (line 199) gives guests
  `own` delete on messages. The current `message_service.delete` checks
  `sender_id == principal.user_id`. For guest principals, `user_id` is
  `guest_session_id`, so this works without changes as long as the message's
  `sender_id` matches. Confirm during Phase 1 implementation.

- **OQ-2: Guest in activity sessions.** R30.26 says "a guest who satisfies the room's
  access tier is a full activity participant." The activity system checks
  `resolve_room_access()`. The Phase 1 shortcut for guest principals should cover
  this, but needs verification against activity-specific ACL checks.

- **OQ-3: Guest session cleanup granularity.** The 30-day cleanup window for
  `guest_sessions` is a starting point. If rooms accumulate significant guest
  traffic, a shorter window (7 days) may be appropriate. Monitor after deployment.

## 15. Deviation Log

- **D-1: Refresh token hash uses SHA-256, not Argon2.** The spec mentions "Argon2 hash"
  for `refresh_token_hash`, but the existing `shared_kernel.auth.tokens.hash_refresh`
  uses SHA-256 (matching `sessions.refresh_token_hash`). Followed the existing pattern
  for consistency.

- **D-2: OQ-1 confirmed and resolved.** `delete_message`'s `is_author` check hard-coded
  `sender_type.value == "user"`, which would reject a guest deleting their own message.
  Fixed to accept both `"user"` and `"guest"`.

- **D-3: Guest endpoints placed in AUTH rate-limit bucket.** The spec requires per-IP
  rate limiting matching the login endpoint. Added `/api/guest/` prefix to the AUTH
  bucket in the rate-limit middleware.

- **D-4: localStorage stores display_name alongside browser_id.** The spec lists
  `{ browser_id, guest_session_id }` for the localStorage entry. The implementation
  adds `display_name` so the "Welcome back, {name}" UI works without a probe API call
  on mount. A server-side probe would require calling `createGuestSession` with an
  empty display name, which fails validation when the browser_id does not match an
  existing session.

- **D-5: Rejoin detection is client-side, not a server probe.** The spec describes
  passing browser_id to the session endpoint on mount for rejoin detection. The
  implementation reads stored display_name from localStorage and shows the welcome-back
  UI immediately, deferring the server call to when the user clicks "Enter Chatroom".
  The server still receives browser_id and confirms the resume (or creates a new session
  if the old one expired).

- **D-6: Authenticated users use registered-guest enrollment, not anonymous session.**
  The spec says guest links always create anonymous sessions regardless of login state,
  with a choice UI in Phase 3 (AC-23). The Phase 2 implementation preserves the
  authenticated user's JWT by falling back to the existing `enrollGuest` path when
  `session.isAuthenticated` is true. Without this, `setAccessToken(guestJWT)` would
  silently overwrite the user's access token, breaking their session for non-chatroom
  endpoints. Phase 3's choice UI replaces this guard.

## 16. Follow-ups

- **FU-1: Guest session analytics.** Dashboard for room owners showing guest session
  counts, peak concurrency, and display-name history. Not in scope for this task.
- **FU-2: Guest-to-user upgrade.** Allow an anonymous guest to convert their session
  into a registered account, preserving message history. Requires linking
  `guest_sessions.id` to `users.id` and migrating `sender_id` references.
- **FU-3: Per-room guest cap override.** Allow room owners to set a custom guest cap
  (above or below the deployment default). Requires a `max_guests` column on
  `chatrooms` and a settings UI field.
- **FU-4: Guest link QR code.** Generate a QR code for the guest link URL in the
  settings panel, for classroom/workshop scenarios where participants scan from a
  projector.
- **FU-5: browser_id partial index should be UNIQUE.** The Phase 1 migration
  (`0085_guest_sessions.py:79`) creates a non-unique partial index on
  `(chatroom_id, browser_id)`. Two concurrent requests from the same browser can
  insert duplicate rows, causing `find_by_browser_id`'s `one_or_none()` to raise
  `MultipleResultsFound`. Fix: make the partial index unique.
- **FU-6: Separate rate-limit bucket for guest ws-ticket.** The `/api/guest/` prefix
  is in the AUTH rate-limit bucket (D-3), so the authenticated `ws-ticket` endpoint
  competes with anonymous session creation. Behind NAT, active guests' WS reconnects
  can starve new session creation. The ws-ticket endpoint should have its own bucket.
- **FU-7: guest_session_service redundant update_last_seen on resume.** The resume
  path calls `update_last_seen` immediately before `update_refresh_hash`, which also
  sets `last_seen_at`. One extra DB write per resume.
- **FU-8: guest_ws_ticket token extraction uses partition() without strip().** If a
  reverse proxy normalizes the Authorization header with extra whitespace, the
  `partition(' ')` extraction in `guest_ws_ticket` captures leading whitespace that
  the middleware's `split+strip` does not, contaminating the Redis-stashed token.
- **FU-9: ChatroomPresence shows truncated UUIDs for other guests.** The presence
  panel resolves the current guest's display name from JWT claims, but other online
  guests still show as truncated UUIDs. Resolving all guest names requires a new
  endpoint or extending the members query to include guest sessions.
- **FU-10: Component test for canSettings prop on ChatroomHeader.** AC-19 is
  implemented but the test plan's ChatroomHeader.test.ts coverage for the new
  `canSettings` prop is not yet written.
- **FU-11: Component test for authenticated choice card in GuestLandingView.** AC-23
  is implemented but the test plan's GuestLandingView.test.ts coverage for the
  `'choosing'` state and `chooseGuest`/`chooseOwnAccount` paths is not yet written.
