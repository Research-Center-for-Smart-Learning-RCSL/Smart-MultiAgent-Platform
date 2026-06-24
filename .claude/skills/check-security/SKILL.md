---
name: check-security
description: Professional-grade security audit — 12 dimensions covering injection, AuthZ, secrets, SSRF, WebSocket, JWT, CSRF, timing attacks, and resource exhaustion. Use before merging or deploying. This project handles user-provided LLM API keys encrypted via Vault Transit.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree is clean) for security vulnerabilities across 12 dimensions. For API routes, trace the full call chain (route → facade → service → repository) to verify security controls. Produce a structured report.

## Scope Detection

1. Run `git diff --name-only HEAD` to find uncommitted changes.
2. If empty, run `git diff --name-only HEAD~1 HEAD` to use the last commit.
3. Read each changed file in full.
4. For API route files, also read the corresponding facade, service, and repository to trace the full authorization and data flow path.
5. For frontend changes, check if the change introduces new user input paths that reach the backend.

---

## Part A — Injection

### 1. SQL Injection

- Flag any raw SQL string concatenation or f-string interpolation in queries.
- All queries must use SQLAlchemy parameterized expressions or `text()` with `:param` binding.
- Check Alembic migrations for `op.execute()` with string interpolation.
- Flag any use of `.format()` or `%` string formatting with SQL fragments.
- Check raw queries in Neo4j driver calls — must use parameter binding (`$param`), not f-strings.

### 2. XSS (Cross-Site Scripting)

- Flag any `v-html` usage outside the approved allowlist (only `ChatroomView.vue` via `renderMarkdown.ts`).
- Verify that `renderMarkdown.ts` passes ALL output through DOMPurify before rendering.
- Flag any direct DOM manipulation: `innerHTML`, `insertAdjacentHTML`, `document.write`, `outerHTML`.
- Flag any backend endpoint that returns `Content-Type: text/html` with user-controlled content.
- Flag template literal injection in frontend where user input is interpolated into HTML strings.
- Check that CSP header (`Content-Security-Policy`) does not include `unsafe-eval` in `script-src`.

### 3. Command Injection

- Flag any use of `subprocess`, `os.system`, `os.popen`, `shlex` with user input, or `shell=True`.
- The ONLY allowed subprocess usage is in `services/mcp_supervisor/` for gVisor container management — verify arguments are not user-controlled.
- Flag any `eval()`, `exec()`, `compile()`, `__import__()` with dynamic input.
- Flag `yaml.load()` without `Loader=SafeLoader` — unsafe deserialization.
- Flag `pickle.loads()` on any untrusted input.

---

## Part B — Authorization

### 4. AuthZ & IDOR (Insecure Direct Object Reference)

**Multi-tenant boundary enforcement:**
- Every API endpoint accessing org-scoped or project-scoped data must verify the caller's membership BEFORE returning data.
- Trace the full chain: route → facade → service → repository query. Verify that the repository query includes `WHERE org_id = :caller_org_id` (or equivalent project-level filter).
- Flag any endpoint that takes a resource ID (`org_id`, `project_id`, `agent_id`, `chatroom_id`, `key_id`, `workflow_id`) from URL path or query params without verifying caller access.
- Flag any endpoint that returns a list without filtering by the caller's org/project scope.

**Privilege escalation:**
- Flag any endpoint that modifies roles (admin, owner, member) without checking the caller's role is sufficient.
- Flag admin-only endpoints missing `Depends(require_admin)`.
- Flag any endpoint that allows self-promotion (user changing their own role to admin/owner).
- Check impersonation flows: impersonating admin's actions must be audit-logged with the real admin's identity.

**Object-level access:**
- For message edit/delete: verify the 5-minute edit window for non-moderators (R13.21).
- For file downloads: verify presigned URLs are scoped to the correct chatroom and expire.
- For workflow runs: verify caller is a project member, not just any authenticated user.
- For key operations: verify the key belongs to the caller's org, not just that the key ID exists.

### 5. Mass Assignment (Over-posting)

- Flag Pydantic request models that include fields the user should not control: `is_admin`, `role`, `org_id`, `created_by`, `is_verified`, `password_hash`.
- Flag `**kwargs` or `model.dict()` passed directly to ORM create/update without field filtering.
- Flag `PATCH` endpoints that accept arbitrary fields without an explicit allowlist.
- Verify that role changes go through dedicated endpoints with proper AuthZ, not through generic update endpoints.

---

## Part C — Secrets & Data Protection

### 6. Secret Leaks

**Log safety:**
- Flag any `logger.*` or `print()` that includes variables named: `password`, `secret`, `token`, `key`, `api_key`, `dek`, `plaintext`, `credential`, `authorization`, `secret_id`, `role_id`.
- Flag logging of full HTTP request headers (may contain `Authorization` bearer tokens).
- Flag logging of full request/response bodies for auth, key upload, or Vault endpoints.
- Flag `repr()` or `str()` on objects that may contain secrets.

**Response safety:**
- Flag any API response that returns `password_hash`, `secret_id`, `dek_wrapped`, Vault tokens, or MinIO root credentials.
- Flag error responses that leak: internal file paths, stack traces, database schema names, or SQL queries in production mode.
- Flag debug endpoints or OpenAPI docs accessible in production (`SMAP_APP_ENV=prod`).

**Code safety:**
- Flag hardcoded strings matching secret patterns: `sk-`, `pk-`, `ghp_`, `Bearer `, `hvs.`, AWS access keys (`AKIA`), base64 strings > 40 chars.
- Flag `.env` files, credential files, or private keys not in `.gitignore`.
- Flag secrets stored in frontend code (any API key, token, or password in `.ts`/`.vue` files).

### 7. Timing Attacks

- Password comparison must use constant-time comparison — flag any `==` comparison on password hashes or tokens.
- Token validation (API keys, session tokens, CSRF tokens) must use `hmac.compare_digest()` or equivalent — flag direct string comparison.
- Flag any authentication flow where the response time differs based on whether the user exists vs. wrong password (user enumeration via timing).

---

## Part D — Protocol & Transport Security

### 8. SSRF (Server-Side Request Forgery)

- The egress proxy (`services/egress_proxy/`) forwards HTTP requests from MCP sandboxes. Verify:
  - The allowlist (`mcp_egress_allowlist` table) is checked BEFORE forwarding.
  - Internal/private IP ranges are blocked: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254` (cloud metadata).
  - DNS resolution happens AFTER allowlist check (prevent DNS rebinding: resolve → check → re-resolve → forward to internal IP).
  - The `Host` header cannot be manipulated to reach internal services.
- Flag any other backend code that makes HTTP requests to user-controlled URLs without validation.
- Flag any URL parameter that is used directly in `httpx.get()`, `requests.get()`, or `urllib.request.urlopen()`.

### 9. WebSocket Security

5 WebSocket endpoints exist (`app/api/ws/`): chatroom, user, rag_configs, workflow_runs, admin_tail.

For each:
- Verify authentication token is validated on connection upgrade, not just on first message.
- Verify token expiration is re-checked periodically (not just at connect time).
- Flag missing `origin` header validation — prevent cross-site WebSocket hijacking.
- Flag missing message size limits — prevent memory exhaustion via large payloads.
- Flag missing rate limiting on incoming messages.
- Verify that disconnection on auth revocation (ban, session invalidation) is implemented.

### 10. JWT & Session Attacks

- Flag JWT verification that does not pin the algorithm — must reject `none` and `HS256` when expecting `RS256` (algorithm confusion attack).
- Flag JWT verification that does not validate `iss` (issuer) and `aud` (audience) claims.
- Verify that refresh tokens are rotated on use (one-time use) — flag reusable refresh tokens.
- Verify that JWT signing key rotation uses the `verify_overlap_days` window for graceful transition.
- Flag session cookies missing `Secure`, `HttpOnly`, or `SameSite` attributes.
- Flag any endpoint that accepts a JWT from query parameters (leaks in server logs and Referer headers).

---

## Part E — Request & Resource Safety

### 11. CSRF & Input Validation

**CSRF:**
- Verify all state-changing endpoints (POST, PUT, PATCH, DELETE) are protected by CSRF tokens or SameSite cookie policy.
- Flag any state-changing GET endpoint (GET should never modify data).
- Flag any endpoint that relies solely on cookie authentication without CSRF protection.

**Input validation:**
- Every route handler must use a Pydantic model for request body — flag raw `dict`, `Request.json()`, or `await request.body()`.
- Flag missing `max_length` on string fields stored in the database.
- Flag missing `ge=0` / `le=N` on numeric fields for pagination (`limit`, `offset`).
- Flag file upload endpoints without: MIME type validation, file size limits, filename sanitization.
- Flag UUID parameters accepted as plain strings without UUID type validation.
- Flag any parameter used in filesystem paths without path traversal sanitization (`../`).
- Flag filenames from user uploads used directly in storage paths — must sanitize or generate new names.

**File upload specifics (TUS + direct):**
- Flag missing anti-virus scan integration for uploaded files.
- Flag missing checks for: zip bombs (compression ratio), symlinks in archives, oversized TUS chunks.
- Flag presigned upload URLs without expiration.

### 12. Resource Exhaustion

- Flag unbounded database queries — every `SELECT` must have a `LIMIT` clause or pagination.
- Flag regex patterns applied to user input without timeout or length limits (ReDoS).
- Flag endpoints that accept unbounded list/array inputs without `max_items` validation.
- Flag recursive functions without depth limits (especially in workflow execution, subagent spawning, instruct chains).
- Flag missing concurrency limits on per-user WebSocket connections (`ws_concurrent_per_user`).
- Flag background tasks (Arq workers) without timeout configuration.
- Flag any `while True` or unbounded loop in request handlers.
- Check that GraphRAG build operations have timeout and memory limits.

---

## Output Format

```markdown
## Security Audit Report

**Scope:** N files checked (list files)
**Threat model:** Multi-tenant BYO-key platform. API key leak = critical. Tenant boundary breach = critical.

### CRITICAL (blocks deployment)
- [SQLi] file:line — f-string in raw SQL query
- [IDOR] file:line — endpoint returns agent without verifying project membership

### HIGH (fix before release)
- [Secrets] file:line — logger.info includes `api_key` variable
- [SSRF] file:line — user-controlled URL passed to httpx.get without allowlist check

### MEDIUM (should fix)
- [Validation] file:line — missing max_length on `name` field (VARCHAR overflow)
- [Resource] file:line — unbounded SELECT without LIMIT

### Summary
| Dimension | Critical | High | Medium |
|-----------|----------|------|--------|
| Injection (1-3) | 0 | 0 | 0 |
| Authorization (4-5) | 0 | 0 | 0 |
| Secrets (6-7) | 0 | 0 | 0 |
| Transport (8-10) | 0 | 0 | 0 |
| Input/Resource (11-12) | 0 | 0 | 0 |
| **Total** | **0** | **0** | **0** |

### AuthZ Trace Coverage
| Endpoint | Facade | Service | Tenant filter verified |
|----------|--------|---------|----------------------|
| GET /api/agents/{id} | AgentsFacade.get | AgentService.get_by_id | Yes — WHERE project_id IN (...) |
```

**Severity rules:**
- **CRITICAL**: exploitable injection, IDOR/tenant boundary breach, API key leak path, authentication bypass, SSRF to internal network.
- **HIGH**: missing AuthZ on non-admin endpoint, secret in logs, JWT algorithm confusion, CSRF on state-changing endpoint, timing attack on auth.
- **MEDIUM**: missing input validation, N+1 queries, resource exhaustion risk, missing rate limit, over-fetching.
