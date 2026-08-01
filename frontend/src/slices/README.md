# Slice architecture (`REQUIREMENTS.md` §24.1 / §24.2)

Twelve slices. Every slice may import `@shared`; cross-slice imports beyond that must follow the direction below.

## Slices

| Slice | Purpose |
|---|---|
| `identity` | Login, register, verify-email, password reset, sessions, account deletion |
| `tenancy` | Orgs, projects, members, invites, org/project transfer |
| `keys` | API key upload, key groups, project-carried keys, search-provider keys, capability table |
| `prompt-studio` | Prompt-assistant configs (personal/org-scoped) and reusable prompt templates |
| `skills` | Skill authoring/bundles, project/org-scoped skill views, per-agent skill-binding panel |
| `agents` | Agent CRUD, RAG config, GraphRAG config/build status, Concept Map panel, MCP egress allowlist |
| `agent-groups` | Agent-group CRUD, membership, and the group-owned Concept Map panel |
| `activities` | Chatroom-hosted "activity" plugins (host/panel components + a plugin SDK), per-project activity-type CRUD |
| `conversation` | Workspaces, chatrooms, messages/attachments, WebSocket presence, search, observations |
| `workflow` | Visual workflow editor (Vue Flow), runs, backstage, approvals, DLQ viewer, orchestration |
| `admin` | Admin console: users/admins/IP-bans/orgs/projects/audit/ops/rate-limits/metrics, impersonation; also mounts the platform-wide Prompt Studio and Skills admin views |
| `notifications` | Notification bell and list |

## Dependency direction

Declared in `frontend/eslint.config.js`'s `SLICE_DEPS` map — the executable source of truth, kept in sync with `REQUIREMENTS.md` R24.06:

| Slice | May import from (besides `shared`) |
|---|---|
| `identity` | — (leaf) |
| `tenancy` | `identity` |
| `keys` | `tenancy`, `identity` |
| `prompt-studio` | `keys` |
| `skills` | `keys` |
| `agents` | `skills`, `prompt-studio`, `keys`, `tenancy`, `identity` |
| `agent-groups` | `agents`, `keys`, `tenancy`, `identity` |
| `activities` | — (leaf; hosted inside `conversation` via a plugin bridge, must never import `conversation` back) |
| `conversation` | `activities`, `agent-groups`, `agents`, `keys`, `tenancy`, `identity` |
| `workflow` | `conversation`, `agent-groups`, `agents`, `keys`, `tenancy`, `identity` |
| `admin` | `prompt-studio`, `skills` |
| `notifications` | `identity` |

`shared` itself imports nothing from any slice, with one deliberate, allowlisted exception: `shared/stores/session.ts` re-exports `useSessionStore` from `@slices/identity` so other code can read session state without a direct slice import (`boundaries/ignore` in `eslint.config.js`).

### Known violations (not currently caught by lint)

Two real cross-slice edges exist in the code today that fall outside the table above:

- `identity/views/DeleteAccountView.vue` imports `orgsApi` from `@slices/tenancy`, even though `identity` is declared as a dependency-free leaf.
- `conversation` imports from `@slices/workflow` in four places (`ChatroomView.vue`, `ChatroomSettingsView.vue`, `useChatroomBindings.ts`, `useChatroomSocket.ts`), which isn't declared. `workflow` also imports from `conversation` (which *is* declared), so the two slices are mutually coupled in practice rather than one-way.

`boundaries/element-types` is configured at `error` severity and covers all 12 slices, but does not currently flag either violation — verified by running `eslint` directly against the two files above (both return zero errors despite the rule being active and correctly resolved for those files). Treat the table as the intended architecture, not a guarantee of current state, until this is root-caused.

## Canonical shape

Each slice directory is intended to contain:

| Folder | Purpose |
|---|---|
| `api/`       | Thin wrappers around generated `@shared/api-client`; slice-owned endpoints only. |
| `types/`     | Slice domain types derived from OpenAPI + UI-only refinements. |
| `stores/`    | Pinia stores — **client state only**. Server state lives in `queries/`. |
| `queries/`   | TanStack Query `useQuery`/`useMutation` hooks. |
| `composables/` | UI-adjacent reactive helpers. |
| `components/`  | Presentational + smart components scoped to the slice. |
| `utils/`       | Pure utility functions (non-composable, non-component). Optional. |
| `views/`       | Route-level page components. |
| `routes.ts`    | `RouteRecordRaw[]`; meta flags (`requiresAuth`, `requiresVerifiedEmail`, `requiredRoles`). |
| `locales/`     | `en.json` + `zh-TW.json` — slice-local messages. |
| `__tests__/`   | Vitest unit + component tests (integration via MSW). |
| `index.ts`     | Public surface of the slice — **only exports importable from other slices**. |

Every slice has `index.ts`, `routes.ts`, `locales/`, `__tests__/`, `api/`, `queries/`, and `views/`. `stores/` is genuinely absent from several slices that have no client-only state (`agent-groups`, `agents`, `keys`, `notifications`, `prompt-studio`, `skills`, `tenancy`) — not an omission, just nothing to store client-side. Other deviations from the table above:

| Slice | Notes |
|---|---|
| `activities` | Adds `plugins/` and `sdk/` — the activity-plugin SDK surface (`defineActivityPlugin`, host bridges). |
| `agent-groups` | Also missing `components/`, `composables/` — currently views-only plus queries/types. |
| `agents`, `keys`, `skills`, `notifications` | Add a `lib/` folder for pure helpers, alongside or instead of `utils/`. |
| `conversation` | Adds `constants/`. |
| `identity` | Missing `composables/`; has a top-level `validation.ts`. |
| `notifications` | Missing `types/` (re-exported from `api/`). |
| `tenancy` | Missing `components/`; has a `styles/` folder (`detail-cards.css`, `member-form.css`) `@import`-ed into two views' `<style scoped>` blocks — scoped, not a global-CSS leak, but not in the table above either. |
| `workflow` | Has a top-level `constants.ts`. |

## Rules

1. **One-way imports.** Declared via `eslint-plugin-boundaries` (`boundaries/element-types`, `error`, all 12 slices) per the dependency table above. Currently has two known gaps — see "Known violations" above.
2. **Public-surface only.** Cross-slice imports go through `@slices/<name>` (the slice `index.ts`), never deep paths — enforced by `no-restricted-imports` (Gate #2). No deep-path cross-slice import exists anywhere in the codebase today.
3. **Store ↔ API boundary.** Stores never import `api/` directly — they subscribe to `queries/` or accept values from views. This is a contributor convention; it is not machine-enforced.
4. **v-html allowlist.** `vue/no-v-html` is `error` everywhere except the files explicitly listed in the Gate #4 override in `eslint.config.js`, all in `slices/conversation`: `ChatroomMessageBubble.vue`, `ChatroomStreamingBubble.vue`, `ChatroomSearchPanel.vue`, and `ObservationCard.vue` (plus a vestigial `ChatroomView.vue` entry that no longer contains a `v-html` directive but hasn't been pruned from the allowlist). Every active site pipes markup through `renderMarkdown()`/`sanitizeSnippet()` → DOMPurify before binding — do not add a file to this list without the same guarantee and a security review.
5. **No global CSS.** Component `<style scoped>` only. Design tokens and component classes live in `@shared/styles/main.css` (Tailwind v4 + `@theme`). Slice-local shared CSS fragments (like `tenancy/styles/`) may exist but must still be `@import`-ed inside a `<style scoped>` block, never registered globally.
