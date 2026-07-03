---
name: check-security
description: Professional-grade security audit — 13 dimensions covering injection, AuthZ, secrets, SSRF, WebSocket, JWT, CSRF, timing attacks, resource exhaustion, supply chain, and LLM/agent-specific attacks (prompt injection, key exfiltration, cross-room leakage). Use before merging or deploying, as the conditional gate in /build's Definition of Done, or whenever the user asks to check for vulnerabilities, review security, or audit auth/tenant/key handling. This project handles user-provided LLM API keys encrypted via Vault Transit.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree
is clean) for security vulnerabilities across 13 dimensions. For API routes, trace the
full call chain (route → facade → service → repository) to verify security controls.
Produce a structured report of findings with traced attack scenarios.

## Threat Model

Attacker personas, in priority order for this product:

1. **Cross-tenant authenticated user** — a legitimate user of another org/project trying
   to reach data, keys, agents, or rooms that aren't theirs. The primary persona: SMAP
   is multi-tenant and BYO-key, so a tenant boundary breach exposes other customers'
   provider API keys and conversations.
2. **Low-privilege insider** — a member trying to reach admin/owner capabilities,
   other members' data, or moderation bypasses.
3. **Malicious content author** — anyone who can put attacker-controlled text where an
   agent or another user will process it: chat messages, agent instructions, RAG
   documents, MCP tool output, file uploads.
4. **Sandboxed MCP tool** — code running inside the gVisor sandbox trying to escape via
   the egress proxy, the supervisor, or its tool-call channel.
5. **Unauthenticated outsider** — classic internet attacker against auth, session, and
   input surfaces.

Judge every finding by asking which persona exploits it and what they gain. API key
exfiltration and tenant boundary breach are always CRITICAL.

## Ground Rules

1. **Trace the exploit before reporting.** For each candidate finding, attempt to walk
   the attack end-to-end: entry point → missing/defeated control → impact. If the walk
   completes, the finding is **confirmed**. If you cannot complete it but cannot find
   the compensating control either, report it as **plausible** — for security the cost
   of a missed vulnerability outweighs a flagged uncertainty, so plausible findings are
   kept and marked, never silently dropped.
2. **A vulnerability does not age into acceptability.** Classify findings Introduced
   (caused by this change) vs Pre-existing (already present in touched code) — but
   unlike quality debt, a Pre-existing CRITICAL still blocks deployment. The
   classification routes ownership and urgency of the fix, not whether it matters.
3. **Separate vulnerabilities from hardening.** A finding needs a concrete attack
   scenario (persona, input, gain). Defense-in-depth improvements with no current
   attack path (adding a second validation layer, tightening an already-safe config)
   go in a separate Hardening section so they don't dilute the severity signal.
4. **Absence of evidence is a finding about coverage, not safety.** If a control could
   not be located (e.g., the rate limiter for an endpoint), say "not found where
   expected" with the locations checked — don't assume it exists elsewhere, and don't
   claim it's missing without having looked.

## Scope Detection

1. Collect changed files: `git status --porcelain` (staged, unstaged, AND untracked —
   new endpoints and new services are the highest-risk files and are invisible to
   `git diff HEAD`). If the tree is clean, use `git diff --name-only HEAD~1 HEAD`.
2. Exclude deleted files and generated code (the generated api-client). Everything else
   is in scope — including non-code files (see change-type triggers below).
3. Read each changed file in full. For API route files, also read the corresponding
   facade, service, and repository to trace the full authorization and data flow path.
4. For frontend changes, check if the change introduces new user input paths that reach
   the backend.
5. **Change-type triggers** — when the diff touches these, run the matching extra audit:
   - `pyproject.toml` / `package.json` / lockfiles → dimension 13a (supply chain)
   - `deploy/`, nginx configs, compose files, Dockerfiles → dimension 13b (deployment config)
   - Alembic migrations → dimension 1 (`op.execute` injection) plus data-exposure review
6. **Large scope** (more than ~10 changed files): fan out subagents — one per Part —
   then merge, dedupe, and apply Ground Rule 1 to the merged set in the main context.

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

### 3. Command Injection & Unsafe Deserialization

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
- Flag `repr()` or `str()` on objects that may contain secrets — including domain models with a key field reaching structured-log context.

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
- Flag any other backend code that makes HTTP requests to user-controlled URLs without validation — including URLs that arrive indirectly via LLM output or tool arguments (see dimension 13).
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
- Verify room/topic subscription checks membership at subscribe time AND that membership
  revocation unsubscribes — a stale subscription is a cross-room leak.

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
- Proxy header trust: `X-Forwarded-For` / `X-Real-IP` must be honored only from the
  configured proxy chain (NPM → nginx → backend) — a client-spoofable actor IP poisons
  audit logs and rate-limit keys.

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
- Flag missing budget/limit enforcement on LLM calls (per-run token caps, per-user
  concurrency) — with BYO keys, a runaway loop burns the customer's provider account.

---

## Part F — LLM & Agent Security

### 13. Agent Attack Surface

The product-specific dimension: agents process attacker-influenceable text with tool
access and provider credentials. Treat all of the following as untrusted input to the
LLM: chat messages, agent instructions authored by non-admin users, RAG document
content, MCP tool output, file contents, web content fetched via tools.

**Prompt injection paths:**
- Trace how untrusted text reaches a system prompt or tool-selection context. Flag
  concatenation of user/document/tool content into system-level instructions without a
  trust boundary (delimiters alone are not a control — assume they fail; ask what the
  blast radius is when they do).
- Flag agent-to-agent message flows where one agent's output becomes another's
  instructions without provenance tracking.

**Credential exfiltration via tools:**
- Provider API keys must never enter model-visible context: flag any code path where a
  decrypted key could appear in a prompt, a tool argument, a tool result, or an agent's
  conversation history.
- Flag tool schemas that accept arbitrary URLs or destinations an injected agent could
  use to exfiltrate context (the egress allowlist in dimension 8 is the control —
  verify tool-originated requests actually go through it).

**Isolation between rooms, agents, and tenants:**
- Flag any context assembly (history windows, RAG retrieval, memory features) that can
  pull content across chatroom, project, or org boundaries — retrieval filters must be
  scoped server-side, not by the agent's good behavior.
- Flag MCP tool output trusted as structured data without validation (a tool can return
  adversarial JSON/markdown).

**Insecure output handling:**
- LLM output is untrusted: verify it passes the same sanitization as user input when
  rendered (DOMPurify, dimension 2) and the same validation when executed (tool
  dispatch, workflow node parameters, SEL expressions).

### 13a. Supply Chain (when dependency manifests change)

- For each new or version-changed dependency: check for known advisories, suspicious
  name similarity to popular packages (typosquatting), and install-time scripts.
- Flag unpinned or range-widened versions in production dependencies.
- Flag dependencies added for functionality that already exists in the codebase or stdlib.

### 13b. Deployment Configuration (when deploy/nginx/compose files change)

- Flag CORS origin widening (`*` or scheme-relative), CSP weakening, or removed security headers.
- Flag containers gaining privileges: `privileged`, added capabilities, host mounts, host network.
- Flag newly exposed ports or services bound to `0.0.0.0` that were previously internal.
- Flag Vault policy capability additions (must trace to §7.6 of REQUIREMENTS.md and the Vault README).

---

## Output Format

```markdown
## Security Audit Report

**Scope:** N files checked (list files). Not covered: <excluded/skipped areas, if any>
**Threat model:** Multi-tenant BYO-key platform. API key leak = critical. Tenant boundary breach = critical.

### CRITICAL (blocks deployment — Introduced or Pre-existing alike)
- [IDOR][confirmed] file:line — endpoint returns agent without verifying project membership.
  Attack: authenticated user from org B enumerates agent IDs, reads org A's agent instructions.
  Fix: add project-membership filter in repository query.

### HIGH (fix before release)
- [Secrets][confirmed] file:line — logger.info includes `api_key` variable.
  Attack: anyone with log access harvests customer provider keys.
  Fix: log key_id only.

### MEDIUM (should fix)
- [Validation][plausible] file:line — missing max_length on `name`; could not confirm a
  DB-level constraint. Fix: add max_length=... to the Pydantic field.

### Hardening (no current attack path)
- [Defense-in-depth] file:line — suggestion.

### Summary
| Dimension | Critical | High | Medium |
|-----------|----------|------|--------|
| Injection (1-3) | 0 | 0 | 0 |
| Authorization (4-5) | 0 | 0 | 0 |
| Secrets (6-7) | 0 | 0 | 0 |
| Transport (8-10) | 0 | 0 | 0 |
| Input/Resource (11-12) | 0 | 0 | 0 |
| LLM/Agent/Chain (13) | 0 | 0 | 0 |
| **Total** | **0** | **0** | **0** |

### AuthZ Trace Coverage
| Endpoint | Facade | Service | Tenant filter verified |
|----------|--------|---------|----------------------|
| GET /api/agents/{id} | AgentsFacade.get | AgentService.get_by_id | Yes — WHERE project_id IN (...) |
```

Every finding carries: verdict (`confirmed` / `plausible` per Ground Rule 1), a
one-sentence attack scenario naming the persona, and a one-clause fix direction. The
AuthZ Trace Coverage table is mandatory for every new or modified endpoint in the diff.

**Clean result:** if no findings survive, say so explicitly with what was checked —
"13 dimensions over N files, AuthZ traced for M endpoints, no findings" — an empty
report is indistinguishable from an audit that didn't run.

**Severity rules:**
- **CRITICAL**: exploitable injection, IDOR/tenant boundary breach, API key leak path (including exfiltration via agent tools or prompts), authentication bypass, SSRF to internal network, cross-room/cross-tenant context leakage.
- **HIGH**: missing AuthZ on non-admin endpoint, secret in logs, JWT algorithm confusion, CSRF on state-changing endpoint, timing attack on auth, prompt-injection path into tool dispatch without validation, malicious dependency indicators.
- **MEDIUM**: missing input validation, resource exhaustion risk, missing rate limit, over-fetching, missing LLM budget caps, unpinned dependencies.
