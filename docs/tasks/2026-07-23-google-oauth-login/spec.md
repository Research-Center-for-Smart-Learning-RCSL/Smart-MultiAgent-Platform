---
type: feature
status: in-progress
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
| Q-7 | Callback topology: Google → SPA route that POSTs `code` to the backend, or Google → backend GET callback that exchanges server-side, sets the refresh cookie, and 302s to the SPA? | Backend-handled GET + 302 + `hydrate()`. | Reuses the existing `smap_refresh` cookie + `session.hydrate()` machinery with no new token plumbing; keeps `code`/tokens out of the SPA URL/history; smaller frontend surface; and gives the login-CSRF state cookie (Q-8) a natural home. Surfaced by the red-team review. |
| Q-8 | The link flow is authenticated, but a full-page navigation to a GET endpoint carries no bearer header, and login-CSRF is unmitigated by Redis-only state. How are both fixed? | Link is initiated by an authenticated **XHR** `POST /google/link/start` that returns the authorize URL (bearer works on XHR); the callback recovers `user_id` from the Redis state, not from request auth. A short-lived `smap_oauth_state` cookie (`SameSite=Lax`) is set at authorize and compared on callback to bind the browser. | The bearer token lives in a JS in-memory ref attached only by the axios interceptor (`shared/transport/axios.ts`); a top-level navigation cannot send it, and `middleware/auth.py` reads only the bearer header (no cookie fallback). The state cookie closes the login-CSRF hole (attacker fixing the victim's Google account). Both surfaced by the red-team review. |

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
round-trip and reuses infrastructure already in the auth path. The callback topology and
the authenticated-link + login-CSRF handling below were revised after an adversarial review
(see Q-7, Q-8 and §8).

**Callback topology (Q-7): backend-handled GET + 302 + `hydrate()`.** The registered
Google `redirect_uri` points at a **backend** `GET /api/auth/google/callback`, not at the
SPA. The backend does the code exchange and id_token verification server-side, mints the
session, sets the `smap_refresh` cookie, and 302-redirects to an SPA landing route; the SPA
then calls the existing `session.hydrate()` (`session.ts:60-71`, refresh cookie → access
token) and `safeRedirect`s. This keeps `code`/tokens out of the SPA URL entirely and reuses
the existing cookie+refresh machinery with **no** new token plumbing in JS. (Rejected
alternative: Google → SPA route → SPA POSTs `code`/`state` to a JSON callback returning
`TokenPairOut`. Works, but exposes `code` in browser history, needs a new POST endpoint +
view + api wrapper, and is a larger surface for no security gain.)

**Login-CSRF binding (F2 fix, Q-8).** The `state` is not only stored server-side in Redis
(single-use, deleted on read) but also written to a short-lived `smap_oauth_state` cookie
(`HttpOnly`, `SameSite=Lax` so it survives the cross-site top-level GET back from Google,
`Secure`, path `/api/auth/google`) at authorize time. The callback requires URL `state` ==
cookie `state` == the Redis entry, all three, before proceeding. Without the cookie tie an
attacker could feed a victim a pre-seeded `state`/`code` and log the victim into the
attacker's Google account.

**Flow — login mode:**

1. Login page button → full-page `window.location.assign('/api/auth/google/authorize?mode=login')`.
2. `GET /api/auth/google/authorize` (**unauthenticated**, R19.01 exception): generate
   `state` + PKCE `code_verifier`/`code_challenge` (S256) + `nonce`; store
   `{code_verifier, nonce, mode:'login'}` in Redis under `state` (short TTL); set the
   `smap_oauth_state` cookie; 302 to Google's authorization endpoint with `redirect_uri`
   built from `_public_origin()` (`auth.py:70-73`).
3. Google 302s the browser to `GET /api/auth/google/callback?code&state` (**unauthenticated**,
   R19.01 exception). Backend: require `state` cookie == query `state` == Redis entry (else
   400); load+delete the Redis entry; exchange `code` at Google's **pinned** token URL using
   `code_verifier` + client secret (httpx, bounded timeout); verify the `id_token` via
   `PyJWKClient` against Google's **pinned** JWKS URL with `algorithms=["RS256"]` (alg pinned
   to block confusion), `audience`=client id, `issuer` ∈ {`accounts.google.com`,
   `https://accounts.google.com`}, `exp` (small leeway), and `nonce` == stored nonce;
   extract `sub`, `email`, `email_verified`, `name`.
4. Resolve the user (table below), run the status gate (`auth_service.py:293-300`), call
   `_establish_session`, set the `smap_refresh` cookie (`_set_refresh_cookie`,
   `auth.py:43-53`), 302 to the SPA landing route (carrying a safe `redirect` param). SPA
   calls `session.hydrate()` then `safeRedirect`.

**Flow — link mode (F1 fix, Q-8):** the authenticated leg never rides a full-page GET
(a top-level navigation carries no `Authorization` header — `middleware/auth.py` reads only
the bearer header, never a cookie). Instead:

1. ProfileView (authenticated) calls `POST /api/auth/google/link/start` via **XHR** (bearer
   auto-attached by the axios interceptor). Backend authenticates via bearer, generates
   `state`/PKCE/nonce, stores `{..., mode:'link', user_id}` in Redis bound to the current
   `user_id`, sets the `smap_oauth_state` cookie, and returns `{authorize_url}` JSON.
2. Frontend `window.location.assign(authorize_url)`.
3. Same `GET /api/auth/google/callback`: `mode` and `user_id` come from the Redis state
   (not from the request's auth, which the Google redirect lacks); the cookie/state triple
   still binds the browser. Backend inserts the `auth_identities` row for that `user_id`
   and 302s to the profile page with a success flag. No new session is minted (the user is
   already logged in; their existing `smap_refresh`/session are untouched).

**User resolution (callback, `mode=login`):** each insert path is wrapped to catch a unique
-constraint `IntegrityError` (asyncpg) and **re-run resolution** — closing the concurrent
double-callback race against `uq_users_email_active` and `UNIQUE(provider, provider_subject)`
(F5). A pre-check is never treated as authoritative (TOCTOU); the DB constraint is.

| Situation | Action |
|---|---|
| `auth_identities(provider='google', provider_subject=sub)` exists | Log in that user (status gate first). |
| No identity; `get_active_by_email(email)` finds a user with `email_verified=true` | Auto-link: insert identity, log in (Q-2). |
| No identity; `get_active_by_email(email)` finds a user with `email_verified=false` | Bind: insert identity, `mark_verified`, **neutralize the existing password** (set `password_hash=NULL`), invalidate that user's existing sessions/refresh tokens, log in (Q-2 refinement). |
| No identity; no active user for that email | Provision: insert `users` row with `password_hash=NULL`, `email_verified=true`, `status=ACTIVE`, `display_name` from Google if the account has none (Q-5); insert identity; log in. |
| Google `id_token` reports `email_verified=false` | Reject before resolution — never provision or bind on an unverified Google email. |

On a successful Google login, refresh `auth_identities.email` from the current Google email
(the column is an informational, last-seen snapshot — F8; `change_email` does not touch it,
documented in §6).

**Null-password (Google-only) accounts — all password-verify sites (F4).** Making
`password_hash` nullable affects four flows, not just login. Every site that verifies a
password must treat `password_hash IS NULL` as "no password credential" and return
`InvalidCredentials` (never call the argon2 verifier on `None` → 500):
`login` (`auth_service.py:254`), `change_password` (`:550`), `change_email` (`:581`),
`delete_account` (`:651`). Additionally, a Google-only user has no way to *set* a first
password (`change_password` requires a current one they never had), which would trap them
against the unlink guard. So this task adds a **set-initial-password** path for
passwordless accounts (a `reset-password`-style flow: request a set-password email →
set via token, reusing `PasswordResetTokenRepository`), and the profile "set a password"
affordance points at it.

**Unlink guard (unlink):** refuse to unlink the last remaining credential — a user with
`password_hash IS NULL` and no other `auth_identities` row must set a password (via the
set-initial-password path above) before unlinking Google (returns an RFC 7807 error the UI
renders alongside the "set a password" affordance).

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
  resolution table with `IntegrityError`-retry (F5); add `link_google`/`unlink_google`.
  **Fix all four password-verify sites for null hash (F4):** `login` (`:254`),
  `change_password` (`:550`), `change_email` (`:581`), `delete_account` (`:651`) must return
  `InvalidCredentials` when `password_hash IS NULL` rather than calling the argon2 verifier
  on `None`. Add a **set-initial-password** flow (request + token-consume, reusing
  `PasswordResetTokenRepository`) so a passwordless account can gain a password (also unblocks
  the unlink guard). `change_email` leaves `auth_identities.email` untouched (documented
  snapshot, F8). New audit actions (see §7).
- **New `application/oauth_service.py` (+ `infrastructure/oauth/google.py`)** — the Google
  OIDC adapter: authorize-URL construction, PKCE, code exchange (httpx, bounded timeout),
  `id_token` verification via `PyJWKClient` against the **pinned** JWKS URL with
  `algorithms=["RS256"]` + `iss`/`aud`/`exp`/`nonce` checks, Redis state store + the
  `smap_oauth_state` cookie (F2). Token/JWKS/authorize URLs are pinned constants — never
  derived from the (attacker-influenceable) `iss` — so the SSRF surface is nil. Fail closed
  (503-style) if Google is unreachable; never hang. Client secret is Vault-sourced only
  (mirror the SMTP factory, `factory.py:31,53`).
- **`interfaces/facade.py`** — optionally expose the link-status read for `get_profile`
  (so `UserProfile`/`UserOut` can carry linked-provider info). Following the existing auth
  convention, the OAuth endpoints may talk to `AuthService` directly.

**API contract** (`app/api/v1/auth.py`) — `gen:api` rerun **required**.
- `GET /api/auth/google/authorize` (**unauthenticated**; 302 to Google; sets
  `smap_oauth_state` cookie).
- `GET /api/auth/google/callback?code&state` (**unauthenticated**; server-side exchange +
  verify; sets `smap_refresh` cookie like `login` `auth.py:311`; 302 to the SPA — no JSON
  body, no `TokenPairOut` for the callback).
- `POST /api/auth/google/link/start` (**authenticated**, XHR) → `{authorize_url}` JSON;
  binds `user_id` into Redis state (F1).
- `DELETE /api/auth/google/link` (authenticated) → link status, with the last-credential
  guard.
- `GET /api/auth/identities` (authenticated) → linked providers, for the profile UI.
- Set-initial-password endpoints (request + confirm), modeled on the password-reset pair.
- Add a `google_linked`/provider field to `UserOut` (`auth.py:174-180`) and `UserProfile`.
- Only `authorize` + `callback` are unauthenticated → added to the `R19.01` exception list
  (§13); `link/start` is bearer-authenticated. All are rate-limited (the `/api/auth/` bucket
  auto-applies, `rate_limit.py:56-60`; consider a tighter bucket for the provisioning
  callback — §16 FU-3).

**New dependency (F3).** Add `pyjwt[crypto]` (pinned) for `PyJWKClient` + RS256/JWKS
id_token verification. Today only SMAP's own tokens are verified, via Vault Transit
(`shared_kernel/auth/jwt.py:109`), which cannot verify Google's keys; `pyproject.toml` has
no JWT/JOSE library. **Hand-rolling JWS verification is forbidden** (alg-confusion footgun);
pin `algorithms=["RS256"]`. Route through `pip-audit`.

**Frontend** (`identity` slice) — after backend `openapi.json` changes + `pnpm run gen:api`.
- `api/auth.ts` — add `googleLinkStart` (XHR→`{authorize_url}`), `googleUnlink`,
  `listIdentities`, and the set-initial-password wrappers; extend `Me` (`:35-42`) + `toMe`
  (`:55-64`) with link status. **No `googleCallback` wrapper** — the callback is a backend
  GET the browser follows, not a JS call.
- `stores/session.ts` — reuse the existing `hydrate()` (`:60-71`) on the post-callback
  landing; no new token action needed (the WebSocket/query-cache lifecycle stays identical).
- `views/LoginView.vue` (+ optionally `RegisterView.vue`) — a "Sign in with Google"
  `SButton variant="secondary"` with an inline Google "G" SVG in the `icon-left` slot (no
  heroicons Google mark exists). Clicking navigates full-page to
  `/api/auth/google/authorize?mode=login` via `window.location.assign`.
- New lightweight landing route/view (e.g. `/auth/google/complete`, public,
  `requiresAuth:false`, `layout:'auth'`) modeled on `VerifyEmailView.vue` — on mount calls
  `session.hydrate()` then `safeRedirect` (success) or shows the error carried in the 302's
  query (failure). This replaces the rejected SPA-POST `GoogleCallbackView`.
- `views/ProfileView.vue` — a connections section showing linked Google status; **link**
  button calls `authApi.googleLinkStart()` (XHR) then `window.location.assign(url)`;
  **unlink** calls the API and surfaces the last-credential guard error next to a "set a
  password" affordance (→ set-initial-password flow).
- i18n — new keys in `slices/identity/locales/en.json` + `zh-TW.json`
  (`identity.login.googleSignIn`, `identity.profile.connections.*`, set-password, error
  keys); any `UserMenu` entry needs `app/locales/*`.

**Deploy/config**
- New `OAuthSection` in `settings.py`: `google_client_id`, `google_redirect_path` (redirect
  URI derived from `_public_origin()`), `enabled` flag. **`google_client_secret` is NOT an
  env var** — read from Vault KV `secret/smap/config/google_oauth` (SMTP precedent,
  `factory.py:31,53`).
- **Egress prerequisite (F6):** `backend-web` must reach Google over HTTPS
  (`accounts.google.com`, `oauth2.googleapis.com`, `www.googleapis.com`). `backend_net` is
  `internal: false` (`docker-compose.yml:19,128`) so a direct route exists and the
  egress-proxy is **not** involved (that plane is sandbox-only); but the hardened staging
  host's own egress policy (`smap.rcsl.online`) must be confirmed to allow these hosts.
  JWKS/token URLs are pinned constants; requests carry an httpx timeout and the JWKS is
  cached.
- Google Cloud Console: an OAuth 2.0 Web-application client with the authorized redirect URI
  set to the **backend** callback (`https://<origin>/api/auth/google/callback`) for each
  origin (staging `smap.rcsl.online`, prod).
- Startup: log a single warning if `enabled` but client id/secret unconfigured, and fail
  closed on the OAuth endpoints rather than 500 (mirror `warn_if_email_unconfigured`,
  `factory.py:98-110`).

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
- [x] Error handling UX — the backend callback conveys failures to the SPA landing route as
  a safe error code in the 302 query (never leaking `code`/tokens); the landing view renders
  RFC 7807-style copy for: state/cookie mismatch or expiry, Google denied/`error` param,
  `email_verified=false` from Google (reject), account banned/deleted, Google unreachable
  (fail-closed, not a hang), provider-already-linked-to-another-user (409), and the
  last-credential unlink guard. Login page reuses `isProblemWithType` branching
  (`LoginView.vue:96-116`); the landing view shows loading/error states like
  `VerifyEmailView.vue`.
- [x] Performance — O(1) per auth: one Redis get/del for state, one id_token verification
  against a **cached** JWKS, one-to-two indexed user/identity lookups. The code-exchange and
  any JWKS refresh are outbound httpx calls with a **bounded timeout** (no unbounded wait on
  an unauthenticated endpoint). No new list endpoints beyond a bounded per-user identities
  read. No N+1.

## 8. Security Considerations

Touches auth, session issuance, and account identity — full treatment required.

- **Account takeover via unverified pre-registration (primary risk).** Handled by the Q-2
  refinement: never auto-log-in to an `email_verified=false` account; bind + verify +
  neutralize the stale password. Without this, an attacker pre-registering the victim's
  email would inherit the session the victim's Google login creates.
- **`email_verified=false` from Google.** Reject provisioning/linking when Google itself
  reports the email unverified — a Google account can carry an unverified email; treating
  it as proof of ownership would reintroduce the takeover vector.
- **CSRF / login-CSRF on the OAuth round-trip (F2).** `state` is single-use, random, stored
  server-side (deleted on read), **and** mirrored into a short-lived `smap_oauth_state`
  cookie (`HttpOnly`, `SameSite=Lax`, `Secure`, path `/api/auth/google`) set at authorize;
  the callback requires URL `state` == cookie `state` == Redis entry. The cookie tie is what
  stops login-CSRF (attacker feeding a victim a pre-seeded `state`/`code` to log them into
  the attacker's Google account) — Redis single-use alone does not. `mode=link`
  additionally binds the state to the initiating `user_id`.
- **`id_token` validation + algorithm pinning (F3).** Verify via `PyJWKClient` against
  Google's **pinned** JWKS URL with `algorithms=["RS256"]` **explicitly pinned** (blocks
  `alg=none`/HS256-vs-RS256 confusion), plus `iss`, `aud` (must equal our client id — blocks
  token substitution from another Google app), `exp` (small leeway), and `nonce` (replay
  defense). Never trust `email`/`sub` from the token-endpoint body without id_token
  verification. Hand-rolled JWS verification is forbidden.
- **SSRF surface is nil.** The authorize/token/JWKS URLs are pinned constants, never derived
  from the (attacker-influenceable) `iss` claim, so the outbound calls cannot be steered.
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
- **Null-hash flows (F4).** Making `password_hash` nullable means every password-verify site
  (`login` `:254`, `change_password` `:550`, `change_email` `:581`, `delete_account` `:651`)
  must fail closed with `InvalidCredentials` for a null hash — calling the argon2 verifier on
  `None` is a 500, and an unhandled null hash on `delete_account` regresses R6.07 self-delete.
- **Rate limiting.** `authorize`/`callback` are public — rate-limited per IP by the
  `/api/auth/` bucket (`rate_limit.py:56-60`); `link/start` is bearer-authenticated.

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
- **`password_hash` nullability blast radius (F4).** Four flows assume a non-null hash and
  must be fixed together — `login` (`:254`, incl. the dummy-hash path `:247`),
  `change_password` (`:550`), `change_email` (`:581`), `delete_account` (`:651`). Each must
  return `invalid credentials` for a null hash, never a 500, and `delete_account` must not
  regress R6.07 self-delete. Plus the set-initial-password path must exist so a passwordless
  user is not trapped by the unlink guard. Covered by AC-8, AC-14, AC-15.
- **Misconfiguration parity with SMTP.** If `enabled` but client id/secret unset, fail
  closed on the OAuth endpoints (clear error / hide the button via config) and log a startup
  warning — do not 500. Mirrors the SMTP `warn_if_email_unconfigured` posture. Same posture
  if Google's token/JWKS endpoints are unreachable: bounded timeout → fail-closed error.
- **Cookie `SameSite`.** Both cookies work under `SameSite=Lax` (`settings.py:214-217`
  disallows `none`): the `smap_oauth_state` cookie must survive the cross-site top-level GET
  back from Google (Lax permits top-level GET), and the `smap_refresh` `Set-Cookie` on the
  callback GET is likewise a top-level navigation Lax allows. Verify the callback is reached
  on the same registered origin on staging before prod.

## 11. Acceptance Criteria

- [ ] AC-1: `GET /api/auth/google/authorize?mode=login` returns a 302 to Google with a
  `state`, `code_challenge` (PKCE S256), `nonce`, and a `redirect_uri` derived from
  `_public_origin()`; the `state` entry is stored in Redis with a bounded TTL and the
  matching `smap_oauth_state` cookie (`SameSite=Lax`) is set.
- [ ] AC-2: A valid callback for a Google `sub` that has never been seen and whose email
  matches no active account provisions a `users` row with `password_hash IS NULL`,
  `email_verified=true`, `status=ACTIVE`, inserts an `auth_identities` row, sets the
  `smap_refresh` cookie, **302-redirects to the SPA landing route** (no JSON body), and
  emits `auth.oauth.provisioned` + `auth.oauth.login.success`. The SPA landing calls
  `hydrate()` and ends up logged in.
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
- [ ] AC-7: A callback is rejected (no session minted) when any of these fail: URL `state`
  != `smap_oauth_state` cookie (login-CSRF binding), state expired/unknown/reused, or an
  id_token failing signature / `algorithms=["RS256"]` pinning (an `alg=none`/HS256 token is
  rejected) / `iss` / `aud` / `exp` / `nonce`.
- [ ] AC-8: A Google-only account (`password_hash IS NULL`) attempting `POST /api/auth/login`
  receives an invalid-credentials error, never a 500.
- [ ] AC-14: A Google-only account calling `change_password`, `change_email`, or
  `delete_account` receives an invalid-credentials (or "set a password first") error, never a
  500, and self-delete is not permanently blocked (R6.07 preserved via the set-password path).
- [ ] AC-15: A passwordless account can complete the set-initial-password flow (request →
  token → set) and afterward can log in with a password and unlink Google.
- [ ] AC-16: Two concurrent first-time callbacks for the same new email converge on a single
  `users` row (the loser catches the `uq_users_email_active` violation and re-resolves to the
  winner), with no 500. Two concurrent link attempts for the same `sub` yield one identity
  and a 409 for the loser.
- [ ] AC-17: When Google's token or JWKS endpoint is unreachable within the bounded timeout,
  the callback fails closed with an error surfaced to the SPA landing — it does not hang.
- [ ] AC-18: `link/start` requires a bearer token (401 without); the resulting callback binds
  the identity to the `user_id` captured in the Redis state, not to any request-supplied id.
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
  in both locales) that navigates to `authorize`; the landing view handles loading/error
  states; the profile page shows link/unlink with the current linked status and a "set a
  password" affordance for passwordless accounts.

## 12. Test Plan

- **Unit (backend, `tests/unit/`)** — resolution-table logic in `login_with_oauth`
  (AC-2..AC-6, AC-9): fake `AuthIdentityRepository`/`UserRepository`, assert the branch taken
  and the audit action, mirroring `tests/unit/test_auth_service.py`. id_token verification
  (AC-7) with a stubbed JWKS: valid, wrong `aud`, expired, bad `nonce`, bad signature, and an
  `alg=none`/HS256 token (must be rejected by the RS256 pinning). State/cookie-mismatch reject
  (AC-7). Null-hash handling at all four verify sites (AC-8, AC-14) and the set-initial-password
  flow (AC-15). `IntegrityError`-retry resolution (AC-16). Google-unreachable fail-closed with a
  patched httpx timeout (AC-17). `_establish_session` characterization: existing login tests
  stay green after the extraction.
- **Integration (`tests/integration/`, Postgres+Redis)** — the migration applies and rolls
  back (incl. the down-guard against null-hash accounts); `authorize` stores state + sets the
  cookie; a mocked Google token/JWKS drives a full backend callback producing a real session
  row + `smap_refresh` cookie + a 302 to the SPA (AC-1..AC-4, AC-10..AC-12, AC-18); state
  single-use/expiry and cookie-binding (AC-7); concurrency (AC-16). Model on
  `tests/integration/test_auth_login_refresh.py`.
- **Frontend (component)** — LoginView renders the Google button (both locales); the landing
  view calls `hydrate()` and redirects on success / renders the 302 error code on failure;
  ProfileView link (XHR→navigate), unlink last-credential guard, and the set-password
  affordance (AC-11, AC-13, AC-15).
- **Manual (`/run` or `frontend:verify`)** — end-to-end against a real Google test client on
  staging (`smap.rcsl.online`): new-account provision, link from profile, unlink guard,
  set-initial-password. Confirms the host egress policy reaches Google (F6).

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
  The `id_token` is verified against Google's JWKS with the algorithm pinned to RS256, plus
  `iss`, `aud` (our client id), `exp`, and `nonce`; `state` is single-use and bound to the
  initiating browser via a short-lived cookie (login-CSRF defense).
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
  credential (no password and no other linked identity) until a password is set; a
  passwordless account may set an initial password via an emailed set-password token. Link
  and unlink are audit-logged (`auth.oauth.account_linked` / `auth.oauth.account_unlinked`).
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
- FU-3: The account-provisioning callback shares the generic `/api/auth/` `AUTH` rate-limit
  bucket (`rate_limit.py:56-60`) rather than a tighter recovery-grade bucket. Consider a
  dedicated bucket for the provisioning path in a later hardening pass (F9). Not changed here.
- FU-4: Interaction with org/project invite acceptance (R6.09-R6.11) was checked — `register`
  carries no invite handling, so Google provisioning does not regress it — but invite
  acceptance being email-keyed post-login should be re-confirmed when that flow is next
  touched. Recorded, not acted on here.
