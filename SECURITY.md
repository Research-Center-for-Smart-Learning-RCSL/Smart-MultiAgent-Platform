# Security

This document describes the security architecture, controls, and disclosure process for SMAP.

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

- **Access tokens** — RS256-signed, 15-minute TTL (configurable via `SMAP_JWT_ACCESS_TTL_SECONDS`).
- **Refresh tokens** — 30-day rotating tokens; each use issues a new token and invalidates the old one.
- **Signing key** — Stored in HashiCorp Vault Transit engine. The private key never touches the filesystem or application memory beyond a single signing operation.
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

---

## Authorization Model

Authorization uses a **24-capability × 6-role** matrix evaluated per request in `shared_kernel/auth/permissions.py`.

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
- The **original creator** of the instance cannot be demoted or deleted.
- Email verification is required before creating organizations, projects, or accepting guest invitations.
- Chat send/export checks room participant membership at the time of the request.
- Admin impersonation sessions are **read-only** — the middleware rejects any mutating method (`POST`, `PUT`, `PATCH`, `DELETE`) while acting under an impersonation JWT.

---

## API Key Handling (BYO-Key)

SMAP stores third-party provider API keys (Anthropic, OpenAI, Gemini, Voyage, Cohere) on behalf of users.

### Storage

1. A per-record data-encryption key (DEK) is generated via Vault Transit `datakey`.
2. The plaintext DEK encrypts the API key with **AES-256-GCM** and a fresh 96-bit nonce per write.
3. The database stores: `ciphertext`, `nonce`, `dek_wrapped` (Vault-encrypted DEK), and an HMAC for integrity.
4. At use, the DEK is decrypted by Vault Transit, the plaintext key is used in memory, then zeroed immediately.

### What is never stored or returned

- Plaintext keys are **never** persisted to the database, logs, or response bodies after the initial upload request completes.
- The only stored human-readable form is a masked preview (first 7 + last 4 characters, e.g., `sk-ant-...xE9a`).
- No "reveal key" endpoint exists; this capability is absent from the authorization matrix by design.

### Key rotation

Keys can be rotated at any time via the UI. Failed provider calls (HTTP 429, 500–503, quota exhaustion) automatically rotate to the next key in a configured group using exponential backoff.

---

## Secrets & Encryption at Rest

| Secret | Storage | Access |
|--------|---------|--------|
| JWT signing key | Vault Transit | Signing only; key never leaves Vault |
| Provider API keys | AES-256-GCM, DEK in Vault Transit | Decrypt on use, zeroize after |
| Guest link tokens | Vault Transit encrypted state | Decrypt on verify |
| PostgreSQL credentials | Vault KV (`secret/smap/config`) | Runtime injection |
| MinIO credentials | Vault KV (service account) | Runtime injection |
| Application secrets | Vault KV or environment variables | Loaded at boot |

**No secrets should be committed to Git.** `.env`, `*.pem`, `*.key`, `*.crt`, and `secrets/` are all git-ignored.

For production, use Vault AppRole authentication (`SMAP_VAULT_ROLE_ID` + `SMAP_VAULT_SECRET_ID`). The `SMAP_VAULT_DEV_TOKEN=root` setting is for local development only and must never be used in production.

---

## Transport Security

All traffic is TLS-terminated at the Nginx reverse proxy.

- **TLS 1.2 minimum**, TLS 1.3 preferred.
- **AEAD cipher suites only**: `ECDHE-ECDSA-AES128-GCM-SHA256`, `ECDHE-RSA-AES128-GCM-SHA256`, `ECDHE-RSA-CHACHA20-POLY1305`.
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

By default SMAP serves the frontend and API from the same origin — no CORS configuration is needed or enabled. If you must serve from separate origins, set `SMAP_SEC_CORS_ORIGINS` to a single allowed origin. Multi-origin cross-origin access is not supported.

### CSRF

SMAP uses `Authorization: Bearer` headers (not cookies) for API authentication, which provides inherent CSRF protection. No additional CSRF tokens are required.

### Trusted proxy resolution

`X-Forwarded-For` is parsed only from IPs in the `SMAP_SEC_TRUSTED_PROXIES` CIDR list (default: `127.0.0.1/32`). The right-most non-proxy address is used as the real client IP. Misconfiguring this setting can allow IP spoofing.

---

## Input Validation & Sanitization

- All request bodies are validated by **Pydantic v2** schemas before reaching application logic. Invalid payloads are rejected with `422 Unprocessable Entity`.
- User-generated Markdown is rendered server-side by `markdown-it` and then sanitized by **Bleach** with an explicit allowlist of tags and attributes.
- CSS payloads (`url(...)`, `@import`, `expression(...)`) are stripped from all user content.
- The frontend applies **DOMPurify** as a secondary defense before inserting any server-provided HTML into the DOM.
- Regular expressions in the application use the **RE2** engine (Google RE2 via `google-re2`) to prevent ReDoS attacks.
- UUIDs passed as path parameters are validated structurally before any database lookup.

---

## File Uploads & Object Storage

- **Single-shot uploads**: 32 MB maximum per file.
- **Resumable uploads (TUS protocol)**: Cap configurable via `SMAP_MINIO_UPLOAD_MAX_BYTES`.
- All uploads require chatroom membership verification before acceptance.
- Files are stored in MinIO (S3-compatible) with a 3-day TTL for chat attachments.
- A `scan_status` field is reserved for anti-malware integration; operators may plug in a scanning service.
- Content-Type is enforced server-side; client-supplied MIME types are not trusted.

---

## Admin & Privileged Operations

Every admin endpoint requires the `ADMIN` role checked by `_require_admin()` as the first operation.

| Operation | Notes |
|-----------|-------|
| List / search users | Read-only |
| Ban / unban user | Logged; triggers JTI denylist flush for target user |
| Ban / unban IP (CIDR) | Takes effect within 5 seconds |
| Promote user to admin | Reversible; logged |
| Force-transfer original creator | Logged; cannot leave the instance without an original creator |
| Hard-delete user | 60-day soft-delete window before permanent removal |
| View impersonation session | Read-only; `impersonated_by` claim written to JWT for full audit trail |

Admin impersonation is explicitly **read-only**: the auth middleware rejects mutating HTTP methods on tokens carrying an `impersonated_by` claim.

---

## Audit Logging

All security-sensitive actions emit structured audit events written to the `audit_events` table:

- Authentication events: login, failed login, logout, password change, token refresh
- Session events: creation, revocation (single and bulk)
- Key lifecycle: upload, test, rotate, delete
- User management: creation, ban, unban, role change, deletion
- Organization/Project: create, update, delete, membership changes
- Admin operations: all actions including impersonation start/end
- IP ban operations

Audit records are append-only from the application's perspective. Retention policy is configurable; the database role used by the application does not have `DELETE` permission on `audit_events`.

---

## Dependency Management

- Backend: `pyproject.toml` pins exact minor versions (e.g., `fastapi==0.115.*`).
- Frontend: `package.json` pins exact versions.
- Dependabot is configured to open grouped PRs weekly for both `backend/` and `frontend/`.
- Run `pip audit` (backend) and `pnpm audit` (frontend) in CI to catch known CVEs before merge.

---

## Self-Hosted Operator Checklist

Before going to production, verify:

- [ ] `SMAP_VAULT_DEV_TOKEN` is **not set**; AppRole credentials are configured instead.
- [ ] `SMAP_NEO4J_PASSWORD` is changed from the default (`neo4jneo4j`).
- [ ] Redis is running with `requirepass` authentication.
- [ ] Qdrant is behind the internal Docker network or configured with TLS + API key (`SMAP_QDRANT_API_KEY`).
- [ ] `SMAP_APP_DOCS_ENABLED=false` (disables `/docs` and `/redoc` in production).
- [ ] TLS certificates are valid and the Nginx `ssl_certificate` / `ssl_certificate_key` paths are correct.
- [ ] `SMAP_SEC_TRUSTED_PROXIES` matches your actual reverse-proxy CIDR(s) exactly.
- [ ] MinIO root credentials have been rotated; runtime uses the Vault-provisioned service account.
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
| SSO / SAML / OIDC | Not in v1 scope |
| Guest link revocation without room deletion | Not supported; mitigate by deleting the room or banning the guest user |
| CSP `unsafe-inline` for styles | Present; tightening requires nonce injection refactor |
| CSP `wasm-unsafe-eval` | Required for WASM dependencies; not removable without changing dependencies |
| Cross-origin (multi-domain) deployments | Not supported in v1 |
| MFA on admin operations | Not in v1 scope |
