# Phase B — Frontend Tools Reorg

**Goal.** Replace the single "MCP" view/tab (which mixes built-in toggles and MCP
servers) with a **Tools** surface grouped into **Hosted** and **Local**, consuming
the unified `/api/agents/{id}/tools` API from Phase A.

**Size.** L
**Depends on.** A (the `/tools` API + `AgentToolOut` contract).
**Unblocks.** C/D/E UI (they add cards/panels into this view), F (Local group).
**Touched code (all under `frontend/src/slices/agents/`):**

- `views/AgentMcpView.vue` → `views/AgentToolsView.vue`
- `views/AgentDetailView.vue` (the "MCP" tab → "Tools")
- `api/index.ts`, `types/schemas.ts`, `queries/index.ts`, `routes.ts`
- `composables/useMcpTest.ts` → `composables/useToolTest.ts`
- `locales/en.json`, `locales/zh-TW.json`
- `frontend/src/shared/api-client/*` (regenerated)

---

## B.1 Regenerate the API client — **CODE** — S

After A.7 freezes the OpenAPI contract:

- Run `pnpm run gen:api` (per root `CLAUDE.md`). New models appear:
  `AgentToolOut`, `AgentToolCreateIn`, `AgentToolPatchIn`, `ToolTestOut`; a new
  `AgentToolsService` (or extended `AgentsService`).
- The generated client is the source of truth for shapes; the hand-written
  wrappers in `api/index.ts` adapt them (B.2). Leave the now-dead `McpService` /
  `McpBinding*` generated models until A.9 removes the backend routes; record the
  intentional dead exports in `frontend-exceptions.md` (the repo already tracks
  dead-api-client decisions there).

**Exit criteria.** `pnpm typecheck` green against the regenerated client.

---

## B.2 API wrapper + types — **CODE** — M

**`api/index.ts`:** replace the `McpBinding` interfaces + the seven mcp/builtin
methods with a tool surface:

```ts
export type AgentToolType =
  | 'hosted_mcp' | 'hosted_web_search' | 'hosted_code_interpreter'
  | 'hosted_file_workspace' | 'hosted_file_search'
  | 'local_function' | 'local_shell'

export interface AgentTool {
  id: string
  agent_id: string
  tool_type: AgentToolType
  enabled: boolean
  display_name: string | null
  config: Record<string, unknown>   // auth REDACTED -> config.auth_present?: boolean
  created_at: string
}

export const agentsApi = {
  listTools(agentId): Promise<AgentTool[]>,                         // GET /tools
  addTool(agentId, body: AgentToolCreateInput): Promise<AgentTool>,// POST /tools
  patchTool(agentId, toolId, body: AgentToolPatchInput): Promise<AgentTool>,
  deleteTool(agentId, toolId): Promise<void>,                       // DELETE — matches the existing deleteMcpBinding verb
  testTool(agentId, toolId): Promise<ToolTestResult>,              // POST /tools/{id}/test
  // ...existing agent CRUD + RAG upload helpers stay
}
```

**`types/schemas.ts`:** replace `mcpBindingCreateSchema`. **Ordering:** Phase B
ships the `hosted_mcp` member only; Phase E adds the `local_function` member. Keep
the union assembled from named members so E appends without rewriting B:

```ts
export const mcpToolCreateSchema = z.object({
  tool_type: z.literal('hosted_mcp'),
  display_name: z.string().trim().max(200).optional(),
  config: z.object({
    source: z.enum(['url', 'package']),
    reference: z.string().trim().min(1).max(2000),
    allowed_tools: z.array(z.string().trim().min(1)).default([]),
  }),
  auth: z.record(z.unknown()).optional(),
})
// Phase E adds functionToolCreateSchema (tool_type: 'local_function', config: functionConfigSchema)
// and widens this union to z.discriminatedUnion('tool_type', [mcpToolCreateSchema, functionToolCreateSchema]).
export const agentToolCreateSchema = z.discriminatedUnion('tool_type', [mcpToolCreateSchema])
```

(`z.discriminatedUnion` exists in the project's Zod 3.23 — verified.)

**Exit criteria.** Unit tests for the schemas (valid/invalid url + package);
`pnpm typecheck` green.

---

## B.3 `AgentToolsView` — Hosted / Local groups — **CODE** — L

Rename `AgentMcpView.vue` → `AgentToolsView.vue` and restructure into two
sections. Reuse the existing card + `SToggle` + table/modal patterns.

**Layout:**

```
Tools
├─ Hosted
│   ├─ [card] MCP servers          (list + "Add server" modal; was the MCP table)
│   ├─ [card] Web search           (toggle)                  hosted_web_search
│   ├─ [card] Code Interpreter     (toggle) [+ uploads → D]  hosted_code_interpreter
│   ├─ [card] File workspace       (toggle)                  hosted_file_workspace
│   └─ [card] File Search          (toggle) [+ docs → C]     hosted_file_search
└─ Local
    ├─ [card] Functions            (list + "Add function" modal → E)  local_function
    └─ [card] Local Shell          ("coming soon" → F)               local_shell
```

**Behavior:**

- Single query `agentKeys.tools(agentId)` returns `AgentTool[]`. Derive each
  singleton card's toggle state by finding the row of that `tool_type`.
- Toggling a singleton → `patchTool(agentId, row.id, { enabled })` with optimistic
  update + server rollback on error (mirror the existing keys-slice pattern).
- MCP card lists `tool_type === 'hosted_mcp'` rows; "Add server" opens the modal
  (source url/package, reference, allowed_tools, optional auth) → `addTool`.
  Per-row Test button → `testTool` (uses `useToolTest`, B.4). Edit/delete as today.
- Functions card lists `tool_type === 'local_function'` rows (UI built in E; in B
  the card renders empty-state + disabled "Add" until E lands, or B can ship the
  card shell only).
- Local Shell card is visually present but its toggle is disabled with a "Coming
  soon" badge (F finalizes the click affordance).

**`AgentDetailView.vue`:** rename the `MCP` tab label/key to `Tools`; its
read-only preview table (currently the MCP bindings) becomes a compact summary of
enabled tools by group, linking to `agents.tools` (was `agents.mcp`).

**Exit criteria.** Toggling each singleton persists and survives reload; MCP
add/edit/delete/test still work end-to-end against the new API.

---

## B.4 Routing, queries, composable rename — **CODE** — S

- **`routes.ts`:** add route `agents.tools` at `/agents/:agentId/tools`
  (component `AgentToolsView`). Replace the old `agents.mcp` entry with a redirect
  using a function so the param carries over:
  ```ts
  { path: '/agents/:agentId/mcp', redirect: (to) => ({ name: 'agents.tools', params: to.params }) }
  ```
- **`queries/index.ts`:** replace `agentKeys.mcpBindings` / `agentKeys.builtinTools`
  with `agentKeys.tools(agentId)`; invalidate it after every mutation.
- **`composables/useMcpTest.ts` → `useToolTest.ts`:** same shape, calls
  `agentsApi.testTool`.

**Exit criteria.** `eslint` (boundaries) green; navigating `/agents/:id/mcp`
redirects to `/agents/:id/tools`.

---

## B.5 i18n migration `agents.mcp.* → agents.tools.*` — **CODE / E2E** — M

- Add a new `agents.tools` namespace in `locales/en.json` + `locales/zh-TW.json`
  covering: group titles (`hosted`, `local`), per-tool labels + descriptions
  (`webSearch`, `codeInterpreter`, `fileWorkspace`, `fileSearch`, `mcp`,
  `functions`, `localShell`), the MCP modal strings (migrate from `agents.mcp.*`),
  the "coming soon" badge, and toggle success/failure toasts.
- Keep the old `agents.builtinTools.*` + `agents.mcp.*` keys until A.9 cleanup,
  then delete. **Watch the literal-`@` trap** (memory `reference_i18n_literal_at`):
  any `@` in a label must be escaped `{'@'}` or it crashes in prod.
- No hardcoded strings in templates — every label via `$t()`.

**Exit criteria.** Both locales have full parity (a key-diff test passes);
Playwright golden path: open Tools → see Hosted/Local groups in zh-TW + en.

---

## B.∞ Phase gate

- [ ] API client regenerated; `api/index.ts` exposes the tool surface only.
- [ ] `AgentToolsView` renders Hosted + Local groups; singleton toggles persist.
- [ ] MCP add/edit/delete/test works against `/tools`.
- [ ] `agents.mcp` redirects to `agents.tools`; detail tab relabeled.
- [ ] i18n parity (en + zh-TW); no literal-`@` crash; no hardcoded strings.
- [ ] After E2E green, A.9 removes the deprecated backend endpoints + old i18n keys.
- [ ] `00-overview.md` §0.6: B = done.

## Appendix: Codebase coordinates for implementors

### Files to modify (all under `frontend/src/slices/agents/`)

**Views (2 files):**
- `views/AgentMcpView.vue` → rename to `views/AgentToolsView.vue` (549 lines)
  - Built-in tools array at line 84: `(['code_exec', 'web_search', 'file'] as const)` → remove, derive from API
  - Built-in query at line 75-81: `agentsApi.getBuiltinTools(agentId)` → replace with `agentsApi.listTools`
  - Built-in mutation at line 91-100: `agentsApi.setBuiltinTools` → replace with per-tool `patchTool`
  - MCP binding query at line 59-62: `agentKeys.mcpBindings(agentId)` → `agentKeys.tools(agentId)`
  - MCP source filter at line 68: `.filter((b) => b.source !== 'builtin')` → `.filter(t => t.tool_type === 'hosted_mcp')`
  - Modal form schema at line 119-124: `mcpBindingCreateSchema` → `mcpToolCreateSchema`
  - SCodeEditor for config JSON at line 481-485: reuse as-is for MCP config; clone for function params (Phase E)
  - All shared UI imports at lines 8-39: `SPageHeader, STable, SBadge, SButton, SCard, SToggle, SDropdown, SModal, SFormField, SInput, SSelect, STextarea, SCodeEditor, SAccordion, SAlert, SEmptyState` from `@shared/ui`
  - Toast at line 44: `const toast = useToast()` — pattern stays
  - Mutations at lines 178-237: rename method calls; keep TanStack Query invalidation pattern
- `views/AgentDetailView.vue`
  - Tab array at line 392-398: change `key: 'mcp'` → `key: 'tools'`; update i18n key + icon; keep dynamic badge
  - MCP preview section at lines 780-849 (inside `v-show="activeTab === 'mcp'"`) → `'tools'`; simplify to summary
  - Route link at line 831-847: `{ name: 'agents.mcp' }` → `{ name: 'agents.tools' }`

**API (1 file):**
- `api/index.ts`
  - HTTP client: `import { http } from '@shared/transport'` (line 1) — Axios instance with `/api` baseURL
  - Replace 7 MCP/builtin methods (lines 231-250) with 5 tool methods
  - Keep multipart upload helper (lines 199-207) for Phase C/D reuse
  - Interfaces at lines 104-146: `McpBinding` → `AgentTool`; `BuiltinToolsState` → remove

**Types (1 file):**
- `types/schemas.ts:74-81` — `mcpBindingCreateSchema` → `mcpToolCreateSchema` (B.2)
  - Zod `discriminatedUnion` is available (Zod 3.23.8 in package.json)

**Queries (1 file):**
- `queries/index.ts:16-19` — replace `mcpBindings(agentId)` + `builtinTools(agentId)` with `tools(agentId)`

**Routes (1 file):**
- `routes.ts:35-39` — rename `agents.mcp` → `agents.tools`; add redirect for old path

**Composables (1 file):**
- `composables/useMcpTest.ts` → `useToolTest.ts` (51 lines)
  - Calls `agentsApi.testMcpBinding` at line 23 → `agentsApi.testTool`
  - Toast keys at lines 28-33: `agents.mcp.testOk/testBad/testFailed` → `agents.tools.mcp.testOk/...`

**Locales (2 files):**
- `locales/en.json:229-298` — `agents.builtinTools.*` (12 keys) + `agents.mcp.*` (58 keys) → `agents.tools.*`
- `locales/zh-TW.json:229-298` — mirror structure

### Shared components used (verified present)

| Component | File | Key Props |
|---|---|---|
| `SToggle` | `shared/ui/SToggle.vue` | `modelValue`, `disabled`, `size`, `id` |
| `SBadge` | `shared/ui/SBadge.vue` | `variant` (info/success/warning/danger/neutral), `size`, `dot`, `removable` |
| `SModal` | `shared/ui/SModal.vue` | `open`, `title`, `size` (sm/md/lg/xl/full), `closable`, `persistent` |
| `SCodeEditor` | `shared/ui/SCodeEditor.vue` | `modelValue`, `language` (json/yaml/markdown/text), `rows`, `readonly` — textarea-based, Tab indent |
| `SCard` | `shared/ui/SCard.vue` | slot-based |
| `STable` | `shared/ui/STable.vue` | `columns`, `data`, `row-key` |
| `useToast` | `shared/composables/useToast.ts` | `.success(msg)` / `.error(msg)` / `.warning(msg)` / `.info(msg)` — wraps vue-sonner |

### API client generation

```bash
pnpm run gen:api
# Runs: openapi --input ../backend/openapi.json --output src/shared/api-client --client axios --useOptions --useUnionTypes
# Tool: openapi-typescript-codegen
# Note: agents slice API (api/index.ts) is HAND-WRITTEN, not auto-generated.
#       The generated client at src/shared/api-client/ is a reference; the slice wraps it.
```

## Cross-cutting checklist

1. **Slice boundaries.** All work stays in `slices/agents` + `shared`; no
   cross-slice imports (eslint-plugin-boundaries).
2. **Design system.** Reuse `SToggle`, card, modal, table primitives; Design D
   light blue/grey; icons via `@heroicons/vue`; no emojis.
3. **A11y.** Toggles labelled; modal focus-trapped (follow `docs/UI/11`).
4. **Type coverage.** Keep ≥95% (the J gate threshold).
