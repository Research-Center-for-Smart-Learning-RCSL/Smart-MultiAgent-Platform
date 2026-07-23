---
type: feature
status: draft
created: 2026-07-23
requirements: [R6.01, R6.02, R6.03, R6.13, R19.01, R19a.12]
depends_on: []
---

# Sign in with Google (OAuth/OIDC) as a login method

## 1. Summary

Add "Sign in with Google" (Google as an OpenID Connect provider, Authorization Code flow
with PKCE) as a second authentication method alongside the existing email/password login,
plus an account-linking surface so a logged-in user can link or unlink their Google
account from their profile page. Google login can authenticate an existing user, link to
an existing user (by verified email), or provision a brand-new account. Password login is
unchanged and remains available for every account that has a password. This directly
reverses the v1 non-goal at `REQUIREMENTS.md:51` ("SSO, OAuth, or MFA in v1"), so it
carries a non-empty SRS Delta (§13).

## 2. Goals and Non-goals

**Goals**
- A user can click "Sign in with Google" on the login page and, on success, land logged in
  with the same session artifacts a password login produces (RS256 access token in body,
  `smap_refresh` cookie, Redis session, DB session mirror row).
- A first-time Google user with no existing account is provisioned as `ACTIVE` +
  `email_verified=true` (Google has already verified the email), skipping the email
  verification flow.
- A Google login whose email matches an existing account is bound to that account per the
  collision policy in Q-2 (auto-link to already-verified accounts; bind-and-neutralize for
  unverified accounts).
- A logged-in user can link a Google account and later unlink it from their profile page,
  with an unlink guard that prevents locking themselves out.
- Every OAuth outcome (login, provision, link, unlink, and the security-relevant rejects)
  is audit-logged following the existing `auth.*` pattern.

**Non-goals**
- Any provider other than Google (no GitHub/Microsoft/Apple). The data model is built to
  extend to them (Q-3), but no second provider is implemented here.
- MFA / 2FA. Explicitly still out of v1 scope; only the "OAuth" clause of
  `REQUIREMENTS.md:51` is reversed, not the MFA clause.
- Google Workspace domain restriction (the `hd` claim). Any Google account is accepted
  (Q-4). No allowlist coupling to `email_domain_policy` in this task.
- Merging two pre-existing distinct accounts (e.g. a password account under email A and a
  separate account whose Google email is A). The link flow binds a Google identity to one
  user; it does not merge user rows.
- Changing the password-login flow, the refresh/rotation machinery, or the per-request
  auth middleware. OAuth reuses all of them unchanged.
- Migrating the existing `smap.local` issuer / cookie model to a cross-site OAuth cookie.
  The callback completes server-side and same-origin (§5), so `SameSite=lax` is retained.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Scope: login only, linking only, or both? | Both — Google login (incl. new-account provisioning) on the login page **and** link/unlink from the profile page. | User wants the complete experience; the two flows share the same identity table and callback machinery, so building both together avoids reworking the seam twice. |
| Q-2 | How to handle an email collision (Google email equals an existing account's email)? | Auto-link. **Refined for safety:** if the existing account is `email_verified=true`, auto-link and log in. If it is `email_verified=false` (e.g. a never-verified `pending` registration), bind the identity, set `email_verified=true`, **and neutralize the existing password** (force a reset) rather than trusting an unproven password. | Auto-link is the smoothest UX (user's stated preference). The unverified-account refinement closes a pre-hijack account-takeover vector: an attacker who pre-registers a victim's email as an unverified account would otherwise inherit a session the real email owner triggers via Google. Google's `email_verified` proves ownership; the stale password does not. |
| Q-3 | Data model: separate identities table vs nullable password column on `users`? | Separate `auth_identities` table (`user_id, provider, provider_subject, email`), one row per linked provider. | Extensible to future providers with no schema churn; keeps `users` focused. Password stays on `users` (made nullable, §6) so a Google-only account has `password_hash = NULL`. |
| Q-4 | Restrict Google login to a Workspace domain (`hd` claim)? | No restriction — any Google account is accepted. | BYO-key self-hosted platform serves general users; a domain fence can be added later without reworking this. |
| Q-5 | Provisioning-time `display_name`: use the Google profile name? | Only when the account has no `display_name` yet (new account, or existing account with a null/empty name). Never overwrite a user-set name. | Respects a name the user chose; still gives new accounts a sensible default. |
| Q-6 | Does this depend on any active dossier under `docs/tasks/`? | No. `depends_on: []`. | Dependency scan (§4) found no non-`implemented` dossier that touches the identity login/session/`users`/`sessions` surface; the ~40 active dossiers are in activities, workflow, a2a, conversation, and admin areas. No logical or overlap prerequisite. |

## 4. Current State

Authentication lives entirely in the `identity` context; there is **no** OAuth/OIDC/social
infrastructure today (confirmed: no client-id/OAuth section anywhere in
`backend/app/config/settings.py`, no provider table).

**Login + session issuance.** `AuthService.login` (`backend/contexts/identity/application/auth_service.py:226-348`)
verifies the password (`auth_service.py:254`), runs the status gate
(DELETED→`AccountDeleted` `auth_service.py:293-294`, BANNED→`AccountBanned` `:295-296`,
unverified→`AccountNotVerified` `:299-300`), then mints the session **inline** at
`auth_service.py:302-325`:
- access token via `jwt.sign_access_token` (`shared_kernel/auth/jwt.py:65-102`, RS256 signed
  through Vault Transit key `smap-jwt-sign`, `shared_kernel/infra/vault.py:331-344`),
- refresh token + Redis session via `tokens.create_session` (`shared_kernel/auth/tokens.py:69-93`),
- DB session mirror row via `SessionRepository.insert` (`repositories.py:200-227`),
- `mark_logged_in` (`repositories.py:171-172`), then `auth.login.success` audit
  (`auth_service.py:327-338`), returning `LoginOutcome` (`auth_service.py:81-85`).

There is **no** standalone "issue a session for user X" seam today — the mint block is
password-coupled inside `login`. Extracting `auth_service.py:302-348` into a private helper
is the highest-value refactor for this feature (§5, §6).

**Routes.** `backend/app/api/v1/auth.py`, prefix `/api/auth` (`auth.py:37`). Services are
built with `_service(db)` → `create_auth_service(db, public_origin=_public_origin())`
(`auth.py:66-67`); `_public_origin()` returns `cors_origins[0]` or the localhost fallback
(`auth.py:70-73`). Login sets the refresh cookie with `_set_refresh_cookie`
(`auth.py:43-53`, `:311`); cookie name `smap_refresh`, path `/api/auth`, `HttpOnly`,
`Secure` (default), `SameSite=lax` (`auth.py:43-53`, `settings.py:213-218`). The access
token is returned in the JSON body (`TokenPairOut` `auth.py:153-159`), not a cookie.
Architecture note: the auth router calls `AuthService` directly rather than through
`IdentityFacade` (only `get_profile` goes through the facade, `auth.py:481`) — this is the
sanctioned local convention an OAuth endpoint follows.

**Users table + constraint.** `users.password_hash` is `NOT NULL`
(`backend/alembic/versions/0001_identity.py:32`). `email` is `CITEXT`
(`0001_identity.py:50`). `uq_users_email_active` is a partial unique index
`ON users (email) WHERE deleted_at IS NULL` (`0001_identity.py:53-56`), enforcing one
active account per email at the DB level — the invariant a "link by email" flow must
respect. `UserRepository.get_active_by_email` already filters soft-deleted rows
(`repositories.py:69-80`); `mark_verified` promotes PENDING→ACTIVE (`repositories.py:152-169`).

**Per-request enforcement (reused unchanged).** `app/api/middleware/auth.py` verifies the
token (`:46`), checks the jti denylist (`:56`), and rejects DELETED/missing→401 (`:62-63`)
and BANNED→403 (`:64-65`) on every request. Any session minted through the shared seam is
automatically covered — a subsequently-banned Google user is rejected on the next request
with no extra work.

**Token-with-state precedent.** The `_TokenRepo` pattern (`repositories.py:319-373`,
`issue`/`consume`) backs email-verify and password-reset tokens — the model to mirror if
OAuth `state`/nonce needs DB persistence (though Redis with a short TTL is the lighter
option, §5).

**Frontend.** Identity slice at `frontend/src/slices/identity/`. Login page
`views/LoginView.vue` (route `routes.ts:13`), register `views/RegisterView.vue`
(`routes.ts:7`). Session state + actions in the Pinia store `stores/session.ts`
(`login` `:24-30`, `applyTokens` `:18-22`, `refreshMe` `:32-34`, `hydrate` `:60-71`).
The hand-written API wrapper `api/auth.ts` (`:82-131`) wraps the generated `AuthService`;
the `Me` type is at `api/auth.ts:35-42` and has no provider field today. The
**callback-route precedent** is `VerifyEmailView.vue:16-41`: a public
(`requiresAuth:false`) route that reads a token from the URL on mount and posts it to the
backend — the exact shape for a Google callback view. Post-auth redirect uses
`safeRedirect` (`LoginView.vue:90`); problem-detail error branching at `LoginView.vue:96-116`.
Account management is split under `/account/*` (`routes.ts:34-63`), reached via
`app/components/UserMenu.vue`; `ProfileView.vue` (`:55-58`) is the account-info page and the
natural home for a connections/link section. Generated client regenerated with
`pnpm run gen:api` (`package.json:14`); `AuthService.ts` is generated ("do not edit",
`AuthService.ts:1`), `api/auth.ts` is hand-written and must be extended manually. i18n keys
live in `slices/identity/locales/en.json` + `zh-TW.json`; app-shell/menu strings in
`app/locales/*`. Shared `SButton` (`shared/ui/SButton.vue`) supports an `icon-left` slot and
can render as an anchor.

## 5. Design

### Options considered

**Seam — where OAuth mints the session.**
- **Option A — Extract a shared `_establish_session(user, *, remote_ip, user_agent, request_id)` helper** from `auth_service.py:302-348`; `login` calls it after credential+status checks, and a new `login_with_oauth` calls the status gate (`:293-300`) then the same helper. One mint path, one audit shape, no duplication.
- **Option B — Duplicate the mint block** in an OAuth method. Faster to write, but forks the session-issuance logic (RS256 + refresh + Redis + DB mirror + audit) into two places that will drift.

**Data model.**
- **Option A — `auth_identities` table** (Q-3), `password_hash` made nullable. One user ↔ many provider identities; password is just another (optional) credential.
- **Option B — nullable `google_sub`/`google_email` columns on `users`**. Simpler now, but every future provider adds columns and collision logic to the hot `users` table.

**OAuth transaction state (`state`/PKCE verifier/nonce).**
- **Option A — Redis with short TTL** (mirrors the session/rate-limit keyspaces already in `tokens.py:41-44`), keyed by `state`, holding the PKCE `code_verifier`, `nonce`, `mode` (login|link), and (for link) the initiating `user_id`.
- **Option B — a DB `oauth_states` table** mirroring `_TokenRepo`. Durable but heavier; OAuth round-trips are seconds-long, so a TTL'd Redis entry is the right lifetime.

### Decision

**Option A on all three.** Extract `_establish_session`, add `auth_identities` (+ nullable
`password_hash`), and hold OAuth transaction state in Redis with a short TTL (e.g. 10 min).

Rationale: the session-issuance seam is the crux — extracting it once makes the RS256
access token + rotating refresh + Redis session + DB mirror + `auth.login.success` audit
reusable by definition, and guarantees a Google session is indistinguishable downstream
from a password session (same middleware, same refresh, same revocation). The identities
table is the only model that stays clean when a second provider is added later
(explicitly wanted, Q-3). Redis state matches the ephemeral lifetime of an OAuth
round-trip and reuses infrastructure already in the auth path.

**Flow (Authorization Code + PKCE, all server-side):**

1. `GET /api/auth/google/authorize?mode=login|link` → generate `state` + PKCE
   `code_verifier`/`code_challenge` + `nonce`; store `{code_verifier, nonce, mode, user_id?}`
   in Redis under `state` (short TTL); 302 to Google's authorization endpoint with
   `redirect_uri` built from `_public_origin()` (`auth.py:70-73`). `mode=link` requires an
   authenticated caller; the current `user_id` is captured into the state.
2. Google redirects the browser to the SPA callback route, which reads `code` + `state`
   from the URL and POSTs them to `POST /api/auth/google/callback`.
3. Callback: load+delete the Redis state by `state` (single-use; missing/expired → 400);
   exchange `code` for tokens against Google's token endpoint using the stored
   `code_verifier`; verify the `id_token` JWT against Google's JWKS (`iss` ∈
   {`accounts.google.com`, `https://accounts.google.com`}, `aud` = configured client id,
   `exp` valid, `nonce` matches the stored nonce); extract `sub`, `email`, `email_verified`,
   `name`.
4. Resolve the user (see resolution table below), run the status gate
   (`auth_service.py:293-300`) against the resolved/created user, then call
   `_establish_session`. For `mode=login` the callback sets the `smap_refresh` cookie
   (`_set_refresh_cookie`, `auth.py:43-53`) and returns `TokenPairOut`, identical to `login`.
   For `mode=link` the callback creates the `auth_identities` row for the captured `user_id`
   and returns the updated link status (no new session needed — the caller is already
   logged in).

**User resolution (callback, `mode=login`):**

| Situation | Action |
|---|---|
| `auth_identities(provider='google', provider_subject=sub)` exists | Log in that user (status gate first). |
| No identity; `get_active_by_email(email)` finds a user with `email_verified=true` | Auto-link: insert identity, log in (Q-2). |
| No identity; `get_active_by_email(email)` finds a user with `email_verified=false` | Bind: insert identity, `mark_verified`, **neutralize the existing password** (set to unusable / require reset), log in (Q-2 refinement). |
| No identity; no active user for that email | Provision: insert `users` row with `password_hash=NULL`, `email_verified=true`, `status=ACTIVE`, `display_name` from Google if the account has none (Q-5); insert identity; log in. |

**Unlink guard (`mode=link`, unlink):** refuse to unlink the last remaining credential — a
user with `password_hash IS NULL` and no other `auth_identities` row must set a password
before unlinking Google (returns an RFC 7807 error the UI renders).

## 6. Detailed Changes

**Backend** (`identity` context)
- **Migration (new alembic revision):** add `auth_identities` table
  (`id uuid pk, user_id uuid fk users(id) on delete cascade, provider text,
  provider_subject text, email citext, created_at timestamptz`), with
  `UNIQUE(provider, provider_subject)`, `UNIQUE(user_id, provider)`, and index on
  `user_id`; **alter `users.password_hash` to nullable**. Reversibility in §10.
- **`infrastructure/tables.py`** — add the `auth_identities` Table; mark `password_hash`
  nullable to match the migration (per memory `[[reference_orm_enum_type_match]]`, the ORM
  column must match the migration or asyncpg errors).
- **`infrastructure/repositories.py`** — new `AuthIdentityRepository`
  (`get_by_provider_subject`, `insert`, `list_for_user`, `delete(user_id, provider)`);
  extend `UserRepository` with an OAuth-provision insert that accepts
  `password_hash=None, email_verified=True, status=ACTIVE` and a password-neutralize path
  (reuse `set_password` semantics, `repositories.py:82-92`).
- **`application/auth_service.py`** — extract `_establish_session` from `:302-348`; add
  `login_with_oauth(profile, *, remote_ip, user_agent, request_id)` implementing the
  resolution table; add `link_google`/`unlink_google(user_id, ...)`. New audit actions
  (see §7).
- **New `application/oauth_service.py` (or `infrastructure/oauth/google.py`)** — the Google
  OIDC adapter: authorize-URL construction, PKCE, code exchange, `id_token` JWKS
  verification (`iss`/`aud`/`exp`/`nonce`), Redis state store. No secret read here beyond
  the Vault-sourced client secret (mirror the SMTP factory pattern, `factory.py:31,53`).
- **`interfaces/facade.py`** — optionally expose the link-status read for `get_profile`
  (so `UserProfile`/`UserOut` can carry linked-provider info). Following the existing auth
  convention, the callback endpoint may talk to `AuthService` directly.

**API contract** (`app/api/v1/auth.py`) — `gen:api` rerun **required**.
- `GET /api/auth/google/authorize` (302 redirect; `mode` query).
- `POST /api/auth/google/callback` (body `{code, state}`) → `TokenPairOut` for login mode;
  sets `smap_refresh` cookie like `login` (`auth.py:311`).
- `POST /api/auth/google/link` / `DELETE /api/auth/google/link` (authenticated) → link
  status.
- `GET /api/auth/identities` (authenticated) → list linked providers, for the profile UI.
- Add a `google_linked`/provider field to `UserOut` (`auth.py:174-180`) and `UserProfile`.
- `authorize`, `callback` are **unauthenticated** endpoints → must be added to the
  `R19.01` exception list (SRS Delta §13) and rate-limited like the other public auth
  endpoints.

**Frontend** (`identity` slice) — after backend `openapi.json` changes + `pnpm run gen:api`.
- `api/auth.ts` — add `googleAuthorize`/`googleCallback`/`linkGoogle`/`unlinkGoogle`
  wrappers; extend `Me` (`:35-42`) + `toMe` (`:55-64`) with link status.
- `stores/session.ts` — optional `loginWithGoogle` action reusing `applyTokens` + `refreshMe`
  (keeps the WebSocket/query-cache lifecycle identical to password login).
- `views/LoginView.vue` (+ optionally `RegisterView.vue`) — a "Sign in with Google"
  `SButton variant="secondary"` with an inline Google "G" SVG in the `icon-left` slot (no
  heroicons Google mark exists; supply a custom SVG). Clicking navigates full-page to
  `/api/auth/google/authorize?mode=login` via `window.location.assign`.
- New `views/GoogleCallbackView.vue` modeled on `VerifyEmailView.vue:16-41` — reads
  `code`/`state`, calls the store, `safeRedirect`s; add a public route in `routes.ts`
  (mirror `verify-email` meta `requiresAuth:false, layout:'auth'`).
- `views/ProfileView.vue` — a connections section showing linked Google account + link/
  unlink buttons (link navigates to `authorize?mode=link`); unlink calls the API and
  surfaces the last-credential guard error.
- i18n — new keys in `slices/identity/locales/en.json` + `zh-TW.json`
  (`identity.login.googleSignIn`, `identity.profile.connections.*`, error keys); any
  `UserMenu` entry needs `app/locales/*`.

**Deploy/config**
- New `OAuthSection` in `settings.py`: `google_client_id`, `google_redirect_path` (or full
  redirect URI derivation from `_public_origin()`), `enabled` flag. **`google_client_secret`
  is NOT an env var** — read from Vault KV `secret/smap/config/google_oauth` following the
  SMTP precedent (`factory.py:31,53`).
- Google Cloud Console: an OAuth 2.0 Client (Web application) with the authorized redirect
  URI pointing at the SPA callback route on each origin (staging `smap.rcsl.online`, prod).
- Startup: log a single warning if `enabled` but client id/secret unconfigured (mirror
  `warn_if_email_unconfigured`, `factory.py:98-110`).

## 7. NFR Checklist

- [x] i18n — all new strings via `$t()`; keys added to both `en.json` and `zh-TW.json`
  (identity slice) and `app/locales/*` if the user menu changes. ESLint bans bare literals.
- [x] Audit log — new actions following `auth_service.py:327-338`: `auth.oauth.login.success`
  (metadata: `provider`, `session_id`, email digest), `auth.oauth.provisioned`,
  `auth.oauth.account_linked`, `auth.oauth.account_unlinked`, and reject events
  (`auth.oauth.login.rejected` for banned/deleted). Email never logged raw — always
  `recipient_digest` (`auth_service.py:39`).
- [x] Tenant isolation — N/A for authorize/callback (pre-tenant identity endpoints, like
  register/login). `link`/`unlink`/`identities` are self-scoped to the authenticated
  `user_id`; no org/project data crosses.
- [x] Error handling UX — RFC 7807 problem details for: state expired/invalid, Google
  denied/`error` param, `email_verified=false` from Google (reject — do not provision an
  unverified identity), account banned/deleted, provider-already-linked-to-another-user
  (409), last-credential unlink guard. Login page reuses the `isProblemWithType` branching
  (`LoginView.vue:96-116`); callback view shows loading/error states like
  `VerifyEmailView.vue`.
- [x] Performance — O(1) per auth: one Redis get/del for state, one JWKS verification
  (JWKS cached), one-to-two indexed user/identity lookups. No new list endpoints beyond a
  per-user identities read (bounded, ≤ number of providers). No N+1.

## 8. Security Considerations

Touches auth, session issuance, and account identity — full treatment required.

- **Account takeover via unverified pre-registration (primary risk).** Handled by the Q-2
  refinement: never auto-log-in to an `email_verified=false` account; bind + verify +
  neutralize the stale password. Without this, an attacker pre-registering the victim's
  email would inherit the session the victim's Google login creates.
- **`email_verified=false` from Google.** Reject provisioning/linking when Google itself
  reports the email unverified — a Google account can carry an unverified email; treating
  it as proof of ownership would reintroduce the takeover vector.
- **CSRF on the OAuth round-trip.** `state` is single-use, random, stored server-side, and
  compared on callback; the Redis entry is deleted on read. `mode=link` binds the state to
  the initiating `user_id` so a callback cannot link an attacker's Google account to a
  victim's session.
- **`id_token` validation.** Verify signature against Google JWKS, and check `iss`, `aud`
  (must equal our client id — blocks token substitution from another Google app), `exp`,
  and `nonce` (replay defense). Never trust `email`/`sub` from the token endpoint response
  body without id_token verification.
- **Authorization Code + PKCE** (not implicit), server-side code exchange; the client
  secret stays server-side (Vault), never reaches the SPA.
- **Redirect URI** is exact-match registered in Google Console and derived from
  `_public_origin()`; no open-redirect via a user-supplied `redirect_uri`. The post-login
  in-app redirect continues to use `safeRedirect` (allowlisted, same-origin).
- **Ban/lockout/deleted.** OAuth runs the same pre-issue status gate
  (`auth_service.py:293-300`) and the shared per-request middleware guard
  (`middleware/auth.py:62-65`) — a banned user cannot enter via Google, and a
  post-ban session is killed on next request. OAuth does not bypass `[R6.13]`.
- **Password neutralization.** When binding to an unverified account, invalidate existing
  sessions/refresh tokens for that user (reuse the password-change revocation path implied
  by `[R6.06]`) so any session an attacker may hold is dropped.
- **No secret logging.** Client secret, `code`, `code_verifier`, `id_token`, and
  access/refresh tokens are never logged (project constraint; redaction filter exists at
  `shared_kernel/logging/redaction.py`).
- **Rate limiting.** `authorize`/`callback` are public — rate-limit per IP like the other
  `R19.01` exceptions.

## 9. Quality Notes

**Existing debt (do not imitate; do not silently fix)**
- The session-mint logic is inlined in `login` (`auth_service.py:302-348`) with no reuse
  seam. This task's extraction of `_establish_session` is the sanctioned fix; keep the
  behavior identical (characterization: existing login tests must stay green).
- The auth router bypasses `IdentityFacade` and calls `AuthService` directly
  (`auth.py:66-67`) — a known deviation from the CLAUDE.md facade rule. Follow the existing
  convention here rather than "fixing" it in this task; record no new deviation.
- `_public_origin()` derives the base URL from `cors_origins[0]` (`auth.py:70-73`) rather
  than a dedicated setting. Reuse it for the redirect URI; do not introduce a second
  origin source. (FU candidate if OAuth exposes it as fragile — see §16.)

**Patterns to follow**
- Session issuance: `auth_service.py:302-338` (the exact mint + audit sequence).
- Public token-consuming route (backend): `verify_email` (`auth.py:280-292`) +
  `_TokenRepo` (`repositories.py:319-373`).
- Public callback view (frontend): `VerifyEmailView.vue:16-41`.
- Vault-sourced secret with logging-fallback: the SMTP factory (`factory.py:31-70`,
  `98-110`).
- Audit emission: `auth_service.py:327-338`.

**Reuse inventory**
- Backend: `jwt.sign_access_token`, `tokens.create_session`, `tokens.hash_refresh`,
  `SessionRepository.insert`, `UserRepository.get_active_by_email`/`mark_verified`/
  `mark_logged_in`/`set_password`, `_set_refresh_cookie`, `_public_origin`, `audit.emit`,
  `recipient_digest`, the status gate at `auth_service.py:293-300`, the per-request guard
  (`middleware/auth.py`, no change).
- Frontend: `SButton` (anchor + `icon-left` slot), `SAuthCard`, `SAlert`, `AuthLayout`,
  `safeRedirect`, `isProblemWithType` branching, `session.applyTokens`/`refreshMe`, the
  `verify-email` route/view shape.

## 10. Risks and Rollback

- **Migration reversibility.** Forward: create `auth_identities`, alter `password_hash` to
  nullable. Down: drop `auth_identities`; re-tightening `password_hash` to `NOT NULL` is
  only safe if no Google-only (`password_hash IS NULL`) accounts exist — the down migration
  must guard on that (or backfill an unusable sentinel) rather than blindly re-adding the
  constraint. Documented in the revision.
- **`password_hash` nullability blast radius.** Any code assuming a non-null hash (e.g.
  the login dummy-hash path, `auth_service.py:247`) must handle password-less accounts:
  a Google-only user attempting password login must get `invalid credentials` (or a
  "use Google" hint), not a 500. Covered by AC-8.
- **Misconfiguration parity with SMTP.** If `enabled` but client id/secret unset, fail
  closed on the OAuth endpoints (return a clear error / hide the button via config) and log
  a startup warning — do not 500. Mirrors the SMTP `warn_if_email_unconfigured` posture.
- **Cookie `SameSite`.** The callback completes via a same-origin POST from the SPA (not a
  cross-site top-level POST), so `SameSite=lax` remains valid; `settings.py:214-217`
  disallows `none`. Verify the callback is reached same-origin on staging before prod.

## 11. Acceptance Criteria

- [ ] AC-1: `GET /api/auth/google/authorize?mode=login` returns a 302 to Google with a
  `state`, `code_challenge` (PKCE S256), `nonce`, and a `redirect_uri` derived from
  `_public_origin()`; the `state` entry is stored in Redis with a bounded TTL.
- [ ] AC-2: A valid callback for a Google `sub` that has never been seen and whose email
  matches no active account provisions a `users` row with `password_hash IS NULL`,
  `email_verified=true`, `status=ACTIVE`, inserts an `auth_identities` row, sets the
  `smap_refresh` cookie, returns a `TokenPairOut`, and emits `auth.oauth.provisioned` +
  `auth.oauth.login.success`.
- [ ] AC-3: A callback whose `sub` already has an `auth_identities` row logs that user in
  (new session) without creating a duplicate identity or user.
- [ ] AC-4: A callback whose email matches an existing `email_verified=true` account (no
  prior identity) auto-links (inserts the identity) and logs in; the existing password
  still works afterward.
- [ ] AC-5: A callback whose email matches an existing `email_verified=false` account binds
  the identity, sets `email_verified=true`, neutralizes the old password (subsequent
  password login with the old password fails), invalidates that user's existing sessions,
  and logs in.
- [ ] AC-6: A callback where the verified id_token reports `email_verified=false` is
  rejected (no user/identity created), with an RFC 7807 error.
- [ ] AC-7: A callback with an expired/unknown/reused `state`, or an id_token failing
  `iss`/`aud`/`exp`/`nonce`/signature checks, is rejected with a 400 and no session minted.
- [ ] AC-8: A Google-only account (`password_hash IS NULL`) attempting `POST /api/auth/login`
  receives an invalid-credentials error, never a 500.
- [ ] AC-9: A BANNED (and separately, DELETED) account cannot obtain a session via the
  Google callback — the status gate rejects it, and `auth.oauth.login.rejected` is emitted.
- [ ] AC-10: An authenticated user can link Google (`mode=link`): the identity binds to
  their own `user_id`, and `auth.oauth.account_linked` is emitted. Attempting to link a
  Google account already bound to a different user returns 409.
- [ ] AC-11: Unlinking Google succeeds when the user still has a password or another
  identity; unlinking the last remaining credential (no password, no other identity) is
  refused with an RFC 7807 error and no state change.
- [ ] AC-12: A session minted via Google is indistinguishable to the per-request middleware
  from a password session (refresh, revocation via jti denylist, ban-on-next-request all
  behave identically).
- [ ] AC-13: The login page renders a "Sign in with Google" button (i18n via `$t`, present
  in both locales) that navigates to `authorize`; the callback view handles loading/error
  states; the profile page shows link/unlink with the current linked status.

## 12. Test Plan

- **Unit (backend, `tests/unit/`)** — resolution-table logic in `login_with_oauth`
  (AC-2..AC-6, AC-9): fake `AuthIdentityRepository`/`UserRepository`, assert the branch
  taken and the audit action, mirroring `tests/unit/test_auth_service.py`. id_token
  verification (AC-7) with a stubbed JWKS: valid, wrong `aud`, expired, bad `nonce`, bad
  signature. `_establish_session` characterization: existing login tests stay green after
  the extraction. Password-neutralize + password-login-of-nullable-hash account (AC-5,
  AC-8).
- **Integration (`tests/integration/`, Postgres+Redis)** — the migration applies and
  rolls back; `authorize` stores state; a mocked Google token/JWKS drives a full callback
  producing a real session row + cookie (AC-1..AC-4, AC-10..AC-12); state single-use/expiry
  (AC-7). Model on `tests/integration/test_auth_login_refresh.py`.
- **Frontend (component)** — LoginView renders the Google button (both locales);
  GoogleCallbackView posts `code`/`state` and redirects on success / shows error on failure;
  ProfileView link/unlink states incl. the last-credential guard error (AC-11, AC-13).
- **Manual (`/run` or `frontend:verify`)** — end-to-end against a real Google test client
  on staging (`smap.rcsl.online`): new-account provision, link from profile, unlink guard.

## 13. SRS Delta

Apply verbatim on approval.

**Amend `REQUIREMENTS.md:51`** (§1 Non-goals) from:
```
- SSO, OAuth, or MFA in v1 (email + password only).
```
to:
```
- MFA in v1. (Google OAuth login is in scope as of §6.1a; other SSO/OIDC providers remain out of scope.)
```

**Add new subsection §6.1a "Google sign-in" after `[R6.08]`:**
```
### 6.1a Google sign-in (OAuth/OIDC)

- **[R6.14]** Users may authenticate with Google via the OpenID Connect Authorization
  Code flow with PKCE, as an alternative to email+password. The client secret is stored in
  Vault KV (`secret/smap/config/google_oauth`), never on the application filesystem or in
  env. `authorize` and `callback` are unauthenticated, rate-limited endpoints (see R19.01).
  The `id_token` is verified for signature (Google JWKS), `iss`, `aud` (our client id),
  `exp`, and `nonce`; `state` is single-use and CSRF-binding.
- **[R6.15]** A successful Google authentication issues the same session artifacts as
  R6.03 (RS256 access token, rotating refresh token, Redis session, jti revocation) and is
  subject to the same ban/lockout/deleted gates (R6.04, R6.13). A first-time Google user
  with no existing account is provisioned directly as `active` + `email_verified=true`,
  bypassing R6.02 email verification; any Google account is accepted (no Workspace-domain
  restriction).
- **[R6.16]** Email-collision binding: a Google login whose verified email matches an
  existing account is bound to that account. If the existing account is already
  email-verified, the Google identity is auto-linked. If it is not yet verified, the
  identity is linked, the account is marked verified, and the existing password is
  invalidated (a reset is required to reuse password login), closing the pre-registration
  takeover vector. A Google `id_token` reporting `email_verified=false` is rejected.
- **[R6.17]** A logged-in user may link and unlink a Google identity from their profile.
  One Google account (`sub`) maps to at most one user; a user has at most one Google
  identity. Unlinking is refused when it would remove the account's only remaining
  credential (no password and no other linked identity) until a password is set. Link and
  unlink are audit-logged (`auth.oauth.account_linked` / `auth.oauth.account_unlinked`).
```

**Amend `[R19.01]`** (`REQUIREMENTS.md:870`) to extend the unauthenticated-exception list:
```
- **[R19.01]** Every HTTP endpoint **requires authentication** (Q57). Exceptions:
  `/api/auth/register`, `/api/auth/login`, `/api/auth/request-password-reset`,
  `/api/auth/verify-email`, `/api/auth/google/authorize`, `/api/auth/google/callback` — all
  rate-limited aggressively.
```

**Amend the audit-events table** (`REQUIREMENTS.md:836`, Auth row) to add:
`auth.oauth.login.success`, `auth.oauth.provisioned`, `auth.oauth.account_linked`,
`auth.oauth.account_unlinked`, `auth.oauth.login.rejected`.

## 14. Open Questions

- OQ-1: Whether to expose a config toggle to later restrict Google login to a Workspace
  domain (`hd`). Not built now (Q-4); noted so the `OAuthSection` shape can leave room.
- OQ-2: Whether the neutralized-password path (AC-5) should also send the affected user a
  notification email ("your account was linked to Google; reset your password to use it").
  Recommended, but a copy/notification decision, not a blocker.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: `_public_origin()` deriving the base URL from `cors_origins[0]` (`auth.py:70-73`)
  is fragile once OAuth depends on it for a registered redirect URI. Consider a dedicated
  `public_origin` setting in a later task. Not fixed here.
- FU-2: The `auth_identities` model is provider-generic; adding GitHub/Microsoft is a
  future task that reuses the seam, adapter interface, and UI section built here.
