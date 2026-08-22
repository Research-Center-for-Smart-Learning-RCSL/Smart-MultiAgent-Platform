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
- **`page.reload()` near a boot refresh logs the session out**, for the same reason. The
  SPA re-authenticates on every full load; reloading before the first refresh has
  committed its rotated cookie sends the old one, the backend reads that as a replay and
  revokes the family, and every later navigation lands on `/login` with
  `getByLabel(/email/i)` never appearing. A sweep that reloaded once per surface to change
  locale died on its seventh navigation this way. **To change a persisted setting, use
  `page.addInitScript` and navigate — never set-then-reload.** One test per locale, one
  `addInitScript` per test: the calls accumulate on the context, so registering a second
  one makes every later navigation run both and the last registered wins.
- **A cold Vite server charges its whole transform cost to your first test.** Playwright's
  `webServer` waits for the port to answer, which happens long before the app's modules
  are transformed, so the first `login()` can exceed its 30s wait and fail as if the app
  were broken. Before an expensive or unrepeatable run (a baseline capture, a long sweep),
  start `pnpm dev` yourself and drive one throwaway spec against it — `reuseExistingServer`
  is on outside CI, so the real run then starts warm. Kill it by port afterwards:
  `Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`.
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

Three blockers seen:

1. **Bootstrap fails: `database "smap_test" does not exist`.** The overlay points at
   `smap_test`, but a `postgres` volume created before the overlay was first used has
   already run its init scripts and won't re-run them. One-time fix:
   `docker exec smap_postgres psql -U smap -d postgres -c "CREATE DATABASE smap_test OWNER smap"`
2. **`global-setup.ts` is not idempotent, and it fails SILENTLY.** A second run against an
   already-seeded stack creates a fresh org, then 403s on `POST /api/projects` ("no
   applicable role in scope"), writes a three-key `.e2e-seed.json`, and **every
   fixture-gated spec then skips** — a green run with zero coverage. Check the seed file
   has the full `E2E_*` set before trusting a pass, or pass the ids you need in yourself.
   Note it runs on **every** `playwright test` invocation, so each run seeds another
   org/project/agent set on top of the last — the stack gets deeper, never cleaner.
3. **`/readyz` 503 listing all six dependencies as `timeout` at once is host load, not a
   broken stack.** Postgres, Redis, Qdrant, Neo4j, MinIO and Vault do not fail together;
   seeing them do so means the machine is starved (a long sweep plus Docker plus a build
   will do it). Wait and re-check before debugging any of them. The same starvation shows
   up in Playwright as an `apiRequestContext.post` timeout on `/api/auth/login` from
   `global-setup`.

Tear down with `down`, **not `down -v`**: the volumes may predate your session.

**To reset `smap_test` to pristine without touching any volume** — which `down` alone does
not do, since it keeps the data — stop the backends first so nothing holds a connection,
then drop and recreate just that database and re-bootstrap:

```powershell
docker compose -f ... -f ... stop backend-web backend-worker
docker exec smap_postgres psql -U smap -d postgres `
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='smap_test';" `
  -c "DROP DATABASE IF EXISTS smap_test;" -c "CREATE DATABASE smap_test OWNER smap;"
# then bootstrap + `up -d --wait backend-web backend-worker` as above
```

The dev database `smap` is untouched by this. A pristine result is 2 seed users and 0
`api_keys`, which is the state CI starts from — worth asserting before anything that
depends on it.

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

## Regenerating the visual parity baseline

`e2e/baselines/visual-token-parity.json` describes a rendered stack, so how it is
captured is part of what it means. Every precondition below has failed in practice at
least once.

1. **Reset `smap_test` to pristine** (above). A saturated stack renders widgets CI never
   does — a developer machine with 70 `api_keys` draws the pagination control that CI's
   single key does not, which is 28 signatures of pure environment difference.
2. **Warm the dev server first** (Gotchas), or the first surface times out on login and
   the run is wasted.
3. **Let `global-setup` run exactly once**, then confirm `.e2e-seed.json` has the full
   11-key set before trusting anything. A second Playwright invocation seeds a second
   org/project set, so a capture taken on the retry describes a data state CI never has.
4. **Capture with `UPDATE_VISUAL_BASELINE=1 pnpm exec playwright test 00-visual-token-parity
   --project=desktop`** — that spec alone. It is numbered `00` because the baseline must
   describe freshly seeded data, and the rest of the suite posts messages and creates
   invites that move it.
5. **Self-check immediately**: re-run the same spec in compare mode against unmodified
   code. It must report no differences.

The spec refuses to write a partial baseline if any surface failed, so a bad run leaves
the committed file alone — retry rather than repair.

**What the self-check cannot catch.** It re-runs on the same machine moments later, so
anything timing-dependent reproduces itself and compares clean. The spec freezes
transitions and animations before capturing for exactly this reason: it switches theme on
a live page, and once a captured property was transitioned (`box-shadow`, when the button
variants gained a resting elevation) the capture began recording a value part-way between
the two themes — 72 of 76 dark slots held the *light* shadow, and the self-check passed
anyway. **If you add a property to `PROPS`, check whether anything transitions it.**

## Sweeping for layout defects

Screenshots answer "does this read right"; they do not answer "is anything clipped", and
168 of them cannot be eyeballed reliably. Measure that half instead — in the page, over
`document.querySelectorAll('*')`:

- **`scrollWidth > clientWidth + 1`** on an element that clips (`text-overflow: ellipsis`,
  `overflow-x: hidden|clip`, or `white-space: nowrap`) is the browser's own statement that
  text was cut. Skip elements with element children (they report their widest descendant,
  which is that descendant's finding) and skip `sr-only`/`visually-hidden` (clipped to 1px
  on purpose, paints nothing).
- **`documentElement.scrollWidth - clientWidth`** catches a page that scrolls sideways.
  Ignore `position: fixed` elements when hunting the culprit — they do not contribute.
- **A box under ~40px wide that is clipping is showing nothing at all**, which is a
  different defect from ellipsis and worth separating: `min-width: 0` and `overflow:
  hidden` both make a flex item's automatic minimum size 0, so a squeezed label does not
  ellipsise, it vanishes.

This found six real defects at 375px that three visual reviews had missed. It is blind to
anything that **wraps** rather than clips, though — a CJK button label breaking one glyph
per line measures as fine — so run it *alongside* looking, not instead of it.
`e2e/25-narrow-viewport-layout.spec.ts` is the permanent, narrower version.

## API-level checks

For deterministic contract checks (e.g. a read-path field), skip the GUI: login via
`POST /api/auth/login`, then drive the REST endpoints with the bearer token. Faster
and immune to the login-fixture flakiness.

## Running the suites

- **Vitest 4 has no `basic` reporter.** `--reporter=basic` fails at startup with "Failed to
  load custom Reporter", which reads as every run failing rather than as a bad flag —
  eight consecutive "failures" that were nothing of the kind. Use the default, or `verbose`
  for per-test durations.
- **A local full-suite failure is not evidence on its own.** Three consecutive `pnpm test`
  runs during one close-out failed 1, 0 and 2 tests and never the same ones (a viewport
  sweep at 10.5s, a CodeMirror re-sync). Re-run the file in isolation before believing it,
  and let CI arbitrate — it is authoritative over this host.
