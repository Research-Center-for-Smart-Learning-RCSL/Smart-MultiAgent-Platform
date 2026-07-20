# Code Quality Dimensions (1-12)

The checklist behind the `check-quality` skill. Read in full when running an audit. Other
skills that only need the dimension names (e.g. `/spec`'s quality lens) can read the
headings alone.

Ground Rule 4 governs every numeric threshold here: they are calibration points that
prompt a judgement, not tripwires that produce a finding on their own.

---

## Part A — Structural Integrity

### 1. Upward Dependency

The most critical structural violation. Lower layers must NEVER import from upper layers.

**Backend layer order (top → bottom):**
```
app/api/v1/          (presentation)
  ↓ calls only
contexts/*/interfaces/  (facade)
  ↓ calls only
contexts/*/application/  (services)
  ↓ calls only
contexts/*/domain/       (models, errors — pure Python)
contexts/*/infrastructure/ (repos, tables — implements application interfaces)
shared_kernel/           (cross-cutting — imported by any layer, imports from none)
```

Flag violations:
- `domain/` importing from `application/`, `infrastructure/`, or `app/`
- `application/` importing from `infrastructure/` concrete classes (should depend on abstract interface)
- `infrastructure/` importing from `app/api/`
- `shared_kernel/` importing from any `contexts/` module
- Route handlers importing anything below facade level (services, repos, tables, domain models)

**Frontend layer order:**
```
app/           (router, layouts, providers)
  ↓ imports from
slices/*/      (feature modules — self-contained)
  ↓ imports from
shared/        (UI components, composables, api-client, styles)
```

Flag violations:
- `shared/` importing from `slices/` or `app/`
- `slices/X/` importing from `slices/Y/` internal modules (only `index.ts` re-exports allowed)
- Component importing directly from another slice's store, api, or composable

### 2. Circular Dependency

- Trace import chains in changed files. Flag any cycle: A → B → C → A.
- Check cross-context imports: `contexts/keys/` → `contexts/agents/` → `contexts/keys/` is a cycle even if each step goes through facades.
- For frontend: check if slice A's store imports from slice B's store which imports back from A.

### 3. Abstraction Leak

Implementation details must not cross layer boundaries:
- Flag SQLAlchemy `Table` or `Column` objects appearing in `application/` or above.
- Flag ORM model instances returned from facades — should be converted to domain models or DTOs.
- Flag Pydantic request/response models imported in `application/` or `domain/` layers.
- Flag Redis/Qdrant/Neo4j client objects passed as function parameters outside `infrastructure/`.
- Frontend: flag raw axios responses leaked to components — should be unwrapped in the api-client layer.

### 4. Separation of Concerns (SoC)

**Backend:**
- Route handlers must only: validate input, call facade, return response. Flag business logic (conditionals, loops, calculations) in route handlers.
- Services must not perform HTTP I/O directly — delegate to infrastructure adapters.
- Flag mixed responsibilities: a single function doing both DB write and external API call.

**Frontend:**
- No raw `fetch`, `WebSocket`, or `EventSource` in components — must use api-client or composables.
- No `window.confirm`, `alert`, `prompt` — must use `SConfirmDialog` / `useConfirmDialog`.
- No direct DOM manipulation in `<script setup>` — use refs and Vue reactivity.
- Store actions must not contain UI logic (toasts, navigation, dialog state).

---

## Part B — SOLID Principles

### 5. Single Responsibility

Calibration: ~8 public methods on a class, ~20 methods on a facade. The question to
answer is "how many reasons does this unit have to change?" — report when the answer
is more than one.

- Flag functions that perform more than one conceptual operation (e.g., validate AND persist AND notify).
- Flag facades grown past coherence — consider splitting by subdomain.
- Flag Vue components with both data-fetching logic AND complex rendering logic — extract into composable + presentational component.

### 6. Open/Closed

- Flag `if/elif/elif` or `match/case` chains that would need modification when adding a new type.
- Specifically check workflow node executors, agent tool dispatchers, and notification handlers — these should use registry/strategy patterns, not switch statements.
- Flag enum additions that require code changes in multiple files.

### 7. Dependency Inversion

- In `application/` services, flag direct instantiation of `infrastructure/` classes.
- Services should receive repository interfaces via constructor/dependency injection, not import concrete implementations.
- Flag `from contexts.X.infrastructure.repositories import ConcreteRepo` inside `application/` — should use an abstract base or protocol.

### 8. Interface Segregation

- Flag facade methods that return large objects when callers only use 1-2 fields.
- Flag service interfaces that force implementers to define methods they don't need.
- Flag composables that return 10+ values — consider splitting.

---

## Part C — Runtime Quality

### 9. Side Effects & Mutability

- Flag functions that modify their input parameters (mutating a passed dict/list).
- Flag module-level mutable state (`_cache = {}`, `_registry = []`) without thread-safety.
- Flag `@lru_cache` on methods that take mutable arguments.
- Flag global/module-level variables modified at import time (side effects on import).
- Frontend: flag `reactive()` objects shared across components without explicit store — race condition risk.
- Frontend: flag in-place mutation of objects inside a `ref` array (`items.value[i].x = y`)
  where computeds/watchers depend on the result — a known pitfall in this codebase;
  updates must be immutable (replace the element or the array).

### 10. Resource Management & Persistence Consistency

- Flag `await session.execute()` outside an `async with session:` context manager.
- Flag opened file handles, HTTP clients, or DB connections without cleanup (`async with`, `try/finally`).
- Flag missing `await` on coroutines (fire-and-forget async calls that silently drop errors).
- Flag event listeners or subscriptions registered without corresponding cleanup in `onUnmounted`.
- Flag ORM column types in `tables.py` that don't match the migration-created PG types
  (e.g., a column declared `sa.Text` where the migration created a PG ENUM) — asyncpg
  fails at runtime on the mismatch; this has caused production 500s in this repo.

### 11. Error Handling Quality

**Backend:**
- Flag bare `except Exception` or `except:` that swallow errors without re-raising or logging.
- Flag `except` blocks that return a generic 500 instead of a domain-specific error.
- Flag missing error propagation — a service catches an error but doesn't translate it to a domain error.
- Flag inconsistent error response format (some endpoints return `{detail: ...}`, others `{error: ...}`).

**Frontend:**
- Flag `useQuery`/`useMutation` calls without error handling (no `onError`, no error state in template).
- Flag views missing all three states: loading, error, and empty.
- Flag `.catch(() => {})` — silently swallowed promise rejections.
- Flag `try/catch` blocks that catch but don't display feedback to the user.

---

## Part D — Maintainability

### 12. Code Hygiene

**DRY:**
- Flag identical or near-identical code blocks (>5 lines) within or across changed files.
- Flag repeated patterns extractable into a shared utility, composable, or base class.
- Check if a similar helper already exists in `shared_kernel/` or `shared/composables/` — reimplementing an existing helper is the highest-value DRY finding.

**Complexity** (calibration points — see Ground Rule 4):
- Functions around 50+ lines or nesting depth > 4; files past ~400 lines; functions with > 5 parameters.
- Flag boolean parameters that change behavior (flag arguments) — suggest separate methods.

**Type Safety** (what the toolchain can't catch):
- Flag `# type: ignore` / `as any` / eslint-disable without a justifying comment.
- Flag `Any` annotations and bare `dict` returns on public boundaries (facades, composables) — internal helpers matter less.

**Dead Code** (beyond what ruff/eslint report):
- Flag commented-out blocks (>3 lines), unreferenced exported functions, write-only variables.

**API Consistency:**
- Flag inconsistent pagination patterns (some endpoints use `limit/offset`, others `page/size`).
- Flag inconsistent response envelope (some return `{data: [...]}`, others return bare arrays).
- Flag inconsistent naming (camelCase vs snake_case in API responses).
