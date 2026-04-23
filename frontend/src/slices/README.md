# Slice architecture (`REQUIREMENTS.md` §24.1 / §24.2)

Seven slices, dependency direction flows **only one way**:

```
conversation → agents → keys → tenancy → identity → shared
                                                      ▲
                                                      │
                                                    admin
```

`admin` is a leaf slice (consumes `shared` only; orthogonal to the operational flow).

## Canonical shape

Each slice directory contains:

| Folder | Purpose |
|---|---|
| `api/`       | Thin wrappers around generated `@shared/api-client`; slice-owned endpoints only. |
| `types/`     | Slice domain types derived from OpenAPI + UI-only refinements. |
| `stores/`    | Pinia stores — **client state only**. Server state lives in `queries/`. |
| `queries/`   | TanStack Query `useQuery`/`useMutation` hooks. |
| `composables/` | UI-adjacent reactive helpers. |
| `components/`  | Presentational + smart components scoped to the slice. |
| `views/`       | Route-level page components. |
| `routes.ts`    | `RouteRecordRaw[]`; meta flags (`requiresAuth`, `requiresVerifiedEmail`, `requiredRoles`). |
| `locales/`     | `en.json` + `zh-TW.json` — slice-local messages. |
| `__tests__/`   | Vitest unit + component tests (integration via MSW). |
| `index.ts`     | Public surface of the slice — **only exports importable from other slices**. |

## Rules (enforced in Phase J)

1. **One-way imports.** Use `eslint-plugin-boundaries` with the direction above.
2. **Public-surface only.** Cross-slice imports go through `@slices/<name>` (the slice `index.ts`), never deep paths.
3. **Store ↔ API boundary.** Stores never import `api/` directly — they subscribe to `queries/` or accept values from views.
4. **Single v-html site.** Only `slices/conversation/lib/renderMarkdown.ts` is allowed to render sanitised HTML.
5. **No global CSS.** Component `<style scoped>` only. Shared tokens live in `@shared/styles/tokens.css`.
