---
name: verify
description: Build/launch/drive the SMAP frontend against the local dev stack to observe runtime behavior (Playwright + the e2e harness).
---

# Verifying the SMAP frontend

Runtime observation for the Vue frontend. The e2e harness under `frontend/e2e/`
is the handle: `playwright.config.ts` auto-starts Vite (`pnpm run dev`, port 5173,
`reuseExistingServer` on) and runs `global-setup.ts`, which logs in as the seed
users and provisions a fresh org/project/agent/workspace/chatroom/workflow, writing
IDs to `e2e/.e2e-seed.json` (read via `env()` in `e2e/fixtures/seed.ts`).

## Drive a surface

Write a short spec in `e2e/` using `import { test, expect } from './fixtures/auth'`
and the `authedPage` fixture (logs in as `e2e-user@example.com`, who is the seeded
ProjectOwner). Navigate by route and screenshot. Key routes:

- Concept Maps overview: `/projects/:projectId/graphrag-configs`
- Agent detail (tabbed; deep-link a tab): `/agents/:agentId?tab=knowledge`
- Agent groups: `/projects/:projectId/agent-groups`, `/agent-groups/:groupId`
- Workspace settings: `/workspaces/:workspaceId/settings`
- Chatroom settings: `/chatrooms/:chatroomId/settings`

Gotchas:
- **Never `waitForLoadState('networkidle')`** — the app holds live websockets, so
  it never idles and your test eats its whole timeout. Wait on a specific element
  (`getByText(...).waitFor()`) instead.
- The login fixture (`waitForURL` after submit) is **flaky on a cold Vite compile**.
  Run one spec at a time and re-run on failure; a warm dev server is far more reliable.
- Read PNG screenshots back with the Read tool to observe the render.

## Unblocking the local dev stack (one-time)

The dev stack (`deploy/compose`, backend on `:28000`) may be only partially
provisioned. Two blockers seen:

1. **Login 500s: `transit/keys/smap-jwt-sign` missing in Vault.** The backend can't
   mint JWTs. Fix (idempotent; Vault dev token is `root`):
   `docker exec smap_backend_web python -m smap.bootstrap vault-init`
   (it errors at a later policy-file step that needs `/deploy` mounted, but the
   transit keys are created before that — login works after).
2. **Seed users absent (login 401).** Registration fails closed (Vault captcha
   config). Provision directly: hash with the app's argon2
   (`docker exec smap_backend_web python -c "from shared_kernel.auth.password import PasswordHasher; print(PasswordHasher().hash('E2eP@ssw0rd!Str0ng'))"`),
   then INSERT into `users` (`email_verified=true, status='active'`) via
   `docker exec -i smap_postgres psql -U smap -d smap`. Admin = row in `admins(user_id)`.
   Credentials live in `e2e/fixtures/auth.ts`.

`qdrant`/`neo4j`/`minio` are often down in this stack — fine for create/read/toggle
surfaces, but the GraphRAG **build** path and file uploads need them.

## API-level checks

For deterministic contract checks (e.g. a read-path field), skip the GUI: login via
`POST /api/auth/login`, then drive the REST endpoints with the bearer token. Faster
and immune to the login-fixture flakiness.
