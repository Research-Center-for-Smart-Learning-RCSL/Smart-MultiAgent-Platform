# Phase A — Foundations & Project Bootstrap

**Goal.** Stand up the empty but runnable SMAP repo: monorepo layout, DDD-aligned backend package tree, Vue 3 frontend skeleton with `slices/`, base Docker Compose stack covering every §25 service, structured JSON logging with redaction, RFC 7807 error scaffold using the canonical `https://smap.local/problems/…` URL prefix, and baseline CI.

**Size.** M
**Depends on.** nothing
**Unblocks.** B (infra bootstrap), J (frontend skeleton).
**Refs.** `REQUIREMENTS.md` §0, §3, §4, §23, §25; `docs/operations.md` §1–§3.

## A.0 Scope summary

At phase close:

- `docker compose up` starts every service in §25: `nginx`, `backend-web`, `backend-worker`, `backend-ws` (can share with backend-web), `frontend`, `postgres` (with `pgvector` + `pg_cron`), `redis`, `qdrant`, `neo4j`, `minio`, `vault`, `egress-proxy`, `mcp-sandbox-supervisor`.
- Backend stub exposes `/healthz`, emits JSON logs, and returns RFC 7807 stubs with URL `https://smap.local/problems/not-implemented`.
- Frontend skeleton aligns with §24.2: `app/`, `slices/` (seven empty slices), `shared/`.
- CI runs format/lint/typecheck/unit smoke for both halves.

## A.1 Monorepo skeleton — **CODE** — S

**Deliverables.**

- Root `.gitignore`, `.editorconfig`, `.gitattributes` (force LF on `*.py/*.ts/*.sh/*.yml/*.md`).
- `Makefile` / `justfile` targets: `dev`, `fmt`, `lint`, `typecheck`, `test`, `docker-up`, `docker-down`, `bootstrap`, `openapi-types`.
- Top-level `README.md` pointing to `REQUIREMENTS.md` and `docs/implement/00-overview.md` only.
- pnpm workspace at root; `packageManager` pinned in root `package.json`.

**Exit criteria.** `make fmt lint test` succeeds on a clean checkout.

## A.2 Backend skeleton (DDD bounded-context layout) — **CODE** — M

**Objective.** Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 async + Alembic + Arq + loguru, pinned; folder shape matches §23.

**Deliverables.**

- `backend/pyproject.toml` pinned: `fastapi`, `pydantic==2.x`, `sqlalchemy==2.x`, `asyncpg`, `alembic`, `arq`, `loguru`, `argon2-cffi`, `authlib` (JOSE), `httpx`, `bleach`, `markdown-it-py` (server-side sanitisation helpers), `google-re2`, `hvac`, `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `respx`.
- Package tree (matches R23.xx):
  ```
  backend/
    app/
      api/            # FastAPI routers (thin)
      config/
      main.py
    contexts/
      identity/ { domain/, application/, infrastructure/, interfaces/ }
      tenancy/        …same layout…
      keys/
      agents/
      knowledge/      # RAG + GraphRAG
      conversation/   # workspaces, chat rooms, messages
      workflow/
      audit/
      notification/
    shared_kernel/
      auth/           # JWT, password, permissions
      db/             # base classes, unit-of-work
      events/
      errors/
      i18n/
    tests/ { unit/, integration/, e2e/ }
  ```
- `backend/app/main.py` mounts a FastAPI app with `GET /healthz` → `{"status":"ok"}`.
- `backend/app/workers/main.py` Arq worker with a no-op task.
- Ruff + Mypy strict on `contexts/*/domain` and `shared_kernel/`; relaxed elsewhere to start.
- Import-linter ruleset enforces R23.01–R23.03 (no cross-context SQL joins, routers call facades only).

**Key IDs.** §4, §23.

**Exit criteria.** `uvicorn app.main:app` runs; `/healthz` returns 200; import-linter green.

## A.3 Frontend skeleton (slices/, not features/) — **CONTRACT** — M

**Deliverables.**

- `frontend/package.json` pinned; vite + vue-tsc; TypeScript strict + `noUncheckedIndexedAccess`.
- Tree per §24.2:
  ```
  frontend/src/
    app/ { main.ts, router.ts, plugins/, layouts/ }
    slices/ { identity, tenancy, keys, agents, conversation, workflow, admin }
    shared/ { types, transport, api-client, ui, styles, i18n, errors, composables, directives }
  ```
- Each slice pre-created with the canonical sub-folder set (`api, types, stores, queries, composables, components, views, routes.ts, locales, __tests__, index.ts`), most files empty / stubs.
- `src/app/main.ts` mounts a shell `App.vue` with an empty layout.
- `.eslintrc.cjs` wires `eslint-plugin-boundaries` and `no-restricted-imports` stubs (lenient until J).
- `pnpm build` succeeds.

**Key IDs.** §24.1 / §24.2.

**Exit criteria.** `pnpm dev` serves blank app; `pnpm build` succeeds.

## A.4 Docker Compose base stack — **OPS** — M

**Deliverables.**

- `deploy/compose/docker-compose.yml` (single authoritative file per §25) with services: `nginx`, `backend-web`, `backend-worker`, `frontend`, `postgres:16` (image with `pgvector` + `pg_cron` extensions available), `redis:7`, `qdrant`, `neo4j:5`, `minio`, `vault` (dev mode for local DX; prod uses Shamir in B.1), `egress-proxy`, `mcp-sandbox-supervisor`.
- Networks:
  - `smap_frontend_net` (nginx ↔ backend-web).
  - `smap_backend_net` (backend ↔ postgres / redis / qdrant / neo4j / minio / vault).
  - `smap_egress_net` (sandbox containers ↔ egress-proxy only).
- Named volumes for every stateful service.
- Healthchecks on all infra services (per `docs/operations.md` §2).
- Log rotation via Docker driver `json-file` `max-size=50m max-file=5`.
- `deploy/compose/docker-compose.override.yml` for local dev (source mounts, reload).
- `.env.example` at repo root documenting every var.

**Key IDs.** §25, `docs/operations.md` §1.3.

**Exit criteria.** `docker compose up -d` → all services healthy; `curl http://localhost/healthz` OK via nginx.

## A.5 Configuration & settings — **CODE** — S

**Deliverables.**

- `app/config/` using `pydantic-settings`.
- Sections: `app`, `database`, `redis`, `qdrant`, `neo4j`, `minio`, `vault`, `security` (includes `TRUSTED_PROXIES` CIDR list + `SMAP_CSP_REPORT_ONLY` toggle), `logging`, `limits`.
- Resolution order: Vault (for secrets) → env → `.env` → defaults.
- Fails fast on missing required vars with one aggregated error message.

**Key IDs.** §7.6 (secrets), §19a.

**Exit criteria.** Aggregated-error unit test passes.

## A.6 Structured logging & redaction — **CODE** — S

**Deliverables.**

- `shared_kernel/logging/` loguru JSON sink with required fields from `docs/operations.md` §1.2 (`ts, level, service, request_id, session_id, user_id, route, latency_ms, event, msg, error`).
- Redaction filter (R17.03 / O1.05): replace values of JSON keys matching `^(authorization|api[_-]?key|secret|password|token|bearer|private[_-]?key|cookie|session)$` (case-insensitive); replace known secret shapes (`sk-ant-…`, `sk-…` ≥ 40 chars, PEM header patterns).
- Request-ID middleware attaches `request_id` to logs via contextvar.
- `SMAP_LOG_LEVEL` controls default level (`info`); dynamic signal change not in v1 (O1.03).

**Key IDs.** `docs/operations.md` §1.

**Exit criteria.** A request with `Authorization` header logs the value as `***`; metrics-level logs carry `request_id`.

## A.7 RFC 7807 error scaffold — **CONTRACT** — S

**Deliverables.**

- `shared_kernel/errors/` with `Problem` pydantic model and `SmapError` base exception.
- All problem types use the **canonical URL prefix `https://smap.local/problems/…`** (R19.06 + §6 of `docs/operations.md`).
- Registry seeded with `not-implemented`, `internal`, `validation`, `rate-limited`, `auth/token-expired` (used by J.3 axios interceptor).
- FastAPI exception handlers for `SmapError`, `RequestValidationError`, `Exception` (last resort) return `application/problem+json`.
- All stub endpoints raise `NotImplementedProblem`.

**Key IDs.** R19.06, `docs/operations.md` §6.

**Exit criteria.** Hitting any stub returns a problem+json with `type=https://smap.local/problems/not-implemented`.

## A.8 Baseline CI — **OPS** — S

**Deliverables.**

- `.github/workflows/ci.yml` (or equivalent) with jobs:
  - `backend-lint` (ruff + mypy on strict paths).
  - `backend-test` (pytest smoke).
  - `frontend-lint` (eslint + vue-tsc).
  - `frontend-test` (vitest smoke).
- Matrix pinned to Python 3.12, Node 20 LTS; pnpm.
- Caches for pip, pnpm, mypy, ruff.
- No `continue-on-error`.

**Exit criteria.** Green CI after A.1–A.7 merge.

## A.9 Pre-commit hooks — **OPS** — S

**Deliverables.**

- `.pre-commit-config.yaml` running `ruff`, `ruff format`, `mypy` (fast), `eslint`, `prettier`, `vue-tsc --noEmit` (incremental).
- README documents installation.

**Exit criteria.** `pre-commit run --all-files` passes.

## A.∞ Phase gate

- [ ] `docker compose up -d` healthy on fresh checkout with all §25 services present.
- [ ] `curl http://localhost/healthz` returns 200 via nginx → backend.
- [ ] Frontend dir shape matches §24.2 (uses `slices/`).
- [ ] Backend dir shape matches §23 (per-context `{domain,application,infrastructure,interfaces}`).
- [ ] Redaction filter passes the Authorization-header test.
- [ ] Problem URL prefix is `https://smap.local/problems/…` everywhere.
- [ ] CI green for backend + frontend lint/type/test.
- [ ] `00-overview.md` §0.8: A = done.

## Cross-cutting checklist

1. **AuthZ tap.** N/A — no real endpoints yet.
2. **Audit tap.** Emit hook interface stub.
3. **Rate limit bucket.** Defined in Phase C.
4. **Observability.** Log fields established.
5. **RFC 7807.** Scaffold + correct URL prefix.
6. **Migration policy.** Alembic baseline in Phase B.
7. **Secrets.** Vault dev mode only.

## Risks

- **Windows line-ending pollution.** `.gitattributes` forces LF; PRs reintroducing CRLF rejected.
- **pnpm / image-digest drift.** Root `packageManager` pinned; compose image digests pinned in B.
- **DDD layout slipping back to flat package.** Import-linter rule blocks regressions.
