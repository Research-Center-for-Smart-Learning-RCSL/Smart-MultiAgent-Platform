# Security

This document describes the security architecture, controls, and disclosure process for SMAP.

> **Licensing & warranty notice.** SMAP is distributed under the GNU AGPL-3.0-or-later (see [LICENSE](./LICENSE)) and is provided **"AS IS" without warranty of any kind** as set out in sections 15 through 17 of that license. This document describes the security controls that SMAP attempts to implement; it does **not** constitute a warranty that the software is free of defects or fit for any particular purpose. Operators are responsible for deploying, configuring, and monitoring their own instances. Reporting a vulnerability under this policy does not create any contractual or fiduciary relationship between the reporter and the project maintainers.

---

## Table of Contents

1. [Reporting a Vulnerability](#reporting-a-vulnerability)
2. [Supported Versions](#supported-versions)
3. [Authentication & Session Management](#authentication--session-management)
4. [Authorization Model](#authorization-model)
5. [API Key Handling (BYO-Key)](#api-key-handling-byo-key)
6. [Secrets & Encryption at Rest](#secrets--encryption-at-rest)
7. [Transport Security](#transport-security)
8. [API & Network Hardening](#api--network-hardening)
9. [Input Validation & Sanitization](#input-validation--sanitization)
10. [File Uploads & Object Storage](#file-uploads--object-storage)
11. [Admin & Privileged Operations](#admin--privileged-operations)
12. [Audit Logging](#audit-logging)
13. [Dependency Management](#dependency-management)
14. [Self-Hosted Operator Checklist](#self-hosted-operator-checklist)
15. [Known Limitations & Out-of-Scope (v1)](#known-limitations--out-of-scope-v1)

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please email **leolove3very@gmail.com** with:

- A clear description of the vulnerability
- Steps to reproduce (proof-of-concept code or screenshots welcome)
- Potential impact assessment
- Any suggested remediation

We aim to acknowledge reports within **2 business days** and to provide an initial severity assessment within **7 days**. We will coordinate a disclosure timeline with you and credit researchers who report valid issues.

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main` branch (latest) | Yes |
| Older tagged releases | No (self-hosted; upgrade is the fix) |

SMAP is a self-hosted product. Security fixes are delivered as commits on `main`; operators are responsible for pulling updates and redeploying.

---

## Authentication & Session Management

### JWT tokens (RS256)

- **Access tokens** — RS256-signed, 15-minute TTL (configurable via `SMAP_JWT_ACCESS_TTL_SECONDS`). Stored in JavaScript memory only (never `localStorage` or `sessionStorage`), so they are not reachable by persistent XSS. A tab-level XSS can use an in-memory token for up to 15 minutes, but cannot obtain the refresh token (stored in an `HttpOnly` cookie unreachable by script).
- **Refresh tokens** — 30-day rotating tokens stored in an `HttpOnly; Secure; SameSite` cookie. Each use issues a new token and invalidates the old one. The browser sends the cookie automatically on same-origin requests; JavaScript cannot read it.
- **Signing key** — Stored in HashiCorp Vault Transit engine. The application only sends data to be signed and receives a signature back; the private key itself never leaves Vault or enters application memory.
- **JTI denylist** — Every token carries a unique `jti`. On logout, password change, or user ban, the `jti` is added to a Redis denylist with a TTL equal to the remaining token lifetime. Every request checks this list.

### Password security

- Argon2id with 64 MiB memory, time cost 3, parallelism 2.
- Minimum policy: 10 characters, at least one letter, one digit, one symbol.
- NFKC Unicode normalization applied before hashing (prevents homograph-attack bypasses).
- Automatic parameter upgrade on next verify if stored hash uses weaker parameters.

### Session state

- Refresh tokens are hashed (SHA-256) before storage in Redis.
- Users can list and individually revoke active sessions via `DELETE /api/auth/sessions/{id}`.
- Sessions are invalidated globally on password change and account ban.

### Social login (Google OAuth / OIDC)

- "Sign in with Google" is supported as an OpenID Connect client (Authorization Code + PKCE), with the returned `id_token` verified against Google's published JWKS (RS256).
- This is consumer social login only — it is not an org-level identity-federation feature. See [Known Limitations](#known-limitations--out-of-scope-v1) for the distinction from enterprise SSO/SAML.

---

## Authorization Model

Authorization uses a **26-capability × 6-role** matrix evaluated per request in `shared_kernel/auth/permissions.py`.

| Role | Scope |
|------|-------|
| `ADMIN` | Global |
| `ORG_OWNER` | Organization |
| `ORG_MEMBER` | Organization |
| `PROJECT_OWNER` | Project |
| `PROJECT_MEMBER` | Project |
| `GUEST` | Chatroom |

Key invariants:

- **`KEY_VIEW_PLAINTEXT` is universally denied** to every role including `ADMIN` — plaintext provider keys are never returned by any endpoint after initial upload.
- The **original creator (OC) role is per-organization**, not instance-wide: an OC cannot be demoted or removed from their organization, and cannot be hard-deleted while doing so would leave the organization without one. Note: an admin `ban` action is **not** currently blocked for an OC — banning bypasses this protection and should be treated with the same caution as removal.
- Email verification is required before creating organizations or projects. Accepting a guest invitation is explicitly exempt — guest access is instead gated by room-level ACLs and the invite token itself.
- Chat send/export checks room participant membership at the time of the request.
- Admin impersonation sessions are **read-only** — the middleware rejects any mutating method (`POST`, `PUT`, `PATCH`, `DELETE`) while acting under an impersonation JWT.

---

## API Key Handling (BYO-Key)

SMAP stores third-party provider API keys (Anthropic, OpenAI, Gemini, Voyage, Cohere) on behalf of users.

### Storage

1. A per-record data-encryption key (DEK) is generated via Vault Transit `datakey`.
2. The plaintext DEK encrypts the API key with **AES-256-GCM** and a fresh 96-bit nonce per write.
3. The database stores: `ciphertext`, `nonce`, `dek_wrapped` (Vault-encrypted DEK), and an HMAC for integrity.
4. At use, the DEK is unwrapped by Vault Transit and the DEK itself is zeroed after decrypting the key. The resulting plaintext provider key is cached in an in-process TTL cache (60 seconds) to avoid re-unwrapping on every provider call, then evicted — it is **not** synchronously zeroized after each individual use, except on the explicit key-retest path, which does zero its buffer. Operators relying on a hard "no plaintext survives the call" guarantee should be aware of this bounded in-memory window.

### What is never stored or returned

- Plaintext keys are **never** persisted to the database, logs, or response bodies after the initial upload request completes.
- The only stored human-readable form is a masked preview (first 7 + last 4 characters, e.g., `sk-ant-...xE9a`).
- No "reveal key" endpoint exists; this capability is absent from the authorization matrix by design.

### Key rotation

There is no single "rotate" action — a key is replaced by deleting the old record and uploading a new one via the UI. Within a key group, keys are tried in priority order (reorderable via the UI); on a failed provider call (HTTP 429, 500/502/503, or quota exhaustion) the router automatically advances to the next key in the group using exponential backoff.

---

## Secrets & Encryption at Rest

| Secret | Storage | Access |
|--------|---------|--------|
| JWT signing key | Vault Transit | Signing only; key never leaves Vault |
| Provider API keys | AES-256-GCM, DEK in Vault Transit | Decrypted on use; see the plaintext-caching note under [Key rotation](#api-key-handling-byo-key) |
| Guest link tokens | CSPRNG-generated opaque token, stored verbatim, compared with constant-time `hmac.compare_digest` | Not treated as secret material by design — guest access is additionally gated by room ACLs. (Vault Transit also exposes `sign_guest_link`/`verify_guest_link` methods for a signed-token scheme, but they are not currently wired into any request path.) |
| PostgreSQL credentials | Environment variables (`SMAP_DB_DSN` / `SMAP_DB_PASSWORD`), compose-injected | Loaded at boot. A Vault KV source for DB credentials is registered but currently returns no values — see [Known Limitations](#known-limitations--out-of-scope-v1) |
| MinIO credentials | Environment variables (root credentials) | Loaded at boot. Bootstrap tooling seeds a scoped service-account entry in Vault KV, but no runtime code currently reads it back — all clients use the root credentials directly. See [Known Limitations](#known-limitations--out-of-scope-v1) |
| Application secrets (CAPTCHA, Google OAuth client secret, SMTP) | Vault KV | Loaded at boot |
| Other application config | Environment variables | Loaded at boot |

**No secrets should be committed to Git.** `.env`, `*.pem`, `*.key`, `*.crt`, and `secrets/` are all git-ignored.

For production, use Vault AppRole authentication (`SMAP_VAULT_ROLE_ID` + `SMAP_VAULT_SECRET_ID`). The `SMAP_VAULT_DEV_TOKEN=root` setting is for local development only and must never be used in production.

---

## Transport Security

All traffic is TLS-terminated at the Nginx reverse proxy.

- **TLS 1.2 minimum**, TLS 1.3 preferred.
- **AEAD cipher suites only**: `ECDHE-ECDSA-AES128-GCM-SHA256`, `ECDHE-RSA-AES128-GCM-SHA256`, `ECDHE-ECDSA-AES256-GCM-SHA384`, `ECDHE-RSA-AES256-GCM-SHA384`, `ECDHE-ECDSA-CHACHA20-POLY1305`, `ECDHE-RSA-CHACHA20-POLY1305`.
- TLS session tickets disabled.
- HTTP → HTTPS redirect enforced.
- **HSTS**: `max-age=31536000; includeSubDomains; preload`.

Internal service-to-service communication (app ↔ PostgreSQL, Redis, Vault, Qdrant, Neo4j) runs on the Docker internal network. For hardened deployments, enable TLS on each internal service and configure the respective DSN/URL with TLS parameters.

---

## API & Network Hardening

### Security headers

| Header | Value |
|--------|-------|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; ...` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains; preload` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | camera, microphone, geolocation, payment all denied |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |

### Rate limiting

| Bucket | Limit |
|--------|-------|
| `/api/auth/*` | 10 req/min per IP |
| Chat message send | 60 req/min per user |
| File uploads (TUS + attachments) | 10 req/min per user |
| All other endpoints | 300 req/min per user |
| WebSocket connections | 5 concurrent per user |

Limits are enforced via Redis sliding-window counters and are configurable via `SMAP_LIMIT_*` environment variables.

### IP banning

CIDR-based IP bans are stored in PostgreSQL, loaded into an in-memory cache with 5-second freshness, and checked as the first meaningful middleware step. Banned IPs receive a `403` before any authentication processing.

### CORS

By default SMAP serves the frontend and API from the same origin — no CORS configuration is needed or enabled. If you must serve from separate origins, set `SMAP_SEC_CORS_ORIGINS` to a JSON list of allowed origins (e.g. `["https://app.example.com","https://admin.example.com"]`); all listed origins are permitted by the CORS middleware. Note: a few features that build absolute URLs (e.g., invite links) use only the first configured origin as the "public" origin, so list your primary user-facing origin first.

### CSRF

Most API authentication uses `Authorization: Bearer` headers, which are not subject to CSRF. The exceptions are `POST /api/auth/refresh` and `POST /api/auth/logout`, which accept the refresh token via the `HttpOnly` cookie as a fallback when no body is supplied. These two endpoints are not protected by a dedicated CSRF token; the mitigation is the cookie's `SameSite=Lax` (default) or `Strict` attribute, which prevents the cookie from being sent on cross-site requests that could trigger these actions.

### Trusted proxy resolution

`X-Forwarded-For` is parsed only from IPs in the `SMAP_SEC_TRUSTED_PROXIES` CIDR list (default: `127.0.0.1/32, ::1/128, 172.16.0.0/12` — loopback plus the Docker bridge range). The right-most non-proxy address is used as the real client IP. Misconfiguring this setting can allow IP spoofing.

---

## Input Validation & Sanitization

- All request bodies are validated by **Pydantic v2** schemas before reaching application logic. Invalid payloads are rejected with `422 Unprocessable Entity`.
- User-generated Markdown is rendered server-side by `markdown-it-py` (the Python port of markdown-it) and then sanitized by **Bleach** with an explicit allowlist of tags and attributes. The frontend uses the JS `markdown-it` for the same CommonMark rendering.
- The `style` attribute is excluded from the sanitizer's allowlist, so CSS payloads (`url(...)`, `@import`, `expression(...)`) are stripped along with any other inline style content.
- The frontend applies **DOMPurify** as a secondary defense before inserting any server-provided HTML into the DOM.
- The **RE2** engine (Google RE2 via `google-re2`) is used for user-configurable regex matching in the workflow rule engine (event/condition matching) to prevent ReDoS. One fallback path (`contexts/workflow/application/event_dispatch.py`) reverts to Python's backtracking `re` engine if a pattern fails to compile under RE2 — that path does not carry the same ReDoS guarantee. Regexes elsewhere in the codebase operate on trusted, non-user-supplied patterns and use the standard library `re` module.
- UUIDs passed as path parameters are validated structurally before any database lookup.

---

## File Uploads & Object Storage

- **Single-shot uploads**: 32 MB maximum per file.
- **Resumable uploads (TUS protocol)**: 1 GiB hard cap.
- All uploads require chatroom membership verification before acceptance.
- Files are stored in MinIO (S3-compatible) with a 3-day TTL for chat attachments.
- **Malware scanning.** Chat attachments and RAG source documents flow through a `scan_status` pipeline backed by a built-in ClamAV adapter (INSTREAM). Scanning is **off by default** — when disabled, files are marked clean — and is enabled with `SMAP_SEC_FILE_SCAN_ENABLED=true` plus `SMAP_SEC_CLAMAV_HOST` / `SMAP_SEC_CLAMAV_PORT`. Quarantined files are not served.
- Content-Type is enforced server-side at download time via an explicit allowlist — any stored MIME type outside the allowlist is served as `application/octet-stream` with a forced attachment disposition, regardless of what the client declared at upload. Uploads are not currently validated against the file's actual content (no magic-byte sniffing) at ingest time.

---

## Admin & Privileged Operations

Nearly every admin endpoint requires the `ADMIN` role via the shared `require_admin()` dependency, checked before any handler logic runs. (The IP-ban router defines its own locally-scoped equivalent of the same name; two knowledge-graph endpoints do an inline `principal.is_admin` check instead of using the shared dependency — all three enforce the same role requirement.)

| Operation | Notes |
|-----------|-------|
| List / search users | Read-only |
| Ban / unban user | Logged; triggers JTI denylist flush for target user |
| Ban / unban IP (CIDR) | Takes effect within 5 seconds |
| Promote / demote admin | Reversible; logged |
| Force-transfer original creator (per organization) | Logged; the transfer requires a target member, so this operation never leaves an organization without an original creator |
| Hard-delete user | 60-day soft-delete window before permanent removal |
| Start / end impersonation session | `impersonated_by` claim written to the JWT for the session's duration and audit trail; there is no separate endpoint to list past impersonation sessions beyond the audit log |

Admin impersonation is explicitly **read-only**: the auth middleware rejects mutating HTTP methods on tokens carrying an `impersonated_by` claim.

---

## Audit Logging

All security-sensitive actions emit structured audit events written to the `audit_logs` table:

- Authentication events: login, failed login, logout, password change, token refresh
- Session events: single-session revocation. Session creation and bulk revocation (e.g., "kill all sessions" on ban or password change) are not separately logged — they are side effects folded into their parent event (`auth.login.success`, `admin.ban_user`, `auth.password_changed`).
- Key lifecycle: upload, test (success/failure), delete. There is no "rotate" action — see [Key rotation](#api-key-handling-byo-key).
- User management: creation, ban, unban, role change, deletion
- Organization/Project: create, update, delete, membership changes
- Admin operations: all actions including impersonation start/end
- IP ban operations

Audit records are append-only from the application's perspective, enforced by PostgreSQL `BEFORE INSERT/UPDATE/DELETE` triggers that raise an exception unless the executing role is the dedicated `smap_audit_retention` role (used only by the nightly retention job via `SET ROLE`). Retention is currently a fixed 365-day purge run by that job; it is not yet exposed as an operator-configurable setting.

---

## Dependency Management

- Backend: `pyproject.toml` pins most direct dependencies to an exact minor version (e.g., `fastapi==0.137.*`); a handful of dependencies (e.g. `starlette`, `protobuf`) use open ranges, and dev-only extras use range pins.
- Frontend: `package.json` pins most dependencies to an exact version; a minority use `^` caret ranges (e.g. `@heroicons/vue`, `tailwindcss`).
- Dependabot is configured to open grouped PRs weekly for both `backend/` and `frontend/`.
- Run `pip audit` (backend) and `pnpm audit` (frontend) in CI to catch known CVEs before merge.

---

## Self-Hosted Operator Checklist

Before going to production, verify:

- [ ] `SMAP_VAULT_DEV_TOKEN` is **not set**; AppRole credentials are configured instead.
- [ ] PostgreSQL password is changed from the compose default (`smap`); update `SMAP_DB_DSN` accordingly.
- [ ] `SMAP_NEO4J_PASSWORD` is changed from the default (`neo4jneo4j`).
- [ ] Redis is running with `requirepass` authentication.
- [ ] Qdrant is behind the internal Docker network or configured with TLS + API key (`SMAP_QDRANT_API_KEY`).
- [ ] `SMAP_APP_DOCS_ENABLED=false` (disables `/docs` and `/redoc` in production).
- [ ] TLS certificates are valid and the Nginx `ssl_certificate` / `ssl_certificate_key` paths are correct.
- [ ] `SMAP_SEC_TRUSTED_PROXIES` matches your actual reverse-proxy CIDR(s) exactly.
- [ ] MinIO root credentials have been rotated from the compose default (`minioadmin`). Runtime currently authenticates with these root credentials directly — see [Known Limitations](#known-limitations--out-of-scope-v1) regarding the not-yet-wired Vault service account.
- [ ] PostgreSQL backups are encrypted at rest and restore has been tested.
- [ ] Log output does not include raw request bodies containing user content or credentials (review `SMAP_LOG_LEVEL` and logger configuration).
- [ ] Vault unseal procedure (Shamir 3-of-5) is documented and recovery keys are stored securely offline.
- [ ] SMTP credentials for email verification are configured and deliverability tested.
- [ ] CAPTCHA (hCaptcha or Cloudflare Turnstile) keys are configured in Vault KV.

---

## Known Limitations & Out-of-Scope (v1)

| Item | Status |
|------|--------|
| Multi-factor authentication (MFA/TOTP) | Not in v1 scope |
| Enterprise SSO / SAML / org-level OIDC federation | Not in v1 scope. Google "Sign in with Google" (consumer OIDC social login) is already supported — see [Authentication & Session Management](#authentication--session-management) |
| Guest link revocation without room deletion | Not supported; mitigate by deleting the room or banning the guest user |
| CSP `wasm-unsafe-eval` | Currently set in `script-src`, but no shipped browser-bundle dependency was found that requires it; candidate for tightening on a future review rather than a confirmed hard requirement |
| Vault-KV-backed runtime credentials for PostgreSQL and MinIO | Bootstrap tooling seeds the expected Vault KV paths, but no runtime code reads them back yet; both currently authenticate with environment-variable credentials (MinIO uses the root account). See [Secrets & Encryption at Rest](#secrets--encryption-at-rest) |
| Provider-API-key plaintext lifetime | Cached in-process for up to 60 seconds after Vault unwrap rather than zeroized immediately after each call. See [API Key Handling](#api-key-handling-byo-key) |
| Workflow event-dispatch regex fallback | Falls back to Python's backtracking `re` engine (losing RE2's ReDoS protection) if a user-supplied pattern fails to compile under RE2, in `contexts/workflow/application/event_dispatch.py` |
| Cross-origin (multi-domain) deployments | The CORS allow-list itself supports multiple origins (`SMAP_SEC_CORS_ORIGINS`), but this is not a primary, fully-tested deployment topology — some URL-building features assume a single primary origin (see [CORS](#api--network-hardening)) |
| MFA on admin operations | Not in v1 scope |
