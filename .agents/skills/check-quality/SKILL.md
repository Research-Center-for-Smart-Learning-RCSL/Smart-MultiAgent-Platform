---
name: check-quality
description: Professional-grade code quality audit — 12 dimensions covering structural integrity, SOLID principles, runtime safety, and maintainability. Use when finishing a feature, before committing, as the quality gate in /build's Definition of Done, or whenever the user asks to review code quality, check for code smells, verify architecture/layer boundaries, or asks "is this code clean" about recent changes.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree
is clean) for code quality issues across 12 dimensions. Produce a single structured
report of verified findings.

This skill is report-only — it changes no files and makes no commits. When it runs as a
gate inside `/build`, that skill owns the commits per the CLAUDE.md commit discipline.

## Ground Rules

These four rules control the signal-to-noise ratio of the whole audit; they outrank any
individual dimension below.

1. **Verify before reporting.** Every finding cites `path:line` and survives an attempt
   to refute it: read the actual import, trace the actual chain, check whether a guard
   or abstraction you missed makes the code correct. A pattern that merely looks like a
   violation is not reportable — false positives train readers to ignore the report.
2. **Classify Introduced vs Pre-existing.** An issue caused by this change is
   *Introduced* and gates the commit. An issue that was already present in touched code
   is *Pre-existing*: report it in its own section so it can route to a follow-up
   (FU-n in the task dossier, if one exists) instead of blocking today's work. When
   this change makes a pre-existing problem worse, that worsening is Introduced.
3. **Don't duplicate the mechanical toolchain.** ruff, mypy, eslint, and vue-tsc already
   catch unused imports, formatting, and obvious type gaps deterministically — assume
   they run and skip hand-auditing what they cover. Do flag what silences them
   (`# type: ignore` without justification, `as any`, eslint-disable) and what they
   cannot see: architecture, semantics, resource lifecycles, duplicated intent.
4. **Numeric thresholds are calibration points, not tripwires.** A 55-line function
   with one linear responsibility can be fine; a 30-line function mixing validation,
   persistence, and notification is not. When a threshold triggers, judge whether the
   underlying design problem is actually present, and report the problem — not the
   number.

## Scope Detection

1. Collect changed files: `git status --porcelain` (captures staged, unstaged, AND
   untracked files — new files are the most common place quality issues hide). If the
   tree is clean, use `git diff --name-only HEAD~1 HEAD`.
2. Filter to `.py`, `.ts`, `.vue`. Exclude deleted files and generated code (the
   generated api-client under `frontend/src/shared/`, alembic version stubs' boilerplate
   — though migration *content* is in scope for dimension 10).
3. Read each changed file in full; for each, read the direct imports needed to verify
   dependency direction and reuse claims.
4. **Large scope** (more than ~10 changed files): fan out subagents — one per Part
   (A–D) across all files, or one per area for very wide changes — then merge, dedupe,
   and apply Ground Rule 1 to the merged set in the main context.

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

---

## Output Format

```markdown
## Code Quality Report

**Scope:** N files checked (list files). Not covered: <excluded/generated/skipped, if any>

### Introduced by this change

#### Critical (must fix before commit)
- [Upward Dep] file:line — `infrastructure/foo.py` imports from `app/api/v1/bar.py` (lower layer depends on upper). Fix: invert via interface in application layer.

#### Warning (fix, or defer explicitly)
- [SRP] file:line — `FooService` mixes key validation and usage metering. Fix: extract metering into its own service.

#### Info (consider improving)
- [Complexity] file:line — `process_data` nests 5 levels deep. Fix: early returns.

### Pre-existing in touched code
- [Abstraction Leak] file:line — ORM model returned from facade (predates this change). Route to follow-up.

### Summary
| Dimension | Critical | Warning | Info |
|-----------|----------|---------|------|
| Structural (1-4) | 0 | 0 | 0 |
| SOLID (5-8) | 0 | 0 | 0 |
| Runtime (9-11) | 0 | 0 | 0 |
| Maintainability (12) | 0 | 0 | 0 |
| **Total** | **0** | **0** | **0** |
```

Every finding carries a one-clause fix direction — a finding without a direction forces
the reader to redo the analysis.

**Clean result:** if no findings survive verification, say so explicitly and state what
was checked — "12 dimensions over N files, no verified findings" is a meaningful result;
an empty report is not.

**Severity rules:**
- **Critical**: upward dependency, circular dependency, abstraction leak across API boundary, silently swallowed security-relevant errors, ORM/migration type mismatch.
- **Warning**: SRP/OCP/DIP violations, missing error handling, side effects, resource leaks, DRY violations > 10 lines.
- **Info**: complexity, dead code, type safety gaps, API inconsistency, minor DRY.

Consumers: `/build`'s Definition of Done treats Introduced-Critical as blocking and
Introduced-Warning as fix-or-defer-as-FU-n; Pre-existing findings inform but never block.
