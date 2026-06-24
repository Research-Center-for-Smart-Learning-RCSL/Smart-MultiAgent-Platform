---
name: check-security
description: Audit changed files for security vulnerabilities — injection, AuthZ/IDOR, secret leaks, input validation gaps, and N+1 query patterns. Use before merging or deploying.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree is clean) for security vulnerabilities. This project handles user-provided LLM API keys encrypted via Vault Transit — any leak is critical. Produce a structured report.

## Scope Detection

1. Run `git diff --name-only HEAD` to find uncommitted changes.
2. If empty, run `git diff --name-only HEAD~1 HEAD` to use the last commit.
3. Read each changed file in full. For API routes, also read the corresponding facade and service to trace the full call chain.

## Dimensions to Check

### 1. Injection

**SQL Injection:**
- Flag any raw SQL string concatenation or f-string interpolation in queries.
- All queries must use SQLAlchemy parameterized expressions or `text()` with `:param` binding.
- Check Alembic migrations for raw `op.execute()` with string interpolation.

**XSS (Cross-Site Scripting):**
- Flag any `v-html` usage outside the approved allowlist (only `ChatroomView.vue` via `renderMarkdown.ts`).
- Verify that `renderMarkdown.ts` passes output through DOMPurify before rendering.
- Flag any direct DOM manipulation (`innerHTML`, `insertAdjacentHTML`, `document.write`).

**Command Injection:**
- Flag any use of `subprocess`, `os.system`, `os.popen`, or shell=True.
- The only allowed subprocess usage is in `services/mcp_supervisor/` for gVisor container management.

### 2. Authorization & IDOR

**Multi-tenant boundary enforcement:**
- Every API endpoint that accesses org-scoped or project-scoped data must verify membership BEFORE returning data.
- Trace the call chain: route → facade → service — verify that the service checks `org_id`/`project_id` ownership against the authenticated user's memberships.
- Flag any endpoint that takes a resource ID (org_id, project_id, agent_id, chatroom_id, key_id, workflow_id) from the URL path or query params without verifying the caller has access.

**Privilege escalation:**
- Flag any endpoint that modifies roles (admin, owner, member) without checking the caller's role is sufficient.
- Flag any admin-only endpoint missing the admin role dependency (`Depends(require_admin)`).
- Check impersonation flows: the impersonating admin's actions must be audit-logged.

**Object-level access:**
- For message edit/delete: verify the 5-minute edit window for non-moderators.
- For file downloads: verify presigned URLs are scoped to the correct chatroom.
- For workflow runs: verify the caller is a project member, not just any authenticated user.

### 3. Secret Leaks

**Log safety:**
- Flag any `logger.*` or `print()` call that includes variables named `password`, `secret`, `token`, `key`, `api_key`, `dek`, `plaintext`, `credential`, or `authorization`.
- Flag logging of full request headers (may contain Authorization bearer tokens).
- Flag logging of full request/response bodies for auth endpoints.

**Response safety:**
- Flag any API response that returns `password_hash`, `secret_id`, `dek_wrapped`, or Vault tokens.
- Flag any error response that leaks internal paths, stack traces, or database schema details in production mode.

**Code safety:**
- Flag hardcoded strings that look like secrets (API keys, tokens, passwords) — patterns: `sk-`, `pk-`, `ghp_`, `Bearer `, base64 strings > 32 chars.
- Flag `.env` files or credential files that are not in `.gitignore`.

### 4. Input Validation

**API boundary validation:**
- Every route handler must use a Pydantic model for request body validation — flag raw `dict` or `Request.json()`.
- Flag missing `max_length` on string fields that will be stored in the database.
- Flag missing `ge=0` / `le=N` on numeric fields used for pagination (limit, offset).
- Flag file upload endpoints without MIME type validation and size limits.

**Path/query parameter safety:**
- Flag UUID parameters accepted as plain strings without UUID validation.
- Flag any parameter used in filesystem paths without sanitization (path traversal).

### 5. N+1 Queries & Data Safety

**N+1 detection:**
- Flag loops that execute a database query per iteration (e.g., `for item in items: await repo.get(item.id)`).
- Flag relationship access on ORM objects outside of a `selectinload`/`joinedload` context.
- Suggest batch queries (`WHERE id IN (...)`) as replacements.

**Transaction safety:**
- Flag multi-step write operations (create + update + delete) that are not wrapped in a single transaction.
- Flag read-then-write patterns without optimistic locking (check-then-act race conditions).

**Data exposure:**
- Flag queries that `SELECT *` or return full ORM objects when only specific fields are needed.
- Flag responses that include related objects not requested by the client (over-fetching).

## Output Format

```markdown
## Security Audit Report

### CRITICAL (blocks deployment)
- [Injection] file:line — description
- [AuthZ] file:line — description

### HIGH (fix before next release)
- [Secrets] file:line — description

### MEDIUM (should fix)
- [Validation] file:line — description
- [N+1] file:line — description

### Summary
- Files checked: N
- Issues: N critical, N high, N medium
- AuthZ check coverage: N/M endpoints verified
```

Classify as **CRITICAL** if exploitable without authentication or if it could leak API keys. **HIGH** for AuthZ gaps, secret exposure in logs, or missing input validation on sensitive endpoints. **MEDIUM** for N+1 queries, over-fetching, or missing validation on non-sensitive fields.
