# Frontend — Vue 3.5 / TypeScript 5.6 / Tailwind v4

## Stack

- **Framework**: Vue 3.5 (Composition API, `<script setup>`)
- **Router**: Vue Router 4.4
- **State**: Pinia 2.2 (client state) + TanStack Vue Query 5.59 (server state)
- **Styling**: Tailwind CSS 4.3 via @tailwindcss/vite, CSS custom properties in `shared/styles/main.css`
- **Forms**: vee-validate 4.14 + Zod 3.23
- **Icons**: @heroicons/vue 2.2 (24/outline, 24/solid, 20/solid)
- **API Client**: Auto-generated from backend OpenAPI spec (`pnpm run gen:api`)
- **Build**: Vite 6.4, pnpm (required — not npm/yarn)
- **i18n**: vue-i18n 11.2 — all user-facing strings via `$t()`

## Slice Architecture

```
src/
  app/          Router, App.vue, layouts, global providers
  shared/       UI components, composables, styles, api-client, types
  slices/       Feature modules — each is self-contained:
    admin/        Admin dashboard, user/org/project management, impersonation
    agents/       Agent CRUD, RAG config, GraphRAG, MCP bindings
    conversation/ Chatrooms, messages, composer, WebSocket presence
    identity/     Login, register, verify email, password reset, sessions
    keys/         API key upload, key groups, search keys, usage
    notifications/ Notification bell + list
    tenancy/      Orgs, projects, members, invites, OC transfer
    workflow/     Visual editor (Vue Flow), runs, backstage, orchestration
```

## SoC Boundaries (eslint-plugin-boundaries)

12 gates enforced in CI:
1. **Layer direction**: app → slices/shared; shared → shared only
2. **Slice isolation**: cross-slice imports only via `index.ts` re-exports
3. **Transport isolation**: no bare `fetch`/`WebSocket`/`EventSource` — use api-client
4. **v-html guard**: allowed only in `ChatroomView.vue` via `renderMarkdown.ts`
5. **No `alert`/`confirm`/`prompt`**: use `SConfirmDialog` component
6. **No global CSS** outside `shared/styles/`
7. **Store isolation**: no cross-slice API imports
8. **View test coverage**: every view has at least 1 test
9. **Bundle budget**: initial <= 250 KB gzip, per-view lazy <= 200 KB gzip
10. **Type coverage**: >= 95%
11. **Accessibility**: vuejs-accessibility plugin rules
12. **i18n**: no bare string literals in templates

## Shared UI Components

Located in `src/shared/ui/`: SCard, SPageHeader, SFormField, SLoadingSpinner, SStatusBadge, SEmptyState, SConfirmDialog, ThemeToggle.

## Commands

```bash
pnpm install              # install deps (pnpm only)
pnpm dev                  # dev server (Vite, port 5173)
pnpm build                # production build
pnpm test                 # unit tests (Vitest)
pnpm run test:e2e         # E2E tests (Playwright)
pnpm run test:coverage    # coverage report
pnpm lint                 # ESLint (all 12 gates)
pnpm run typecheck        # vue-tsc
pnpm run gen:api          # regenerate API client from openapi.json
pnpm run check:bundle-size    # verify bundle budget
pnpm run check:type-coverage  # verify >= 95% type coverage
pnpm run check:openapi-drift  # verify frontend types match backend
```

## Patterns

- **Data fetching**: TanStack Vue Query (`useQuery`, `useMutation`) — never raw axios in components
- **Form validation**: vee-validate + Zod schemas — never manual validation
- **Toasts**: `useToast()` composable wrapping vue-sonner — never raw toast calls
- **Confirmation**: `useConfirmDialog()` + `SConfirmDialog` — never `window.confirm`
- **Theme**: `useTheme()` — light/dark/system via `data-theme` attribute on `:root`
- **Responsive**: `useBreakpoint()` composable with CSS custom property breakpoints

## Content Rendering

ChatroomView renders markdown with: markdown-it + highlight.js + KaTeX + Mermaid + DOMPurify. These are lazy-loaded. Never bypass DOMPurify sanitization.
