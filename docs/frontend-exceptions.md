# Frontend CI Gate Exceptions

Exceptions to the 12 SoC gates (§24.15) filed here with rationale.
Gates themselves cannot be silently disabled — every bypass must be listed.

## Gate #4: v-html allowlist

| File | Rationale |
|------|-----------|
| `slices/conversation/views/ChatroomView.vue` | Renders sanitized markdown via `renderMarkdown.ts` (DOMPurify). Single approved site per R24.41. |

## Gate #12: i18n bare-string allowlist

| Token | Rationale |
|-------|-----------|
| Provider names (OpenAI, Claude, etc.) | Brand names are not translatable. |
| Punctuation / symbols | Layout characters have no locale variant. |
| `shared/ui/` atoms | Design-system atoms use fixed labels; consumer views wrap with `$t()`. |

## Gate #N (visual regression) and R24.12 / R24.16: Storybook deferred in v1

**Status:** Deferred — not installed in v1.
**Date logged:** 2026-04-25 (audit `docs/project-audit-2026-04-25.md` §2).

### Scope of the gap

- `frontend/package.json` carries no `@storybook/*` dependency.
- `frontend/.storybook/` does not exist.
- No `*.stories.ts` files exist under `frontend/src/`.
- The "Storybook play functions" path in R24.12 and the "Playwright screenshots on Storybook stories" path in R24.15 Gate #N are therefore not executable.

### Rationale

1. **Design-system surface is two components.** `frontend/src/shared/ui/` contains only `FormField.vue` and `PermissionGate.vue`. R24.15 itself scopes visual regression to "design-system only in v1"; with a two-atom design system, the cost of a full Storybook + Playwright-screenshot toolchain (Storybook 8 for Vue 3 + Vite, ~15 dev dependencies, dedicated CI job, baseline-image management) exceeds the value delivered.
2. **Behavioural coverage is already in place.** The 50 MSW-mocked integration tests under `frontend/tests/` exercise both atoms in real view contexts, and the 8 Playwright E2E specs cover end-to-end rendering. The uncovered slice is *isolated* component rendering — which, for two atoms, is duplicative of the integration coverage.
3. **v1 constraints (memory: `project_smap`).** Single-host self-hosted deployment for ≤100 concurrent users; the operator is not consuming a marketing-grade design system. Tooling that exists primarily to support a growing component library is premature here.
4. **R24.12 80 % presentational coverage is satisfied via Vitest + `@vue/test-utils` alone** for the current atom set. The clause `Vitest + @vue/test-utils + Storybook play functions` lists the toolchain inclusively; with two atoms, Vitest reaches the 80 % threshold without play functions.

### Reinstatement trigger

Re-evaluate when **either** condition is met:

- `frontend/src/shared/ui/` grows beyond ~6 atoms, **or**
- a dedicated design-system slice (separate from feature slices) is introduced.

At that point install Storybook 8 (Vue 3 + Vite framework), add `.storybook/main.ts` + `preview.ts`, co-locate `Foo.stories.ts` per R24.16, and wire a Playwright visual-regression job against the static Storybook build per R24.15 Gate #N.

### Coverage substitute in v1

| R24.12 / R24.15 obligation | v1 substitute |
|---|---|
| Storybook play functions | Vitest + `@vue/test-utils` unit specs in `frontend/src/shared/__tests__/` |
| Playwright screenshots on Storybook stories | Playwright E2E specs in `frontend/e2e/` (full-view rendering) |
| `Foo.stories.ts` co-location (R24.16) | N/A — no stories in v1 |
