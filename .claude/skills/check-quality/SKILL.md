---
name: check-quality
description: Audit changed files for code quality issues — SoC violations, duplication, complexity, type safety, error handling, and dead code. Use when finishing a feature or before committing.
---

## Task

Audit the **changed files** in the current working tree (or the last commit if the tree is clean) for code quality issues across six dimensions. Produce a single structured report.

## Scope Detection

1. Run `git diff --name-only HEAD` to find uncommitted changes.
2. If empty, run `git diff --name-only HEAD~1 HEAD` to use the last commit.
3. Filter to `.py`, `.ts`, `.vue` files only.
4. Read each changed file in full.

## Dimensions to Check

### 1. Separation of Concerns (SoC)

**Backend:**
- API route files (`app/api/v1/`) must only call facades (`contexts/*/interfaces/facade.py`) — flag any direct import of services, repositories, or SQLAlchemy models.
- `application/` services must not import from `infrastructure/` — flag direct table or repository class imports.
- `shared_kernel/` must not import from any `contexts/` module.
- No business logic in route handlers — flag inline queries, loops over DB results, or conditional branching beyond input validation.

**Frontend:**
- Slice files must not import from other slices except via `index.ts` re-exports.
- `shared/` must not import from `slices/` or `app/`.
- No raw `fetch`, `WebSocket`, or `EventSource` — must use the generated api-client.
- No raw `window.confirm` or `alert` — must use `SConfirmDialog` / `useConfirmDialog`.

### 2. DRY (Don't Repeat Yourself)

- Flag identical or near-identical code blocks (>5 lines) across changed files.
- Flag repeated patterns that could be extracted into a shared utility, composable, or base class.
- Check if a similar helper already exists in `shared_kernel/` (backend) or `shared/composables/` (frontend).

### 3. Complexity

- Flag functions exceeding 50 lines.
- Flag nesting depth > 4 levels.
- Flag files exceeding 400 lines — suggest splitting.
- Flag functions with > 5 parameters — suggest a config/options object.

### 4. Type Safety

**Backend:**
- Flag `Any` type annotations or bare `dict` returns.
- Flag missing return type annotations on public functions.
- Flag `# type: ignore` without an accompanying explanation.

**Frontend:**
- Flag `as any` casts.
- Flag missing prop type definitions in Vue components.
- Flag untyped `ref()` or `reactive()` calls that should have explicit generics.

### 5. Error Handling

**Backend:**
- Flag bare `except Exception` or `except:` without re-raising or logging.
- Flag API routes missing error response documentation.
- Flag services that call external systems (DB, Redis, Vault, MinIO) without try/except.

**Frontend:**
- Flag `useQuery`/`useMutation` calls without `onError` handling or an error UI state.
- Flag missing loading and error states in views (should show `SLoadingSpinner` or error message).
- Flag unhandled promise rejections (`.then()` without `.catch()`).

### 6. Dead Code

- Flag unused imports (Python: `F401`, TypeScript: `no-unused-vars`).
- Flag functions/methods that are defined but never called within the changed files or their known callers.
- Flag commented-out code blocks (>3 lines).
- Flag variables assigned but never read.

## Output Format

```markdown
## Code Quality Report

### Critical (must fix before commit)
- [SoC] file:line — description

### Warning (should fix)
- [DRY] file:line — description

### Info (consider improving)
- [Complexity] file:line — description

### Summary
- Files checked: N
- Issues: N critical, N warning, N info
```

Classify as **Critical** if it violates an enforced SoC gate or introduces a security-relevant type hole. **Warning** for duplication, missing error handling, or high complexity. **Info** for dead code or minor type issues.
