# SMAP — Smart Multi-Agent Platform

Self-hosted platform for composing, orchestrating, and chatting with multi-LLM agent groups. BYO-key model — users bring their own provider API keys; SMAP never handles billing.

## Architecture

```
backend/          Python 3.12 — FastAPI + Arq workers + Alembic migrations
  app/            API routes (app/api/v1/), middleware, config, bootstrap
  contexts/       DDD bounded contexts (identity, tenancy, keys, agents, conversation, workflow, admin)
  shared_kernel/  Cross-cutting: auth, db, errors, events, crypto
  services/       Standalone microservices (egress_proxy, mcp_supervisor)
  smap/           CLI tools (bootstrap, rotation)
frontend/         Vue 3.5 + Tailwind v4 + TypeScript 5.6
  src/app/        Router, layouts, App.vue
  src/slices/     Feature slices (admin, agents, conversation, identity, keys, notifications, tenancy, workflow)
  src/shared/     UI components, composables, styles, api-client (generated)
deploy/           Docker Compose overlays, nginx, Vault, observability
```

## Separation of Concerns (SoC)

This is a hard rule. Every change must respect layer boundaries:

**Backend layers** (top → bottom, no upward imports):
- `app/api/v1/` → calls `contexts/*/interfaces/facade.py`
- `contexts/*/application/` → orchestrates domain logic via services
- `contexts/*/infrastructure/` → repositories, tables, external adapters
- `shared_kernel/` → imported by any context, never imports from contexts

**Frontend layers** (enforced by eslint-plugin-boundaries):
- `app/` → imports from slices and shared
- `slices/` → imports from own slice and shared only; cross-slice via index.ts re-exports
- `shared/` → imports from shared only

## Commit Format

```
type(scope): subject
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `style`, `chore`, `perf`
Scopes: `backend`, `frontend`, `deploy`, `nginx`, `ci`, `obs`, `e2e`, `review`, `deps`, `build`

## Codex Pull Request Workflow

- Every Codex-authored development change must be isolated in a dedicated pull request; never push directly to `main`.
- Before requesting integration, Codex must inspect the complete diff and leave a self-review comment that records the scope, verification evidence, and merge decision.
- Codex must respect required independent approvals and branch protection; a self-review never substitutes for an approval that repository policy requires from another writer.
- Prefer the pull request's clean remote CI environment for full test execution. Run local checks only when they are fast and representative, and document any local-environment limitation in the PR.
- Unless necessary, keep all project memory in English and do not use emojis. Before recording new project memory, Codex must confirm the intended entry with the user.
- Every pull request merge commit subject must include its pull request number in the form `(#123)`.

## Security Constraints

- Never hardcode secrets — all credentials via environment variables or Vault KV
- Never log API keys, tokens, or passwords — even at DEBUG level
- All user input must be validated at the API boundary (Pydantic models)
- Multi-tenant AuthZ: every API endpoint must verify org/project membership before returning data
- Provider API keys are envelope-encrypted via Vault Transit — never stored in plaintext
- Markdown rendering uses DOMPurify — never bypass XSS sanitization
- No `eval()`, no `exec()`, no dynamic SQL string concatenation

## Key Commands

| Task | Command | Where |
|------|---------|-------|
| Backend tests | `pytest -q` | `backend/` |
| Backend lint | `ruff check . && ruff format --check .` | `backend/` |
| Backend typecheck | `mypy .` | `backend/` |
| Frontend tests | `pnpm test` | `frontend/` |
| Frontend lint | `pnpm lint` | `frontend/` |
| Frontend typecheck | `pnpm typecheck` | `frontend/` |
| Frontend build | `pnpm build` | `frontend/` |
| DB migration | `alembic revision --autogenerate -m "description"` | `backend/` |
| Apply migrations | `alembic upgrade head` | `backend/` |
| Regenerate API types | `pnpm run gen:api` | `frontend/` |
| Full dev stack | `docker compose up -d` | `deploy/compose/` |

## Style Rules

- No emojis anywhere in code, UI, comments, or commit messages
- Comments only when the WHY is non-obvious; never explain WHAT
- All user-facing strings go through `$t()` (vue-i18n) — no hardcoded text in templates
- Icons: use @heroicons/vue — no emoji substitutes
