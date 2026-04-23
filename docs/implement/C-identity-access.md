# Phase C — Identity, Tenancy, Access & Web Security

**Goal.** Ship the full identity / tenancy / security surface end-to-end: users + email verification + sessions + JWT (RS256 with Transit-stored private key + `kid` rotation + Redis denylist) + password lifecycle; Orgs with the Original Creator invariant + transfer flow + default-project auto-create; polymorphic Projects; unified Invites (Org + Project); the 24×6 permission matrix; IP bans; rate-limit buckets with exact numeric budgets; CAPTCHA; the full §19a header / trust boundary set; CSP report-only mode; admin append-only audit emission trigger; email-domain allow/denylist.

**Size.** XL
**Depends on.** B (Vault, DB, bootstrap CLI).
**Unblocks.** D, E, F, G, H, I.
**Refs.** `REQUIREMENTS.md` §5, §6, §8 (incl. §8.5), §17, §19, §19a, §21.1, §22.1, §22.2, §22.2a, §22.3.

## C.0 Scope summary

At phase close:

- Register (with CAPTCHA) → verify email → login → refresh → logout → change password → change email (re-verify) → reset password → list/revoke sessions all work end-to-end through `/api/auth/*`.
- Orgs, projects, members, invites, original-creator transfer all work through `/api/orgs/*`, `/api/projects/*`, `/api/invites`.
- Permission matrix enforced server-side from a single module (R5.05).
- Admin `/api/admin/ip-bans` CRUD present; banned users/IPs 403 at earliest middleware layer.
- Rate-limit middleware applies R19.02 numeric budgets + R19.03 WS cap.
- CSP / HSTS / CORS / CSRF / trust-boundary / CAPTCHA controls from §19a active.

## C.1 Backend bounded-context layout — **OPS** — S

**Objective.** Re-align `backend/` with §23.

**Deliverables.**

- `backend/app/{api,config,main.py}` for the thin ASGI shell.
- `backend/contexts/{identity,tenancy,keys,agents,knowledge,conversation,workflow,audit,notification}/{domain,application,infrastructure,interfaces}`.
- `backend/shared_kernel/{auth,db,events,errors,i18n}`.
- R23.01–R23.03: no cross-context SQL joins; cross-context reads go through target facades; `api/` routers call only facades.

**Key IDs.** `[R23.01]`–`[R23.03]`.

**Exit criteria.** Import-linter rule enforces boundary on CI.

## C.2 Identity schema — **CODE** — M

**Deliverables.**

- Alembic revision `0001_identity`:
  ```
  users (
    id uuid pk, email citext, password_hash text, email_verified bool,
    status enum('active','pending','banned','deleted'),
    banned_reason text null, banned_at timestamptz null, deleted_at timestamptz null,
    last_login_at, created_at,
    UNIQUE (email) WHERE deleted_at IS NULL
  );
  ip_bans  (cidr cidr pk, reason text, banned_at);
  sessions (id uuid pk, user_id fk users, refresh_token_hash text,
            created_at, last_used_at, user_agent, ip_inet inet);
  password_reset_tokens (id uuid pk, user_id fk users, token_hash text,
                         expires_at, used_at timestamptz null);
  email_verify_tokens   (id uuid pk, user_id fk users, token_hash text,
                         expires_at, used_at timestamptz null);
  admins (user_id fk users PRIMARY KEY, promoted_by_user_id fk users null,
          promoted_at timestamptz, revoked_at timestamptz null);
  admin_impersonation_sessions (id uuid pk, admin_user_id fk users,
                                target_user_id fk users,
                                started_at, ended_at timestamptz null,
                                started_request_id uuid);
  ```
- `users.email` partial-unique survives soft-delete (R6.07 recovery window).

**Key IDs.** §21.1 Identity + admins + impersonation.

**Exit criteria.** Migration up/down clean; soft-delete then re-register same email blocked during recovery.

## C.3 Password hashing — **CODE** — S

**Deliverables.**

- `smap/shared_kernel/auth/password.py` using `argon2-cffi`:
  - **memory 64 MiB, time 3, parallelism 2** (R6.01 — **not** 4).
  - `hash_len=32`.
- `verify_and_upgrade()` rehashes on parameter change.
- Password policy on change/register: ≥ 10 chars, ≥ 1 letter, ≥ 1 digit, ≥ 1 symbol (R6.01).

**Key IDs.** `[R6.01]`.

**Exit criteria.** Unit tests for accept/reject; rehash-on-param-change test.

## C.4 JWT (RS256 via Vault Transit) + refresh — **CODE** — M

**Deliverables.**

- JWT signing uses **Vault Transit** key `smap-jwt-sign` (RS256) with `kid` header; rotated quarterly, 7-day overlap during which both old and new `kid` verify (R6.03).
- Private key **never** on filesystem, **never** in KV. Public keys fetched via `/v1/transit/keys/smap-jwt-sign` for verification and cached in-process.
- Access tokens TTL 15 min.
- Refresh tokens TTL 30 days, **rotating**:
  - Redis: `session:{sha256(refresh_token)}` holds session record; TTL = refresh remaining.
  - DB: `sessions` row (§21.1) mirrors for the "list my active sessions" UI (R6.08).
  - Reuse of a consumed refresh invalidates the whole session family (denylist jti + remove DB row).
- Access-token revocation: Redis `jti_denylist:{jti}` with TTL = access TTL; middleware checks every request. Denylist on logout, password change, ban, detected compromise.
- Password change (R6.06) invalidates all refresh + denylists currently-valid access tokens of that user.

**Key IDs.** `[R6.03]`, `[R6.06]`, `[R6.08]`; §21.1 entity table `JWT signing key | Vault Transit smap-jwt-sign`.

**Exit criteria.** Rotation integration test keeps old `kid` verifying for 7 days; refresh reuse kills family.

## C.5 Auth endpoints — **CODE** — M

**Deliverables.** All paths under `/api/auth/*` exactly per §22.1:

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | CAPTCHA required (R19a.12). Email verification dispatch. |
| POST | `/api/auth/verify-email` | `{token}` → transitions to `active`; blocks Org/Project create + Guest accept while `pending` (R6.02). |
| POST | `/api/auth/login` | lockout after 5 attempts / 15 min / account **AND** 20 / 15 min / IP (R6.04). |
| POST | `/api/auth/refresh` | rotates pair. |
| POST | `/api/auth/logout` | invalidate current refresh + deny jti. |
| POST | `/api/auth/request-password-reset` | token TTL 30 min, single-use (R6.05). |
| POST | `/api/auth/reset-password` | |
| POST | `/api/auth/change-password` | `{current, new}`; invalidates all sessions (R6.06). |
| POST | `/api/auth/change-email` | `{new_email, password}`; re-verification required. |
| GET | `/api/auth/me` | |
| GET | `/api/auth/sessions` | list `sessions` rows for caller. |
| DELETE | `/api/auth/sessions/{id}` | revoke one (denylist jti issued from its last refresh). |

- CAPTCHA provider keys in Vault KV `secret/smap/config/captcha`.
- Admin email-domain allow/denylist runtime config (R19a.13) consulted by `register`.

**Key IDs.** `[R6.01]`–`[R6.08]`, `[R19a.12]`–`[R19a.13]`, §22.1.

**Exit criteria.** Full Playwright flow; domain denylist blocks signup.

## C.6 Role scoping & permission matrix — **CODE** — L

**Deliverables.**

- `Role` enum: `Admin, OrgOwner, OrgMember, ProjectOwner, ProjectMember, Guest` (R5.01–R5.04).
- Single `permissions` service (`smap/shared_kernel/auth/permissions.py`) — every route reaches it via a FastAPI dependency; no duplicated checks (R5.05).
- `Capability` enum maps to the 24 rows of §5.2; scope resolver pulls `org_id` / `project_id` / `chatroom_id` from path params or request bodies.
- OrgOwner→ProjectOwner inheritance computed at check time, never stored (R5.03).
- `Original Creator` is a separate immutable bit on `org_members.is_original_creator` (R5.02).
- Frontend uses `<PermissionGate>` but every render-time check is paired with a server check (R5.05 + R24.20).

**Key IDs.** §5.2, `[R5.01]`–`[R5.05]`.

**Exit criteria.** 144-case matrix test (24 capabilities × 6 roles) + scope inheritance tests.

## C.7 Tenancy schema — **CODE** — M

**Deliverables.**

- Alembic revision `0002_tenancy`:
  ```
  orgs (
    id uuid pk, name citext, creator_user_id fk users,
    version int not null default 1,
    created_at, deleted_at,
    UNIQUE (name) WHERE deleted_at IS NULL
  );
  org_members (
    org_id fk orgs, user_id fk users, role enum('owner','member'),
    is_original_creator bool, joined_at,
    UNIQUE (org_id, user_id),
    EXCLUDE USING btree (org_id WITH =) WHERE (is_original_creator)
  );
  projects (
    id uuid pk, owner_user_id fk users null, owner_org_id fk orgs null,
    name,
    version int not null default 1,
    created_at, deleted_at,
    CHECK ((owner_user_id IS NOT NULL) <> (owner_org_id IS NOT NULL)),
    UNIQUE (owner_user_id, name) WHERE deleted_at IS NULL,
    UNIQUE (owner_org_id,  name) WHERE deleted_at IS NULL
  );
  project_members (
    project_id fk projects, user_id fk users, role enum('owner','member'),
    joined_at, UNIQUE (project_id, user_id)
  );
  ```
- Creating an Org (R8.01 + R8.05): atomically inserts `orgs` row, `org_members(role=owner, is_original_creator=true)`, AND a default `projects(owner_org_id=..., name='Default Project')`.
- Deletion semantics (R8.11–R8.14): soft-delete; 60-day nightly hard-delete; cascaded cleanups live in I4.

**Key IDs.** §21.1 tenancy, `[R8.01]`–`[R8.14]`.

**Exit criteria.** Atomic Org-creation produces 3 rows; Original Creator EXCLUDE violates under concurrent insert.

## C.8 Org endpoints + Original-Creator transfer — **CODE** — M

**Deliverables.**

- Endpoints (§22.2):
  - `GET /api/orgs`, `POST /api/orgs` (caller becomes OC+Owner; default project auto-created).
  - `GET /api/orgs/{id}`, `PATCH /api/orgs/{id}` (`If-Match: <version>`), `DELETE /api/orgs/{id}` (OC or Admin only; soft).
  - `POST /api/orgs/{id}/restore` (Admin only, 60-d window).
  - `GET /api/orgs/{id}/members`, `POST /api/orgs/{id}/invites`.
  - `DELETE /api/orgs/{id}/members/{uid}`, `PATCH /api/orgs/{id}/members/{uid}` (role change; OC row rejected).
- Original-Creator transfer (§8.5):
  ```
  original_creator_transfers (
    id uuid pk, org_id fk orgs,
    initiator_user_id fk users, target_user_id fk users,
    state enum('pending','accepted','rejected','cancelled','expired','admin_forced'),
    created_at, resolved_at timestamptz null, expires_at timestamptz,
    UNIQUE (org_id) WHERE resolved_at IS NULL
  );
  ```
  - Initiate (OC only): target must already be an OrgOwner (R8.15); 409 if another pending exists (R8.17).
  - Accept (`POST .../accept`): atomically flip `is_original_creator` on `org_members`.
  - Cancel (`DELETE .../{tid}`) by initiator or Admin.
  - Expire: 7 days; worker sets `state='expired'` (R8.16 step 4).
  - List pending (`GET .../original-creator-transfers`) — at most one (R8.17).
- `R8.18` enforcement: self-delete of a user blocked while they are OC of any Org with other active members; error lists affected Org IDs.
- `R8.19` Admin force-transfer: `POST /api/admin/orgs/{id}/force-transfer-original-creator` (Phase I).

**Key IDs.** §22.2, §8.5, `[R8.15]`–`[R8.19]`.

**Exit criteria.** Concurrent transfer+self-delete scenario covered; expiry worker tested.

## C.9 Projects endpoints — **CODE** — S

**Deliverables.**

- Endpoints (§22.3):
  - `GET /api/projects?scope=user|org&id=…`
  - `POST /api/projects` (`{owner_type, owner_id, name}`) — `owner_type ∈ {'user','org'}` maps to the two nullable FKs.
  - `GET /api/projects/{id}`, `PATCH /api/projects/{id}`, `DELETE /api/projects/{id}` (soft).
  - `POST /api/projects/{id}/restore` (Admin only).
  - `GET /api/projects/{id}/members`, `POST /api/projects/{id}/invites`.
  - `DELETE /api/projects/{id}/members/{uid}`, `PATCH /api/projects/{id}/members/{uid}`.
- Individual-owned projects cannot migrate into an Org (R8.07).

**Key IDs.** §22.3, `[R8.06]`–`[R8.10]`.

**Exit criteria.** Polymorphic CHECK enforced; member add/remove round-trip.

## C.10 Unified invitations — **CODE** — M

**Deliverables.**

- Alembic revision `0003_invites`:
  ```
  invites (
    id uuid pk, scope_type enum('org','project'), scope_id uuid,
    role enum('owner','member'),
    inviter_user_id fk users, invitee_email citext,
    invitee_user_id fk users null,
    state enum('pending','accepted','rejected','revoked','expired'),
    token_hash text,
    expires_at, created_at, resolved_at timestamptz null,
    UNIQUE (scope_type, scope_id, invitee_email) WHERE state = 'pending'
  );
  ```
- Endpoints (§22.2 / §22.3 / §22.2a):
  - `POST /api/orgs/{id}/invites`, `POST /api/projects/{id}/invites` — emails link.
  - `GET /api/invites?state=pending|accepted|rejected` — inbound view for caller.
  - `POST /api/invites/{id}/accept`, `POST /api/invites/{id}/reject`.
- Unverified (`status='pending'`) users cannot accept Guest invites (R6.11 wrapping).

**Key IDs.** §22.2a, `[R6.09]`–`[R6.11]`.

**Exit criteria.** Pending duplicate blocked; accept flips membership atomically.

## C.11 Web security headers, TLS, CORS, CSRF, trust — **CODE + OPS** — M

**Deliverables.**

- **Transport (R19a.01–R19a.04)**: Nginx TLS terminator; TLS 1.2 min / 1.3 preferred; only AEAD cipher suites (`TLS_AES_128_GCM_SHA256`, `TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`, ECDHE+AES-GCM); HSTS `max-age=31536000; includeSubDomains; preload`.
- **Response headers (R19a.04 + table in §19a.2)** applied by Nginx for HTML + echoed by backend for JSON:
  - CSP: `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'`. **`img-src https:` is intentionally broad** (R19a.05) because agents emit markdown with public-link images.
  - `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`.
- **CSP report-only toggle** (R19a.06): env `SMAP_CSP_REPORT_ONLY=1` flips enforce → report-only; reports posted to `/api/csp-report`.
- **CORS (R19a.07–R19a.08)**: same-origin default; allowlist is exactly one origin (configured public origin); cross-origin explicitly unsupported in v1.
- **CSRF (R19a.09)**: tokens in `Authorization: Bearer`, not cookies, so form-CSRF is inapplicable. Future cookie auth requires double-submit.
- **Trusted proxies (R19a.10–R19a.11)**: `X-Forwarded-For` trusted **only** when the immediate peer's address is in `TRUSTED_PROXIES` CIDR list (default: `127.0.0.0/8`, Docker bridge subnet, operator-configured front-proxy subnet). Parser walks right-to-left, taking the right-most non-trusted address; otherwise uses peer address as `actor_ip`.
- **CAPTCHA (R19a.12)**: hCaptcha / Turnstile selectable via setting; keys in Vault KV `secret/smap/config/captcha`.
- **Email domain allow/denylist (R19a.13)**: runtime config checked on `register`.

**Key IDs.** §19a.

**Exit criteria.** Snapshot test locks header set; spoofed `X-Forwarded-For` from untrusted peer ignored; `SMAP_CSP_REPORT_ONLY=1` switches to report-only.

## C.12 Rate limiting — **CODE** — M

**Deliverables.**

- `smap/shared_kernel/auth/ratelimit.py` using Redis + Lua sliding-window.
- Default buckets (R19.02):
  - **auth** (`/api/auth/*`): 10 req / min / IP.
  - **chat-send** (`POST /api/chatrooms/{id}/messages`): 60 / min / user.
  - **upload** (attachment POST + tus Creation POST): 10 / min / user (tus PATCH 300 / min / user is set in F.5).
  - **other**: 300 / min / user.
- WS: max **5 concurrent** per user (R19.03); 6th rejected with `ws-per-user-limit`.
- Runtime-adjustable via `rate_limit_policies (key text pk, window_sec int, max_count int, scope enum('user','ip','user_and_ip'))` (R19.04 + §21.1).
- Banned users/IPs 403 at earliest middleware (R19.05).
- 429 response (R19.06): RFC 7807 problem+json with `type = https://smap.local/problems/rate-limited`; headers `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

**Key IDs.** §19.

**Exit criteria.** Burst test hits exactly the configured bucket; admin `PATCH /api/admin/rate-limits/{key}` (Phase I) updates bucket live.

## C.13 IP bans — **CODE** — S

**Deliverables.**

- Table `ip_bans (cidr, reason, banned_at)` populated via Admin (`/api/admin/ip-bans`, Phase I).
- Middleware consults `ip_bans` (cached in-process, invalidated on write) before rate limit; returns 403 (RFC 7807 type `/problems/ip-banned`).

**Key IDs.** `[R6.13]`, `[R19.05]`, §21.1 `ip_bans`.

**Exit criteria.** Adding a CIDR 403s next request within 5 s.

## C.14 Audit backbone — **CODE** — S

**Deliverables.**

- Alembic revision `0004_audit`:
  ```
  audit_logs (
    id bigserial pk, actor_user_id fk users null, actor_ip inet,
    action text, resource_type text, resource_id uuid null,
    metadata jsonb, session_id uuid null, request_id uuid null,
    created_at
  );
  CREATE INDEX ON audit_logs (actor_user_id, created_at DESC);
  CREATE INDEX ON audit_logs (resource_type, resource_id);
  CREATE INDEX ON audit_logs (created_at);
  ```
- **Append-only trigger (R17.04)**: DB trigger denies UPDATE/DELETE on `audit_logs` except from a whitelisted role used by the nightly retention job.
- Emission helper `audit.emit(action, actor, resource, metadata)`.
- Secret redaction (R17.03): before logging, recursive JSON walker replaces values whose **key** matches `^(authorization|api[_-]?key|secret|password|token|bearer|private[_-]?key|cookie|session)$` (case-insensitive) with `"<redacted>"`; also values matching known secret shapes (`sk-ant-…`, `sk-…` ≥ 40 chars, PEM header patterns).
- C endpoints emit: `auth.login.success/failed`, `auth.logout`, `auth.password_reset_requested/changed`, `auth.email_verified/email_changed`, `auth.session_revoked`, `user.created/banned/unbanned`, `org.created/deleted/restored/member_invited/member_removed/owner_promoted/owner_demoted`, `project.created/deleted/restored/member_invited/member_removed`.
- `audit_logs` is visible to Admin only (R17.02); no `/orgs/{id}/audit` endpoint.

**Key IDs.** §17, `[R17.01]`–`[R17.04]`.

**Exit criteria.** Trigger-blocked UPDATE raises; secret-shaped value in metadata reaches DB as `<redacted>`.

## C.15 Frontend auth + tenancy shell — **CONTRACT** — M

**Objective.** Scaffold `slices/identity/` and `slices/tenancy/` just enough to drive Playwright through C endpoints.

**Deliverables.**

- `slices/identity/views/` — `RegisterView, LoginView, VerifyEmailView, PasswordResetRequestView, PasswordResetConfirmView, ChangePasswordView, ChangeEmailView, SessionsView`.
- `slices/tenancy/views/` — `OrgListView, OrgDetailView, OrgMembersView, OrgTransferView, ProjectListView, ProjectDetailView, ProjectMembersView, InboxInvitesView`.
- Route meta uses `requiresAuth` + `requiresVerifiedEmail` + `requiredRoles` (R24.18).
- Session composable keeps access token in memory; refresh token in `sessionStorage`, cleared on logout (R24.11).
- 401 + `type=https://smap.local/problems/auth/token-expired` → silent refresh + single replay (R24.12 #4).

**Key IDs.** §24.2, §24.6, §24.12.

**Exit criteria.** Playwright end-to-end register → verify → login → create org → invite → accept → transfer.

## C.∞ Phase gate

- [ ] Argon2id parallelism = **2**.
- [ ] JWT private key in **Vault Transit** (`smap-jwt-sign`), never on filesystem.
- [ ] Refresh stored in Redis `session:{sha256(refresh)}` + `sessions` DB row.
- [ ] 144-case permission matrix test green.
- [ ] Org creation atomically produces default project (R8.05).
- [ ] `original_creator_transfers` flow + 7-day expiry worker verified.
- [ ] Polymorphic projects CHECK verified.
- [ ] `ip_bans` middleware 403s at earliest layer.
- [ ] Rate-limit numeric budgets match R19.02 exactly; WS cap = 5.
- [ ] `audit_logs` append-only trigger rejects UPDATE/DELETE.
- [ ] CSP report-only toggle works; X-Forwarded-For trust boundary respects TRUSTED_PROXIES CIDRs.
- [ ] All endpoint paths are `/api/auth/*`, `/api/orgs/*`, `/api/projects/*`, `/api/invites*`.
- [ ] `00-overview.md` §0.8: C = done.

## Cross-cutting checklist

1. **AuthZ tap.** Every C endpoint uses `require(capability, scope)`; `@public` registry holds the four no-auth endpoints (register, login, request-password-reset, verify-email — R19.01).
2. **Audit tap.** Emitters listed in C.14.
3. **Rate limit bucket.** Four R19.02 buckets + WS cap.
4. **Observability.** Counters `auth_login_total{status}`, `auth_refresh_total`, `captcha_failures_total`, `ip_ban_blocks_total`, `ratelimit_hits_total{bucket}`.
5. **RFC 7807.** `https://smap.local/problems/{auth/invalid-credentials, auth/token-expired, auth/captcha-required, auth/password-weak, auth/lockout, auth/email-unverified, auth/domain-denied, tenancy/original-creator-conflict, tenancy/transfer-conflict, tenancy/original-creator-self-delete-blocked, invites/expired, invites/duplicate, rate-limited, ip-banned, forbidden}`.
6. **Migration policy.** Revisions `0001_identity`, `0002_tenancy`, `0003_invites`, `0004_audit`, each N-1 compatible.
7. **Secrets.** JWT keys in Transit; CAPTCHA/SMTP/HMAC/DEK wrap in Vault.

## Risks

- **Argon2 cost on smaller hardware.** CI timing gate per release.
- **Permission matrix drift.** Single module + 144-case matrix test; every capability change updates §5.2 first.
- **Trusted-proxy misconfiguration.** Operator README walks through `TRUSTED_PROXIES` CIDR; test harness warns if untrusted peer forwards `X-Forwarded-For`.
- **Email deliverability.** Invitation + verification emails may be spam-filtered; UI offers "copy link" affordance as fallback.
