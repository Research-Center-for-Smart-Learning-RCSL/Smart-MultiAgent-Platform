---
name: check-security
description: Professional-grade security audit — 13 dimensions covering injection, AuthZ, secrets, SSRF, WebSocket, JWT, CSRF, timing attacks, resource exhaustion, supply chain, and LLM/agent-specific attacks (prompt injection, key exfiltration, cross-room leakage). Use before merging or deploying, as the conditional gate in /build's Definition of Done, or whenever the user asks to check for vulnerabilities, review security, or audit auth/tenant/key handling. This project handles user-provided LLM API keys encrypted via Vault Transit.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree
is clean) for security vulnerabilities across 13 dimensions. For API routes, trace the
full call chain (route → facade → service → repository) to verify security controls.
Produce a structured report of findings with traced attack scenarios.

This skill is report-only — it changes no files and makes no commits. When it runs as a
gate inside `/build`, that skill owns the commits per the CLAUDE.md commit discipline.

The 13 dimensions live in `references/dimensions.md` — read it in full before auditing.
The threat model and ground rules below decide what a checklist hit actually means; a hit
is a candidate, never a finding, until its exploit is traced.

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

0. **Explicit scope wins.** If the caller supplied a file list or a base ref, use it —
   `git diff --name-only <base>...HEAD` plus `git status --porcelain` for anything still
   uncommitted. `/build` passes its task base commit here because it commits at
   milestones; the default detection below would then audit only the final milestone,
   which for a security gate means an unreviewed endpoint ships with a clean report
   attached. State the resolved scope in the report either way.
1. Otherwise collect changed files: `git status --porcelain` (staged, unstaged, AND
   untracked — new endpoints and new services are the highest-risk files and are invisible
   to `git diff HEAD`). If the tree is clean, use `git diff --name-only HEAD~1 HEAD` — and
   say so in the report, since that window is one commit wide.
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
6. **Large scope** (more than ~10 changed files): fan out subagents — one per Part of
   `references/dimensions.md` — then merge, dedupe, and apply Ground Rule 1 to the merged
   set in the main context.

## Dimensions

Read `references/dimensions.md`:

- **Part A — Injection**: 1 SQL injection, 2 XSS, 3 command injection and unsafe
  deserialization.
- **Part B — Authorization**: 4 AuthZ/IDOR (tenant boundary, privilege escalation,
  object-level access), 5 mass assignment.
- **Part C — Secrets**: 6 secret leaks (logs, responses, code), 7 timing attacks.
- **Part D — Protocol & Transport**: 8 SSRF, 9 WebSocket security, 10 JWT and session
  attacks.
- **Part E — Request & Resource Safety**: 11 CSRF and input validation, 12 resource
  exhaustion.
- **Part F — LLM & Agent**: 13 agent attack surface (prompt injection, credential
  exfiltration via tools, cross-room/tenant isolation, insecure output handling),
  13a supply chain, 13b deployment configuration.

## Output Format

```markdown
## Security Audit Report

**Scope:** N files checked (list files), resolved from <base ref / working tree / HEAD~1>.
Not covered: <excluded/skipped areas, if any>
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

Consumers: `/build`'s Definition of Done blocks on CRITICAL regardless of Introduced or
Pre-existing (Ground Rule 2), treats HIGH as fix-or-defer-with-user-agreement, and routes
MEDIUM and Hardening to FU-n. `/spec`'s security lens reads `references/dimensions.md` to
decide what a Security Considerations section must cover for the surfaces it touches.
