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
- **AUTH rate limit (10 req/min/IP).** The SPA re-authenticates via
  `/api/auth/refresh` on every full page load, so a multi-test suite trips the AUTH
  bucket and bounces tests to `/login` (the historic "login flakiness"). `global-setup`
  raises the `auth`/`auth-recovery` policies to 1000/min via
  `PATCH /api/admin/rate-limits/{key}` (admin) so this doesn't happen. If you drive the
  app outside the harness and hit 429s on login/refresh, either wait out the 60s
  sliding window or bump those policies the same way. The auth fixture also backs off
  on 429. Storage-state reuse does NOT work: refresh rotates the token and revokes the
  family on reuse — log in per test (fresh session each), not once-and-share.
- **`chat-send` is NOT one of the buckets `global-setup` raises.** Seeding messages
  through `POST /api/chatrooms/{id}/messages` 429s around the 30th. Raise it the same
  way if a spec needs a room with real history.
- **`locator.click()` scrolls its target into view first**, which resets the scroll
  container's `scrollTop`. Any measurement of feed scroll position taken before a click
  is invalid. Use `dispatchEvent('click')` and assert `scrollTop` still holds its value.
- **`toBeVisible()` does not mean "finished animating".** `SDrawer` slides in over
  `--transition-slow` (300ms) and is visible from the first frame, while it is still
  `translateX(-100%)` — a panel measured then reports `x = -272` at a 320px viewport, so
  every geometry assertion after it is off by a full panel width. Poll for the transform
  to settle before measuring:
  `await expect.poll(async () => (await panel.boundingBox())?.x).toBe(0)`.
  Same applies to `SModal`'s scale/fade.
- **Tab panels are `v-show`, not `v-if`** (agent detail, and others). Every tab's content is
  in the DOM, so `.last()` on a shared selector will happily pick an element from a hidden
  panel — whose `boundingBox()` is `null`. Scope with `:visible`.
- Account routes are under `/account/*` (`/account/profile`, `/account/sessions`), not
  `/profile`. A wrong route renders the 404 view, which still has a `<main>`, so the failure
  surfaces as a missing child rather than as a navigation error.
- Read PNG screenshots back with the Read tool to observe the render.

## Bringing up the full test stack

For anything the partially-provisioned dev stack can't serve (below), bring up the
E2E overlay instead — it provisions Vault/MinIO/Qdrant/Neo4j properly, so none of
the dev-stack blockers below apply. From the repo root, mirroring `ci.yml`:

```
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/compose.test.yml \
  up -d --wait postgres redis vault qdrant neo4j minio mailhog
docker compose -f ... -f ... run --rm --no-deps --volume "$PWD/deploy:/deploy:ro" \
  -e SMAP_APP_ENV=test -e SMAP_DB_DSN=postgresql+asyncpg://smap:smap@postgres:5432/smap_test \
  -e VAULT_ADDR=http://vault:8200 -e VAULT_TOKEN=root -e SMAP_VAULT_DEV_TOKEN=root \
  backend-web python -m smap.bootstrap all
docker compose -f ... -f ... up -d --wait backend-web backend-worker
```

Two blockers seen:

1. **Bootstrap fails: `database "smap_test" does not exist`.** The overlay points at
   `smap_test`, but a `postgres` volume created before the overlay was first used has
   already run its init scripts and won't re-run them. One-time fix:
   `docker exec smap_postgres psql -U smap -d postgres -c "CREATE DATABASE smap_test OWNER smap"`
2. **`global-setup.ts` is not idempotent, and it fails SILENTLY.** A second run against an
   already-seeded stack creates a fresh org, then 403s on `POST /api/projects` ("no
   applicable role in scope"), writes a three-key `.e2e-seed.json`, and **every
   fixture-gated spec then skips** — a green run with zero coverage. Check the seed file
   has the full `E2E_*` set before trusting a pass, or pass the ids you need in yourself.

Tear down with `down`, **not `down -v`**: the volumes may predate your session.

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
3. **Key upload 500s: KV config paths missing.** `vault-init` throws on the
   `/deploy/vault/policies/*.hcl` read (blocker #1 above) *before* it reaches
   its KV-seed loop, so `secret/smap/config/{hmac-key,captcha,smtp,minio}`
   never get written. Login/JWT works (transit keys are created earlier), but
   `POST /api/keys` 500s with `InvalidPath: .../smap/config/hmac-key` — needed
   for any flow that uploads a provider key (agent creation needs a key
   carried into its key group first). Fix (idempotent):
   ```
   docker exec smap_vault sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=root vault kv put secret/smap/config/hmac-key key=$(docker exec smap_vault sh -c 'head -c32 /dev/urandom | base64')"
   docker exec smap_vault sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=root vault kv put secret/smap/config/captcha provider=hcaptcha public_key= secret_key="
   docker exec smap_vault sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=root vault kv put secret/smap/config/smtp host= port=587 user= password="
   docker exec smap_vault sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=root vault kv put secret/smap/config/minio access_key= secret_key="
   ```
   A fake key (`sk-test-00000000000000000000000000000000`) uploads and carries
   fine after this — the backend doesn't validate provider keys against the
   real API in dev.

`qdrant`/`neo4j`/`minio` are often down in this stack — fine for create/read/toggle
surfaces, but the GraphRAG **build** path and file uploads need them.

## API-level checks

For deterministic contract checks (e.g. a read-path field), skip the GUI: login via
`POST /api/auth/login`, then drive the REST endpoints with the bearer token. Faster
and immune to the login-fixture flakiness.
