---
name: check-quality
description: Professional-grade code quality audit — 12 dimensions covering structural integrity, SOLID principles, runtime safety, and maintainability. Use when finishing a feature or before committing.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree is clean) for code quality issues across 12 dimensions. For each finding, trace the dependency chain to verify the violation. Produce a single structured report.

## Scope Detection

1. Run `git diff --name-only HEAD` to find uncommitted changes.
2. If empty, run `git diff --name-only HEAD~1 HEAD` to use the last commit.
3. Filter to `.py`, `.ts`, `.vue` files only.
4. Read each changed file in full.
5. For each changed file, also read its direct imports to verify dependency direction.

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

- Flag classes with more than 8 public methods — likely doing too much.
- Flag functions that perform more than one conceptual operation (e.g., validate AND persist AND notify).
- Flag facades with 20+ methods — consider splitting by subdomain.
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

### 10. Resource Management

- Flag `await session.execute()` outside an `async with session:` context manager.
- Flag opened file handles, HTTP clients, or DB connections without cleanup (`async with`, `try/finally`).
- Flag missing `await` on coroutines (fire-and-forget async calls that silently drop errors).
- Flag event listeners or subscriptions registered without corresponding cleanup in `onUnmounted`.

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
- Check if a similar helper already exists in `shared_kernel/` or `shared/composables/`.

**Complexity:**
- Flag functions exceeding 50 lines or nesting depth > 4 levels.
- Flag files exceeding 400 lines — suggest splitting.
- Flag functions with > 5 parameters — suggest options object or builder.
- Flag boolean parameters that change behavior (flag arguments) — suggest separate methods.

**Type Safety:**
- Backend: flag `Any` annotations, bare `dict` returns, `# type: ignore` without explanation.
- Frontend: flag `as any` casts, missing prop types, untyped `ref()`/`reactive()`.

**Dead Code:**
- Flag unused imports, unreferenced functions, commented-out blocks (>3 lines), write-only variables.

**API Consistency:**
- Flag inconsistent pagination patterns (some endpoints use `limit/offset`, others `page/size`).
- Flag inconsistent response envelope (some return `{data: [...]}`, others return bare arrays).
- Flag inconsistent naming (camelCase vs snake_case in API responses).

---

## Output Format

```markdown
## Code Quality Report

**Scope:** N files checked (list files)

### Critical (must fix before commit)
- [Upward Dep] file:line — `infrastructure/foo.py` imports from `app/api/v1/bar.py` (lower layer depends on upper)
- [Circular] file:line — cycle: A → B → C → A

### Warning (should fix)
- [SRP] file:line — `FooService` has 12 public methods, consider splitting
- [Abstraction Leak] file:line — ORM model `UserTable` returned from facade

### Info (consider improving)
- [Complexity] file:line — function `process_data` is 67 lines, nesting depth 5
- [DRY] file:line — duplicated validation pattern, see `shared_kernel/validators.py`

### Summary
| Dimension | Critical | Warning | Info |
|-----------|----------|---------|------|
| Structural (1-4) | 0 | 0 | 0 |
| SOLID (5-8) | 0 | 0 | 0 |
| Runtime (9-11) | 0 | 0 | 0 |
| Maintainability (12) | 0 | 0 | 0 |
| **Total** | **0** | **0** | **0** |
```

**Severity rules:**
- **Critical**: upward dependency, circular dependency, abstraction leak across API boundary, silently swallowed security-relevant errors.
- **Warning**: SRP/OCP/DIP violations, missing error handling, side effects, resource leaks, DRY violations > 10 lines.
- **Info**: complexity, dead code, type safety gaps, API inconsistency, minor DRY.
