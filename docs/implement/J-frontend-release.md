# Phase J — Frontend Integration, E2E & Release

**Goal.** Land the production UI exactly as `REQUIREMENTS.md` §24 defines it: strict 5-layer stack (Views → Composables → Stores/Queries → API/WS → Types) with L0 atoms and shared transport, **seven slices** (`identity / tenancy / keys / agents / conversation / workflow / admin`) each with the canonical internal shape, cross-slice dependency direction enforced, 12 CI SoC gates, Playwright E2E against `compose.test.yml`, bundle budgets **250 KB / 200 KB**, type coverage **≥ 95 %**, release checklist executed on staging.

**Size.** XL
**Depends on.** A (skeleton), C (auth shell), D–I (feature backends + audit + notifications).
**Unblocks.** production launch.
**Refs.** `REQUIREMENTS.md` §24 (all 16 subsections); §22; §25; `docs/operations.md` §8–§9; `deploy/vault/README.md` verification checklist.

## J.0 Scope summary

By close:

- Routes per §24.6 render with correct `requiresAuth / requiresVerifiedEmail / requiredRoles` guards.
- Every slice (7) is complete and each carries the canonical sub-folder set (`api, types, stores, queries, composables, components, views, routes.ts, locales, __tests__, index.ts`).
- Dependency direction enforced: `conversation → agents → keys → tenancy → identity → shared`; reverse fails CI.
- 12 CI gates green (§24.15).
- Playwright golden paths green against `compose.test.yml` (R24.40).
- Bundle: initial ≤ 250 KB gzip (R24.48); per-view lazy ≤ 200 KB gzip.
- Type coverage ≥ 95 % (R24.48).
- Release checklist + Vault verification checklist executed on staging host.

## J.1 Slice layout freeze & boundary lint — **CODE** — M

**Deliverables.**

- `frontend/src/` top-level (§24.2):
  ```
  app/  {main.ts, router.ts, plugins/, layouts/}
  slices/  {identity, tenancy, keys, agents, conversation, workflow, admin}
  shared/  {types, transport, api-client, ui, styles, i18n, errors, composables, directives}
  ```
  (Folder name is `slices/`, **not** `features/`.)
- Every slice has exactly: `api/, types/, stores/, queries/, composables/, components/, views/, routes.ts, locales/, __tests__/, index.ts`.
- `eslint-plugin-boundaries` configured with:
  - Layer rules: views → composables → (stores, queries) → api → types + L0 atoms; reverse forbidden.
  - Cross-slice dependency direction (R24.06): `conversation → agents → keys → tenancy → identity → shared`. Reverse or cross-dependency outside this chain fails CI.
  - `no-restricted-imports` auto-generated per slice so cross-slice imports resolve only through the target slice's `index.ts` (R24.05).
  - `shared/` may not import from any slice (R24.07).

**Key IDs.** §24.1 / §24.2 / §24.5.

**Exit criteria.** Violation PR fails CI with a human-readable message.

## J.2 OpenAPI type generation — **CODE** — M

**Deliverables.**

- Backend exposes `/openapi.json` via FastAPI's built-in generator, curated with tags per bounded context.
- Generator choice per §24.16: **`openapi-typescript-codegen`**. `make openapi-types` emits into `frontend/src/shared/api-client/`.
- `api-client/` is the low-level surface — one function per endpoint, fully typed.
- Slice `api/` folders wrap these into use-case-shaped calls (`fetchAgentList(projectId)`) that add domain semantics (R24.13).
- CI drift check: regeneration must match committed output; otherwise CI fails.

**Key IDs.** §24.13, §24.16.

**Exit criteria.** Drift gate green; each slice `api/` file ≤ 300 LOC by virtue of delegating to `api-client`.

## J.3 Transport layer — **CODE** — M

**Deliverables.** `shared/transport/` contains:

- `axios.ts` — configured axios instance and interceptors, exactly in order (R24.12):
  1. Inject `Authorization: Bearer <access_token>` from identity store.
  2. Inject `Idempotency-Key` on POST when caller opts in (`idempotency.ts` helper).
  3. Inject `Accept-Language` from i18n locale.
  4. On 401 with `type=https://smap.local/problems/auth/token-expired`: pause request, silently refresh, replay once; on refresh failure, flush queue as `AuthError` + navigate to `/login`.
  5. On 429: parse `Retry-After`, `X-RateLimit-*` headers; surface as `RateLimitError` subclass.
  6. Any non-2xx with `application/problem+json`: parse into a typed `ApiError` subclass (R24.35).
- `ws-manager.ts` — `WsManager` singleton exposing `channel(path) -> Channel` (R24.14). `Channel` provides `subscribe(eventName, handler)`, `send(payload)`, and handles reconnect / backoff / auth refresh via in-socket `{"type":"refresh","access_token":"..."}`. WS auth uses `Sec-WebSocket-Protocol: bearer.<access_token>` (§22.14).
- `problem-json.ts` — RFC 7807 parser → typed error subclasses.
- `idempotency.ts` — UUID v4 helper for POST creates.
- Components never import `axios`, `fetch`, `WebSocket`, `EventSource`, or `new URL()` for API paths. Lint gate #3 enforces.

**Key IDs.** §24.4 / §24.11–§24.14, §22.14.

**Exit criteria.** Single-flight refresh test; WS reconnect + in-socket refresh; 429 Retry-After honoured.

## J.4 State management — **CODE** — S

**Deliverables.**

- Server state: TanStack Query (`@tanstack/vue-query`). Query-key convention `[slice, resource, …params]`; a `shared/api-client/queryKeys.ts` exports typed factories.
- Client state: Pinia — session tokens (access in memory, refresh in `sessionStorage` cleared on logout — R24.11), UI preferences, draft forms, impersonation flag.
- Pinia stores **never** cache server data (R24.10). Lint gate #7 bans `store` importing another slice's `api/`.
- Mutations invalidate via `queryClient.invalidateQueries`; WS events also call it or apply optimistic `setQueryData` patches (R24.09, R24.21).

**Key IDs.** §24.3, §24.7, §24.11.

**Exit criteria.** Gate #7 green; token survival test across refresh.

## J.5 Routing & guards — **CODE** — M

**Deliverables.**

- `app/router.ts` composes `routes.ts` from each slice + a single not-found catch-all (R24.17).
- `meta: { requiresAuth, requiresVerifiedEmail, requiredRoles }` (R24.18).
- Pure-function guards tested in isolation (R24.19): `authGuard`, `verifiedEmailGuard`, `roleGuard`, `banKickGuard` (listens for `ban-kick` on `/ws/user/{id}`).
- `<PermissionGate :cap="..." :scope="...">` short-circuits UI to hidden/disabled; every render-time check is paired with a server-side check (R5.05 / R24.20).

**Key IDs.** §24.6.

**Exit criteria.** Guard matrix test; lazy chunks visible in build output.

## J.6 Forms & validation — **CODE** — M

**Deliverables.**

- **vee-validate + Zod** everywhere (R24.24). Schemas live in `slices/<n>/types/` and are reused for client pre-flight.
- Backend RFC 7807 `detail.field_errors: [{path, message}]` piped to vee-validate `setErrors()` (R24.25).
- `<FormField>` wrapper handles label, error, help text, ARIA — components never hand-roll this (R24.26).

**Key IDs.** §24.8.

**Exit criteria.** Server field errors render inline without ad-hoc plumbing.

## J.7 Components, design tokens, responsive, a11y — **CODE** — L

**Deliverables.**

- **Naming** (R24.16): `BaseFoo.vue` (atom in `shared/ui/`), `Foo.vue` (slice-local presentational), `FooView.vue` / `FooContainer.vue` (container). Storybook stories next to presentational components.
- **Element Plus** is the base library (R24.27), imported on-demand via `unplugin-auto-import` to keep bundle small.
- Design tokens in `shared/styles/tokens.css` CSS custom properties, theme via `data-theme` attribute on `<html>` (R24.28).
- **No Tailwind** in v1 (R24.29). Scoped `<style>` blocks for layouts; global CSS only in `shared/styles/{tokens,reset}.css` + Element Plus overrides. Lint gate #6 bans non-scoped `<style>` outside `shared/styles/` (R24.30).
- Breakpoints 480 / 768 / 1024 / 1280 px exposed as CSS vars + `useBreakpoint()` composable (R24.31).
- Chat collapses to single pane < 768 px; drawers for side panels (R24.32).
- Workflow canvas < 1024 px is read-only with a "switch to desktop" notice (R24.33).
- Touch targets ≥ 44 × 44 px (R24.34).
- A11y: `eslint-plugin-vuejs-accessibility`; no bare `role="button"` on non-buttons; axe-core smoke run per top-level view (R24.20 / R24.34).

**Key IDs.** §24.5, §24.9, §24.10, §24.13 (partial), R20.08.

**Exit criteria.** Lighthouse a11y + Axe critical issues = 0 on core views; bundle sizes respect budgets.

## J.8 Real-time integration — **CODE** — M

**Deliverables.**

- Per-slice composables wrap WS channels (e.g. `useChatroomSocket(id)`, `useRagConfigSocket(id)`, `useWorkflowRunSocket(id)`). Components never subscribe directly (R24.22).
- On reconnect, composables fetch a delta (`GET /api/chatrooms/{id}/messages?since=<id>`, etc.) before resuming event application (R24.23).
- Ban-kick on `/ws/user/{id}` → `banKickGuard` redirects.

**Key IDs.** §24.7.

**Exit criteria.** Dual-browser chat with forced disconnect retains consistency.

## J.9 Markdown render, guest UX, security — **CODE** — S

**Deliverables.**

- Single render pipeline at `slices/conversation/lib/renderMarkdown.ts`: `markdown-it` → **DOMPurify** → KaTeX / Mermaid / highlight.js via DOM-mutation APIs (R24.41 / R24.42).
- No other file uses `v-html`; ESLint gate #4 allowlists only this path.
- Guest-link landing: after consumption, `history.replaceState(null, '', '/c/<chatroom_id>')` strips the token from URL + `Referer` surface (R24.43).
- CSRF placeholder `useCsrfToken()` composable reserved for future cookie-auth (R24.44).

**Key IDs.** §24.13.

**Exit criteria.** Cheat-sheet XSS payloads render clean; guest-link URL cleared from router history.

## J.10 i18n — **CODE** — S

**Deliverables.**

- `vue-i18n` 9; per-slice `locales/en.json` (R24.2 slice internal shape).
- English-only in v1; other locales scaffolded (lazy-loaded via dynamic import) — R24.46.
- Every template string wrapped in `$t(...)`; a custom ESLint rule (gate #12) rejects bare literals in `.vue` templates outside `shared/ui/` atoms.
- `<html lang="…">` synced to active locale.

**Key IDs.** §24.2 (i18n), §24.16.

**Exit criteria.** Pseudo-locale dev toggle surfaces any untranslated literal.

## J.11 Error classes & telemetry — **CODE** — S

**Deliverables.**

- `shared/errors/` exports (R24.35):
  - `ApiError` (base with `type, title, status, detail, instance`).
  - `AuthError`, `PermissionError`, `ValidationError`, `RateLimitError`, `NetworkError` subclasses.
- Global Vue `errorCaptured` handler routes (R24.36):
  - `AuthError` → navigate to `/login`.
  - `PermissionError` → toast + stay.
  - `ValidationError` → vee-validate handled.
  - Network/5xx/unknown → banner with exponential-backoff retry.
- In production, `console.error` redirected to `/api/csp-report` for CSP + to a future `/api/frontend-errors` (out of v1) (R24.37).
- `useToast()` is the single approved user-visible transient channel (R24.38).

**Key IDs.** §24.11.

**Exit criteria.** Each error class has a Storybook story.

## J.12 Build & bundling — **CODE** — M

**Deliverables.**

- **Vite 5** with rollup (R24.45). Each slice-entry view lazy-loaded via dynamic `import()`.
- `@vue-flow/core`, `mermaid`, `katex` each isolated into their own chunks.
- Locale bundles lazy-loaded; only `en.json` ships at startup (R24.46).
- Source maps emitted in production but served only to Admins via a private `/admin/sourcemaps/` path (or retained internally — operator chooses) (R24.47).
- Budgets (R24.48 — exact): **initial ≤ 250 KB gzip; per-view lazy chunk ≤ 200 KB gzip**. Violations fail CI via `bundlesize`.

**Key IDs.** §24.14.

**Exit criteria.** CI rejects a PR that pushes initial chunk past 250 KB or a view past 200 KB.

## J.13 The 12 CI SoC gates — **CODE** — L

**Deliverables.** Each gate is a CI job per §24.15 (exact set):

| # | Gate | Tool |
|---|---|---|
| 1 | Layer direction (views→composables→stores→api→transport, no reverse) | `eslint-plugin-boundaries` |
| 2 | Slice isolation — cross-slice imports only via `index.ts` | `no-restricted-imports` (auto-generated per slice) |
| 3 | Transport isolation — only `shared/transport/*` and `slices/*/api/*` may import axios / `WebSocket` / `EventSource` / `fetch` | Custom ESLint rule |
| 4 | `v-html` allowed only in `renderMarkdown.ts` | `vue/no-v-html` + allowlist |
| 5 | No `alert` / `confirm` / `prompt` | `no-alert` |
| 6 | No global CSS outside `shared/styles/` | Custom checker script |
| 7 | Store must not import from another slice's `api/` | `eslint-plugin-boundaries` |
| 8 | Every view has ≥ 1 integration test | Vitest file-glob check |
| 9 | Bundle budget | `bundlesize` (against values in J.12) |
| 10 | Type coverage ≥ 95 % (no `any`) | `type-coverage` |
| 11 | Accessibility — no `role="button"` on non-buttons, labels on inputs | `eslint-plugin-vuejs-accessibility` |
| 12 | i18n — no bare string literals in `.vue` templates outside `shared/ui/` atoms | Custom ESLint rule |

- Exceptions filed in `docs/frontend-exceptions.md` with rationale; gates themselves cannot be silently disabled.

**Key IDs.** §24.15.

**Exit criteria.** All 12 jobs green on `main`.

## J.14 Testing strategy — **CODE** — L

**Deliverables.**

- **Unit (Vitest)** — composables, utils, Zod schemas; target ≥ 90 % lines (R24.12 table).
- **Components (Vitest + @vue/test-utils + Storybook play functions)** — presentational tier; target ≥ 80 %.
- **Integration (Vitest + MSW)** — views + mocked API; covers every container (R24.39).
- **E2E (Playwright)** — against `compose.test.yml` that stands up the full stack with a seeded fixture project (R24.40):
  - Register → verify-email → login → create Org → invite → accept → transfer OC.
  - Upload LLM key → validate → carry into project → build group.
  - Create Agent → attach RAG → ingest doc via tus → grounded answer.
  - GraphRAG build → live SSE/WS progress via `/ws/rag-configs/{id}`.
  - Two-browser chatroom live; edit window enforced; moderator edit audited.
  - tus 600 MB upload with mid-transfer blip.
  - Workflow editor → `/validate` inline errors → run → live steps on `/ws/workflow-runs/{id}`.
  - Admin impersonate → target notified → audit visible; last-Admin demote blocked.
- **Visual regression** — Playwright screenshots on Storybook stories for design-system atoms only in v1.

**Key IDs.** §24.12.

**Exit criteria.** All paths green; retry budget ≤ 1 per path.

## J.15 Deployment bring-up — **OPS** — M

**Deliverables.**

- Single authoritative `deploy/compose/docker-compose.yml` per §25 with all services: `nginx`, `backend-web` ×N, `backend-worker` ×M, `backend-ws` (can share with backend-web), `frontend`, `postgres` (with `pgvector` + `pg_cron` extensions), `redis`, `qdrant`, `neo4j`, `minio`, `vault`, `egress-proxy`, `mcp-sandbox-supervisor`.
- Networks (§25):
  - `smap_frontend_net`: nginx ↔ backend-web.
  - `smap_backend_net`: backend ↔ postgres/redis/qdrant/neo4j/minio/vault.
  - `smap_egress_net`: sandbox containers ↔ egress-proxy only.
- Separate `compose.test.yml` (tied to J.14 E2E) brings up the same service set with seeded fixtures and Vault dev mode.
- Nginx config: HSTS preload, CSP, WebSocket upgrade, no server banner.
- `deploy/README.md` walks through bring-up (Vault unseal → bootstrap CLI → first admin → smoke test).

**Key IDs.** §25.

**Exit criteria.** Staging bring-up in < 60 min from the README alone.

## J.16 Release checklist — **OPS** — S

**Deliverables.** `docs/release-checklist.md` composed from:

- Vault 7-point verification (from `deploy/vault/README.md`).
- Backend: `readyz` green on staging; bootstrap idempotent; first admin created; CI 12 gates green on the tagged release.
- Frontend: Lighthouse scores, bundle budget respect, type-coverage threshold.
- Data: every retention worker's last-run metric ≤ expected window.
- Docs: `docs/implement/00-overview.md` §0.8 all phases ticked.
- Release tag: `vYYYY.MM.DD` or `v1.0.0` at launch.

**Key IDs.** §25, `deploy/vault/README.md`.

**Exit criteria.** Checklist executed once on staging; notes committed.

## J.∞ Phase gate

- [ ] 12 CI SoC gates green.
- [ ] All Playwright golden paths green.
- [ ] Initial bundle ≤ 250 KB gzip; per-view ≤ 200 KB gzip.
- [ ] Type coverage ≥ 95 %.
- [ ] Staging bring-up completed via `deploy/README.md` in < 60 min.
- [ ] Release checklist executed.
- [ ] `00-overview.md` §0.8: J = done; full table green.

## Cross-cutting checklist

1. **AuthZ tap.** Frontend re-checks server-side via `<PermissionGate>`; never claims sole authority.
2. **Audit tap.** Backend remains the source; frontend surfaces audits via `/api/admin/audit`.
3. **Rate limit bucket.** Frontend respects `Retry-After` and surfaces friendly 429 UX.
4. **Observability.** Optional browser telemetry hooks reserved (R24.37).
5. **RFC 7807.** All six error classes mapped 1-to-1 to problem types.
6. **Migration policy.** No DB changes in J except bug fixes.
7. **Secrets.** No secret in frontend env; build-time config public only.

## Risks

- **OpenAPI drift.** `make openapi-types` drift gate catches.
- **Bundle creep.** `bundlesize` budgets per slice.
- **A11y regressions.** Axe per-route smoke + Lighthouse.
- **Release surprises.** Staging dry-run mandated by J.16.
