# 06 — Agents

> Agent configuration, RAG knowledge bases, GraphRAG, and MCP server bindings.
> All views use AppShell layout with 24px content padding. Every user-facing string via `$t()`.

---

## 1. AgentListView

**File**: `src/slices/agents/views/AgentListView.vue`
**Route**: `/projects/:projectId/agents`
**API**: `GET /api/projects/{project_id}/agents`

### 1.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|  [< Projects] / ProjectName / Agents          [+ Create Agent]   |
+------------------------------------------------------------------+
|                                                                    |
| SSearchInput  [Model: All v]  [Status: All v]                    |
|                                                                    |
| STable                                                             |
| +------+----------+--------+---------+-------+------+---------+   |
| |      | Name     | Model  | Key Grp | RAG   | A2A  | Actions |   |
| +------+----------+--------+---------+-------+------+---------+   |
| |      | My Agent | claude | prod-k  | docs  | --   |  [...] |   |
| |      | Coder    | openai | dev-k   | --    | On   |  [...] |   |
| |      | Planner  | gemini | prod-k  | specs | On   |  [...] |   |
| +------+----------+--------+---------+-------+------+---------+   |
|                                                                    |
| SPagination   Showing 1-3 of 3                                    |
+------------------------------------------------------------------+
```

### 1.2 Page Header

**Component**: `SPageHeader`

| Prop | Value |
|------|-------|
| `breadcrumbs` | `[{ label: $t('nav.projects'), to: '/projects' }, { label: projectName }, { label: $t('agents.list.title') }]` |
| `title` | `$t('agents.list.title')` ("Agents") |
| Actions slot | `SButton` variant=primary, icon-left `PlusIcon`: `$t('agents.list.create')` ("Create Agent") |

Create button navigates to `/agents/new` (AgentDetailView in create mode).

### 1.3 Filters

Horizontal row below header with 16px gap between items.

| Filter | Component | Options |
|--------|-----------|---------|
| Search | `SSearchInput` placeholder `$t('agents.list.searchPlaceholder')` | Filters by `name` client-side |
| Model | `SSelect` | `All`, `claude`, `openai`, `gemini` |
| Status | `SSelect` | `All`, `Active`, `Deleted` (admin only) |

### 1.4 Table Columns

**Component**: `STable` with `stickyHeader`

| Column | Key | Sortable | Width | Renderer |
|--------|-----|----------|-------|----------|
| Name | `name` | Yes | auto | Text, clickable link to detail |
| Model | `model_hint` | Yes | 100px | `SBadge` neutral: `claude` / `openai` / `gemini` |
| Key Group | `key_group_id` | No | 140px | Resolved group name (lookup from cache) |
| RAG | `rag_config_id` | No | 100px | Config name if set, `--` muted if null |
| A2A | `a2a_enabled` | Yes | 60px | `SBadge` success "On" or `--` muted |
| Actions | — | No | 48px | `SDropdown` with `EllipsisVerticalIcon` trigger |

**Row click**: navigates to `/agents/{agentId}` (AgentDetailView).

**Actions dropdown items**:

| Key | Label | Icon | Variant |
|-----|-------|------|---------|
| `edit` | `$t('common.edit')` | `PencilSquareIcon` | default |
| `duplicate` | `$t('agents.list.duplicate')` | `DocumentDuplicateIcon` | default |
| `divider` | — | — | divider |
| `delete` | `$t('common.delete')` | `TrashIcon` | danger |

**Delete action**: opens `SConfirmDialog` variant=danger. Title: `$t('agents.detail.confirmDelete')`. Calls `DELETE /api/agents/{agentId}` with `If-Match: {version}`.

### 1.5 Empty State

**Component**: `SEmptyState`

| Prop | Value |
|------|-------|
| `icon` | `CpuChipIcon` |
| `title` | `$t('agents.list.emptyTitle')` ("No agents yet") |
| `description` | `$t('agents.list.emptyDescription')` ("Create your first AI agent to get started.") |
| Action slot | `SButton` primary: `$t('agents.list.create')` |

### 1.6 Loading State

`STable` with `loading=true` renders 5 skeleton rows via `SSkeleton`.

### 1.7 Error State

`SAlert` variant=danger with retry button. Title: `$t('agents.list.loadError')`.

### 1.8 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 1024px | Full table with all columns |
| 768-1023px | Hide Key Group and RAG columns |
| < 768px | Card list layout: each agent as `SCard` with name, model badge, actions dropdown |

### 1.9 Components Used

`SPageHeader`, `SSearchInput`, `SSelect`, `STable`, `SBadge`, `SButton`, `SDropdown`, `SEmptyState`, `SConfirmDialog`, `SPagination`, `SSkeleton`, `SAlert`

---

## 2. AgentDetailView

**File**: `src/slices/agents/views/AgentDetailView.vue`
**Route**: `/agents/:agentId` (edit) or `/agents/new` (create with `projectId` query param)
**API**: `GET/PATCH /api/agents/{agentId}`, `POST /api/projects/{projectId}/agents`

This view uses a tabbed layout for the full agent configuration. Five tabs organize the settings into logical groups.

### 2.1 Overall Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|  [< Agents] / Agent Name                [Delete]  [Save Changes] |
+------------------------------------------------------------------+
|                                                                    |
| STabs                                                              |
| [ General ]  [ Prompt ]  [ Knowledge ]  [ MCP ]  [ Orchestration ]|
| ----------------------------------------------------------------- |
|                                                                    |
|   (Tab content area — see sections below)                         |
|                                                                    |
+------------------------------------------------------------------+
```

### 2.2 Page Header

**Component**: `SPageHeader`

| Prop | Value |
|------|-------|
| `breadcrumbs` | `[{ label: $t('nav.agents'), to: agentListRoute }, { label: agent.name \|\| $t('agents.detail.new') }]` |
| `title` | Agent name (edit) or `$t('agents.detail.new')` ("New Agent") |
| Actions slot | `SButton` variant=danger ghost `TrashIcon` (edit only) + `SButton` variant=primary `:loading="saving"` `$t('common.save')` |

**Save button**: disabled when form is pristine or invalid. Submits `PATCH` (edit) or `POST` (create).

**Delete button**: opens `SConfirmDialog` variant=danger. On confirm: `DELETE /api/agents/{agentId}` with `If-Match`, then navigates back to agent list.

**Optimistic locking**: edit mode sends `If-Match: {version}` header. On 409 Conflict: `SAlert` warning with "Someone else modified this agent. Reload to see changes." and a reload button.

### 2.3 Tab Configuration

**Component**: `STabs`

| Tab Key | Label | Icon | Badge |
|---------|-------|------|-------|
| `general` | `$t('agents.detail.tabs.general')` ("General") | `Cog6ToothIcon` | — |
| `prompt` | `$t('agents.detail.tabs.prompt')` ("Prompt") | `CommandLineIcon` | — |
| `knowledge` | `$t('agents.detail.tabs.knowledge')` ("Knowledge") | `BookOpenIcon` | — |
| `mcp` | `$t('agents.detail.tabs.mcp')` ("MCP") | `ServerIcon` | Binding count |
| `orchestration` | `$t('agents.detail.tabs.orchestration')` ("Orchestration") | `ArrowsPointingOutIcon` | — |

Default tab: `general`. Tab state preserved in URL query `?tab=prompt` for direct linking.

### 2.4 Tab: General

Core identity and model configuration.

```
+------------------------------------------------------------------+
| SCard "Agent Identity"                                            |
|                                                                    |
|  SFormField  Name *                                               |
|  [ My Agent                                             ]         |
|                                                                    |
|  SFormField  Model Provider *          SFormField  Model ID       |
|  [ claude          v ]                 [ claude-sonnet-4-2025... ]|
|                                                                    |
|  SFormField  Key Group *                                          |
|  [ production-keys  v ]                                           |
|                                                                    |
+------------------------------------------------------------------+
|                                                                    |
| SCard "Context Settings"                                          |
|                                                                    |
|  SFormField  Context Mode                                         |
|  ( ) General    ( ) Compact                                       |
|                                                                    |
|  SFormField  Token Cap  (visible when compact)                    |
|  [ 4096                                                 ]         |
|                                                                    |
+------------------------------------------------------------------+
```

**Component**: `AgentFormFields.vue` (existing, wraps form fields)

#### Form Fields — Identity

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `name` | `SInput` | text | required, 1-200 chars | `agents.form.name` |
| `model_hint` | `SSelect` | enum | required | `agents.form.modelHint` |
| `model_id` | `SInput` | text | optional, max 200 chars, whitespace-trimmed | `agents.form.modelId` |
| `key_group_id` | `SSelect` | UUID | required, populated from project key groups | `agents.form.keyGroup` |

`model_hint` options:

| Value | Label |
|-------|-------|
| `claude` | `$t('agents.form.modelHints.claude')` ("Anthropic Claude") |
| `openai` | `$t('agents.form.modelHints.openai')` ("OpenAI") |
| `gemini` | `$t('agents.form.modelHints.gemini')` ("Google Gemini") |

`model_id` help text: `$t('agents.form.modelIdHelp')` ("Optional override. Leave empty to use provider default.").

`key_group_id` options loaded from `GET /api/projects/{projectId}/key-groups`. If empty: `SAlert` variant=warning with link to create a key group first.

#### Form Fields — Context

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `context_mode` | `SRadio` group | enum | `general` or `compact` | `agents.form.contextMode` |
| `context_token_cap` | `SInput` type=number | int | required when `compact`, must be > 0 | `agents.form.contextTokenCap` |

`context_token_cap` field is conditionally visible: only shown when `context_mode === 'compact'`. Help text: `$t('agents.form.contextTokenCapHelp')` ("Maximum context window tokens in compact mode.").

### 2.5 Tab: Prompt

System prompt editor with strategy selector.

```
+------------------------------------------------------------------+
| SCard "System Prompt"                                             |
|                                                                    |
|  SFormField  Prompt Strategy                                      |
|  [ Full    v ]                                                    |
|  Help: "Full sends the complete prompt every turn..."             |
|                                                                    |
|  SFormField  System Prompt                                        |
|  +------------------------------------------------------------+  |
|  | SCodeEditor (language: markdown)                            |  |
|  |                                                              |  |
|  | You are a helpful coding assistant.                          |  |
|  | Follow these rules:                                          |  |
|  | 1. Write clean, documented code                              |  |
|  | 2. Explain your reasoning                                    |  |
|  |                                                              |  |
|  +------------------------------------------------------------+  |
|  12,345 / 100,000 characters                                     |
|                                                                    |
+------------------------------------------------------------------+
```

#### Form Fields — Prompt

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `prompt_strategy` | `SSelect` | enum | `full` or `lazy` | `agents.form.promptStrategy` |
| `system_prompt` | `SCodeEditor` | text | optional, max 100,000 chars | `agents.form.systemPrompt` |

`prompt_strategy` options:

| Value | Label | Description |
|-------|-------|-------------|
| `full` | `$t('agents.form.strategies.full')` ("Full") | Sends complete prompt every turn |
| `lazy` | `$t('agents.form.strategies.lazy')` ("Lazy") | Sends prompt only on first turn |

**Character counter**: displayed below the code editor in `--color-muted` 12px text. Format: `{current} / 100,000`. Turns `--color-warning` at 90,000+ and `--color-danger` at 99,000+.

**SCodeEditor props**: `language="markdown"`, `rows=16`, `placeholder=$t('agents.form.systemPromptPlaceholder')`.

### 2.6 Tab: Knowledge

RAG and GraphRAG association selectors.

```
+------------------------------------------------------------------+
| SCard "RAG Configuration"                                         |
|                                                                    |
|  SFormField  RAG Config                                           |
|  [ document-base       v ]  [View Config ->]                     |
|  Help: "Attach a RAG knowledge base for retrieval-augmented..."   |
|                                                                    |
+------------------------------------------------------------------+
|                                                                    |
| SCard "GraphRAG Configuration"                                    |
|                                                                    |
|  SFormField  GraphRAG Config                                      |
|  [ knowledge-graph     v ]  [View Config ->]                     |
|  Help: "Attach a GraphRAG config for graph-based retrieval..."    |
|                                                                    |
|  SAlert info (when graphrag attached)                             |
|  "Build state: idle.  Last built: 2024-12-01 14:30"              |
|                                                                    |
+------------------------------------------------------------------+
```

#### Form Fields — Knowledge

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `rag_config_id` | `SSelect` | UUID or null | optional, project-scoped | `agents.form.ragConfig` |
| `graphrag_config_id` | `SSelect` | UUID or null | optional, project-scoped | `agents.form.graphragConfig` |

`rag_config_id` options loaded from `GET /api/projects/{projectId}/rag-configs`. First option: `$t('agents.form.noRagConfig')` ("None — no RAG") with value `null`.

`graphrag_config_id` options loaded from `GET /api/projects/{projectId}/graphrag-configs`. First option: `$t('agents.form.noGraphragConfig')` ("None — no GraphRAG") with value `null`.

**View Config links**: `SButton` variant=link, navigates to the respective config detail view. Visible only when a config is selected.

**GraphRAG status alert**: when a GraphRAG config is attached, display an `SAlert` variant=info showing `last_build_state` and `last_build_at` (fetched from `GET /api/graphrag/{configId}/status`).

### 2.7 Tab: MCP

MCP server bindings summary. This tab shows a read-only summary with a link to the full MCP management view.

```
+------------------------------------------------------------------+
| SCard "MCP Server Bindings"                                       |
|                                                                    |
|  STable (compact)                                                 |
|  +--------+-----------------------------+------------+---------+  |
|  | Source | Reference                   | Tools      | Actions |  |
|  +--------+-----------------------------+------------+---------+  |
|  | builtin| web_search                  | All        |  [Test] |  |
|  | url    | https://mcp.example.com/sse | 3 allowed  |  [Test] |  |
|  | package| @mcp/server-filesystem      | All        |  [Test] |  |
|  +--------+-----------------------------+------------+---------+  |
|                                                                    |
|  [Manage MCP Bindings ->]                                         |
|                                                                    |
+------------------------------------------------------------------+
```

**MCP table columns**:

| Column | Key | Width | Renderer |
|--------|-----|-------|----------|
| Source | `source` | 80px | `SBadge` neutral |
| Reference | `reference` | auto | Monospace text, truncated |
| Tools | `allowed_tools` | 100px | "All" if empty array, or `"{n} allowed"` |
| Actions | — | 80px | `SButton` ghost sm "Test" |

**Test button**: calls `POST /api/agents/{agentId}/mcp/{bindingId}/test`. While testing: button shows loading spinner. On success: `SAlert` success with `"OK — {tool_names.length} tools discovered in {duration_ms}ms"`. On failure: `SAlert` danger with `error` message.

**Manage link**: `SButton` variant=link navigating to `/agents/{agentId}/mcp` (AgentMcpView).

**Empty state**: `SEmptyState` icon=`ServerIcon`, title=`$t('agents.mcp.emptyTitle')` ("No MCP bindings"), description=`$t('agents.mcp.emptyDescription')`, action button: "Add MCP Binding" linking to AgentMcpView.

**Data source**: `GET /api/agents/{agentId}/mcp`.

### 2.8 Tab: Orchestration

Wake-up rules, Agent-to-Agent, and workflow capabilities.

```
+------------------------------------------------------------------+
| SCard "Agent-to-Agent"                                            |
|                                                                    |
|  SFormField                                                       |
|  SToggle  Enable A2A communication                               |
|  Help: "Allow this agent to communicate with other agents..."     |
|                                                                    |
+------------------------------------------------------------------+
|                                                                    |
| SCard "Wake-up Configuration"                                     |
|                                                                    |
|  SFormField  Trigger: Every N Messages                            |
|  [ 5                                                    ]         |
|                                                                    |
|  SFormField  Trigger: Silence (minutes)                           |
|  [ 10                                                   ]         |
|                                                                    |
|  SFormField                                                       |
|  SToggle  Call Only (no autonomous wake)                          |
|                                                                    |
|  SFormField  Auto-stop Rounds                                     |
|  [ 3                                                    ]         |
|                                                                    |
+------------------------------------------------------------------+
|                                                                    |
| SCard "Workflow Capabilities"                                     |
|                                                                    |
|  SToggle  Can instruct other agents                               |
|  SToggle  Can approve actions                                     |
|  SToggle  Can create sub-agents                                   |
|                                                                    |
|  SFormField  Max Alive Sub-agents  (when can_create_subagent)     |
|  [ 5                                                    ]         |
|                                                                    |
+------------------------------------------------------------------+
```

#### Form Fields — A2A

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `a2a_enabled` | `SToggle` | boolean | — | `agents.form.a2aEnabled` |

#### Form Fields — Wake-up

These fields are stored as a JSON dict in `wakeup_config`. The form decomposes the dict into individual fields and reassembles on save.

| Dict Key | Component | Type | Validation | i18n |
|----------|-----------|------|------------|------|
| `every_n_messages` | `SInput` type=number | int or null | >= 1 if set | `agents.form.wakeupEveryN` |
| `silence_minutes` | `SInput` type=number | int or null | >= 1 if set | `agents.form.wakeupSilence` |
| `call_only` | `SToggle` | boolean | — | `agents.form.wakeupCallOnly` |
| `autostop_rounds` | `SInput` type=number | int or null | >= 1 if set | `agents.form.wakeupAutostop` |

Help text for wake-up card: `$t('agents.form.wakeupHelp')` ("Configure when this agent wakes up in a chatroom conversation.").

When `call_only` is enabled, `every_n_messages` and `silence_minutes` fields are disabled (greyed out) since the agent only responds when directly called.

#### Form Fields — Workflow Capabilities

These fields are stored as a JSON dict in `workflow_capabilities`. Same decompose/reassemble pattern as wake-up.

| Dict Key | Component | Type | Validation | i18n |
|----------|-----------|------|------------|------|
| `can_instruct` | `SToggle` | boolean | — | `agents.form.canInstruct` |
| `can_approve` | `SToggle` | boolean | — | `agents.form.canApprove` |
| `can_create_subagent` | `SToggle` | boolean | — | `agents.form.canCreateSubagent` |
| `max_alive_subagents` | `SInput` type=number | int | 1-20, required when `can_create_subagent` | `agents.form.maxAliveSubagents` |

`max_alive_subagents` is conditionally visible: only shown when `can_create_subagent === true`.

### 2.9 Form Validation (vee-validate + Zod)

The form uses `useForm()` from vee-validate with Zod schema validation (`agentCreateSchema` / `agentPatchSchema` from `types/schemas.ts`).

**Submit flow**:
1. Validate all fields across all tabs
2. If validation fails: switch to the first tab containing an error, show field-level errors
3. Reassemble `wakeup_config` and `workflow_capabilities` dicts from decomposed fields
4. Create mode: `POST /api/projects/{projectId}/agents` then navigate to edit view
5. Edit mode: `PATCH /api/agents/{agentId}` with `If-Match: {version}`
6. On success: toast `$t('agents.detail.saved')`, update local version
7. On 409 Conflict: show conflict alert (see 2.2)
8. On 422 Validation Error: map server errors to form fields

### 2.10 Loading State

Full-page `SSkeleton` composition:
- Page header skeleton (text line 200px)
- Tab bar skeleton (5 rectangles)
- Two card skeletons with 4 field skeletons each

### 2.11 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 1024px | Two-column grid inside cards where logical (e.g., model_hint + model_id side by side) |
| 768-1023px | Single column, full-width cards |
| < 768px | Tabs collapse to `SSelect` dropdown for tab switching. Cards stack vertically. Save/Delete buttons become fixed bottom bar |

### 2.12 Components Used

`SPageHeader`, `STabs`, `SCard`, `SFormField`, `SInput`, `SSelect`, `SRadio`, `SToggle`, `SCodeEditor`, `SButton`, `SBadge`, `STable`, `SAlert`, `SConfirmDialog`, `SEmptyState`, `SSkeleton`, `SDropdown`

---

## 3. RagConfigListView

**File**: `src/slices/agents/views/RagConfigListView.vue`
**Route**: `/projects/:projectId/rag-configs`
**API**: `GET /api/projects/{project_id}/rag-configs`

### 3.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|  [< Projects] / ProjectName / RAG Configs   [+ Create Config]   |
+------------------------------------------------------------------+
|                                                                    |
| SSearchInput                                                      |
|                                                                    |
| STable                                                             |
| +----------+----------+------------------+-------+---------+      |
| | Name     | Strategy | Embedding        | Top-K | Actions |      |
| +----------+----------+------------------+-------+---------+      |
| | doc-base | fixed    | openai/3-small   | 8     |  [...]  |      |
| | specs    | semantic | voyage/voyage-3  | 12    |  [...]  |      |
| +----------+----------+------------------+-------+---------+      |
|                                                                    |
| SPagination                                                       |
+------------------------------------------------------------------+
```

### 3.2 Page Header

**Component**: `SPageHeader`

| Prop | Value |
|------|-------|
| `breadcrumbs` | `[{ label: $t('nav.projects'), to: '/projects' }, { label: projectName }, { label: $t('agents.ragList.title') }]` |
| `title` | `$t('agents.ragList.title')` ("RAG Configurations") |
| Actions slot | `SButton` primary `PlusIcon`: `$t('agents.ragList.create')` |

**Create button**: opens `SModal` with RAG config creation form (see 3.4).

**Pre-check**: if the project has no embedding-capable key groups, the create button is disabled and a tooltip shows `$t('agents.ragList.noEmbedKeys')` ("Add an embedding API key first.").

### 3.3 Table Columns

**Component**: `STable`

| Column | Key | Sortable | Width | Renderer |
|--------|-----|----------|-------|----------|
| Name | `name` | Yes | auto | Text, clickable link to detail |
| Strategy | `chunk_strategy` | Yes | 100px | `SBadge` neutral: `fixed` / `semantic` |
| Embedding | — | No | 160px | `"{embed_provider}/{embed_model}"` monospace |
| Top-K | `top_k` | Yes | 70px | Numeric |
| Rerank | `rerank_enabled` | No | 80px | `SBadge` success "On" or `--` muted |
| Actions | — | No | 48px | `SDropdown` |

**Row click**: navigates to `/projects/{projectId}/rag-configs/{configId}` (RagConfigDetailView).

**Actions dropdown**:

| Key | Label | Icon | Variant |
|-----|-------|------|---------|
| `edit` | `$t('common.edit')` | `PencilSquareIcon` | default |
| `divider` | — | — | divider |
| `delete` | `$t('common.delete')` | `TrashIcon` | danger |

**Delete**: `SConfirmDialog` variant=danger. Warns that all documents and vector data will be permanently removed.

### 3.4 Create Modal

**Component**: `SModal` size=lg, title=`$t('agents.ragList.create')`

The create form contains all required RAG config fields. See Section 4.4 for full field definitions. The modal footer has Cancel (secondary) and Create (primary) buttons.

### 3.5 Empty State

`SEmptyState` icon=`DocumentTextIcon`, title=`$t('agents.ragList.emptyTitle')` ("No RAG configurations"), description=`$t('agents.ragList.emptyDescription')` ("Create a RAG config to give your agents a knowledge base."), action: Create button.

### 3.6 Components Used

`SPageHeader`, `SSearchInput`, `STable`, `SBadge`, `SButton`, `SDropdown`, `SModal`, `SFormField`, `SInput`, `SSelect`, `SToggle`, `SEmptyState`, `SConfirmDialog`, `SPagination`, `SSkeleton`, `SAlert`

---

## 4. RagConfigDetailView

**File**: `src/slices/agents/views/RagConfigDetailView.vue`
**Route**: `/projects/:projectId/rag-configs/:configId`
**API**: `GET/PATCH /api/rag-configs/{config_id}`, `GET/POST /api/rag-configs/{config_id}/documents`, `DELETE /api/rag-documents/{document_id}`
**WebSocket**: `ws://host/ws/rag-configs/{config_id}` (live ingestion progress)

### 4.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|  [< RAG Configs] / doc-base                  [Delete]  [Save]   |
+------------------------------------------------------------------+
|                                                                    |
| STabs                                                              |
| [ Settings ]  [ Documents ]                                       |
| ----------------------------------------------------------------- |
|                                                                    |
| (Settings tab)                                                    |
| SCard "Embedding"                                                 |
|  Provider: [openai v]  Model: [text-embedding-3-small v]         |
|  Key: [embed-key-group v]                                         |
|                                                                    |
| SCard "Chunking"                                                  |
|  Strategy: [fixed v]                                              |
|  Chunk Size: [512]   Overlap: [64]                                |
|                                                                    |
| SCard "Retrieval"                                                 |
|  Top-K: [8]                                                       |
|  SToggle: Enable Reranking                                        |
|  Provider: [cohere v]  Model: [rerank-3]  Key: [rerank-key v]    |
|                                                                    |
+------------------------------------------------------------------+
```

### 4.2 Tab Configuration

**Component**: `STabs`

| Tab Key | Label | Icon | Badge |
|---------|-------|------|-------|
| `settings` | `$t('agents.ragForm.tabs.settings')` ("Settings") | `Cog6ToothIcon` | — |
| `documents` | `$t('agents.ragForm.tabs.documents')` ("Documents") | `DocumentIcon` | Document count |

### 4.3 Page Header

**Component**: `SPageHeader`

| Prop | Value |
|------|-------|
| `breadcrumbs` | `[{ label: $t('agents.ragList.title'), to: ragListRoute }, { label: config.name }]` |
| `title` | Config name |
| Actions slot | `SButton` danger ghost (delete) + `SButton` primary (save) |

### 4.4 Settings Tab — Form Fields

#### Embedding Card

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `embed_provider` | `SSelect` | enum | required | `agents.ragForm.embedProvider` |
| `embed_model` | `SSelect` | string | required, must match whitelist | `agents.ragForm.embedModel` |
| `embed_key_id` | `SSelect` | UUID | required | `agents.ragForm.embedKey` |

`embed_provider` options: `openai`, `gemini`, `voyage`.

`embed_model` options change based on selected provider:

| Provider | Models |
|----------|--------|
| `openai` | `text-embedding-3-small`, `text-embedding-3-large` |
| `gemini` | `text-embedding-004` |
| `voyage` | `voyage-3` |

`embed_key_id` populated from project API keys filtered to the selected provider.

#### Chunking Card

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `chunk_strategy` | `SSelect` | enum | required | `agents.ragForm.chunkStrategy` |
| `chunk_params.chunk_size_tokens` | `SInput` type=number | int | required for `fixed`, default 512 | `agents.ragForm.chunkSize` |
| `chunk_params.chunk_overlap_tokens` | `SInput` type=number | int | required for `fixed`, default 64 | `agents.ragForm.chunkOverlap` |
| `chunk_params.similarity_threshold` | `SInput` type=number | float | required for `semantic`, 0-1, default 0.8 | `agents.ragForm.similarityThreshold` |

`chunk_strategy` options: `fixed`, `semantic`.

Conditional fields: when `fixed` is selected, show chunk size and overlap. When `semantic` is selected, show similarity threshold. The form decomposes `chunk_params` dict on load and reassembles on save.

#### Retrieval Card

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `top_k` | `SInput` type=number | int | 1-100, default 8 | `agents.ragForm.topK` |
| `rerank_enabled` | `SToggle` | boolean | — | `agents.ragForm.rerankEnabled` |
| `rerank_provider` | `SSelect` | enum or null | required when rerank enabled | `agents.ragForm.rerankProvider` |
| `rerank_model` | `SInput` | string or null | required when rerank enabled | `agents.ragForm.rerankModel` |
| `rerank_key_id` | `SSelect` | UUID or null | required when rerank enabled | `agents.ragForm.rerankKey` |

`rerank_provider` options: `cohere`. Additional providers may be added.

Rerank fields (`rerank_provider`, `rerank_model`, `rerank_key_id`) are conditionally visible: only shown when `rerank_enabled === true`.

### 4.5 Documents Tab

```
+------------------------------------------------------------------+
| SCard "Upload Documents"                                          |
|                                                                    |
|  SFileUpload                                                      |
|  +------------------------------------------------------------+  |
|  |  [^]  Drop files here or click to browse                   |  |
|  |       PDF, TXT, MD, DOCX — up to 32 MB                     |  |
|  +------------------------------------------------------------+  |
|                                                                    |
+------------------------------------------------------------------+
|                                                                    |
| SCard "Documents"                                                 |
|                                                                    |
|  STable                                                           |
|  +---------------------+------+--------+----------+---------+    |
|  | Filename            | Size | Status | Scanned  | Actions |    |
|  +---------------------+------+--------+----------+---------+    |
|  | architecture.pdf    | 2.1M | ready  | clean    | [x]     |    |
|  | api-spec.md         | 340K | ingest | pending  | [x]     |    |
|  | notes.txt           | 12K  | ready  | clean    | [x]     |    |
|  +---------------------+------+--------+----------+---------+    |
|                                                                    |
|  (Ingestion progress — shown during active ingestion)             |
|  SProgressBar  value=65  "Processing 13 / 20 chunks..."          |
|                                                                    |
+------------------------------------------------------------------+
```

#### File Upload

**Component**: `SFileUpload`

| Prop | Value |
|------|-------|
| `accept` | `.pdf,.txt,.md,.docx,application/pdf,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `maxSize` | `33554432` (32 MB) |
| `multiple` | `true` |

**Upload flow**:
1. User drops or selects files
2. Validate size (max 32 MB per file) and type
3. For each file: `POST /api/rag-configs/{configId}/documents` as multipart form-data
4. On success: add document to table, begin WebSocket progress tracking
5. On error: `SAlert` danger with specific error (size exceeded, type rejected, etc.)

**AuthZ note**: document upload requires Project Owner role. If the current user lacks this role, the upload zone is hidden and replaced with `SAlert` variant=info: `$t('agents.rag.ownerRequired')` ("Only project owners can upload documents.").

#### Documents Table

**Component**: `STable`

| Column | Key | Width | Renderer |
|--------|-----|-------|----------|
| Filename | `filename` | auto | Text with file type icon |
| Size | `size_bytes` | 80px | Formatted: KB/MB |
| Status | `status` | 100px | `SBadge` — see status map |
| Scanned | `scan_status` | 100px | `SBadge` — see scan map |
| Actions | — | 48px | `SButton` ghost icon-only `TrashIcon` |

**Status badge map**:

| Status | Variant | Label |
|--------|---------|-------|
| `ingesting` | `info` | `$t('agents.rag.status.ingesting')` |
| `ready` | `success` | `$t('agents.rag.status.ready')` |
| `failed` | `danger` | `$t('agents.rag.status.failed')` |
| `quarantined` | `warning` | `$t('agents.rag.status.quarantined')` |

**Scan status badge map**:

| Status | Variant | Label |
|--------|---------|-------|
| `pending` | `neutral` | `$t('agents.rag.scan.pending')` |
| `clean` | `success` | `$t('agents.rag.scan.clean')` |
| `quarantined` | `danger` | `$t('agents.rag.scan.quarantined')` |
| `skipped` | `neutral` | `$t('agents.rag.scan.skipped')` |

**Delete document**: `SConfirmDialog` variant=danger. On confirm: `DELETE /api/rag-documents/{documentId}`.

**Empty state**: `SEmptyState` icon=`DocumentIcon`, title=`$t('agents.rag.emptyTitle')` ("No documents uploaded"), description=`$t('agents.rag.emptyDescription')`.

### 4.6 Real-Time Ingestion Progress

**Composable**: `useRagConfigSocket(configId)` (existing)

**WebSocket endpoint**: `/ws/rag-configs/{config_id}`

**Authentication**: JWT via WebSocket subprotocol header.

**Events and UI behavior**:

| Event | UI Update |
|-------|-----------|
| `ingestion.started` | Show `SProgressBar` indeterminate. Status text: `$t('agents.rag.ingestionStarted')` |
| `ingestion.progress` | Update `SProgressBar` value: `(processed / total) * 100`. Text: `$t('agents.rag.ingestionProgress', { processed, total })` |
| `ingestion.indexing` | `SProgressBar` indeterminate. Text: `$t('agents.rag.indexing')` ("Indexing vectors...") |
| `ingestion.completed` | Hide progress bar. Update document status to `ready`. Toast success. |
| `ingestion.failed` | `SAlert` danger with error message. Update document status to `failed`. |

**Progress bar placement**: below the documents table inside the Documents card, full width.

**Reconnection**: on WebSocket disconnect, the composable automatically reconnects with exponential backoff (1s, 2s, 4s, max 30s). On reconnect, it syncs current config status from the REST API.

### 4.7 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 1024px | Two-column grid for embedding fields (provider + model side by side) |
| 768-1023px | Single column cards |
| < 768px | Tabs collapse to `SSelect`. File upload zone shrinks. Document table hides Size and Scanned columns |

### 4.8 Components Used

`SPageHeader`, `STabs`, `SCard`, `SFormField`, `SInput`, `SSelect`, `SToggle`, `SButton`, `STable`, `SBadge`, `SFileUpload`, `SProgressBar`, `SAlert`, `SEmptyState`, `SConfirmDialog`, `SSkeleton`

---

## 5. GraphragConfigListView

**File**: `src/slices/agents/views/GraphragConfigListView.vue`
**Route**: `/projects/:projectId/graphrag-configs`
**API**: `GET /api/projects/{project_id}/graphrag-configs`

### 5.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|  [< Projects] / ProjectName / GraphRAG        [+ Create Config]  |
+------------------------------------------------------------------+
|                                                                    |
| STable                                                             |
| +------------+-----------+------------+----------+---------+      |
| | Agent      | Key Group | Build State| Last Built| Actions|      |
| +------------+-----------+------------+----------+---------+      |
| | My Agent   | builder-k | idle       | 12/01    | [...]   |      |
| | Coder      | builder-k | running    | --       | [...]   |      |
| +------------+-----------+------------+----------+---------+      |
|                                                                    |
+------------------------------------------------------------------+
```

### 5.2 Page Header

**Component**: `SPageHeader`

| Prop | Value |
|------|-------|
| `breadcrumbs` | `[{ label: $t('nav.projects'), to: '/projects' }, { label: projectName }, { label: $t('agents.graphragList.title') }]` |
| `title` | `$t('agents.graphragList.title')` ("GraphRAG Configurations") |
| Actions slot | `SButton` primary `PlusIcon`: `$t('agents.graphragList.create')` |

**Create button**: opens `SModal` with creation form (see 5.4).

### 5.3 Table Columns

**Component**: `STable`

| Column | Key | Sortable | Width | Renderer |
|--------|-----|----------|-------|----------|
| Agent | `agent_id` | Yes | auto | Resolved agent name (link to agent detail) |
| Builder Key Group | `builder_key_group_id` | No | 160px | Resolved group name |
| Build State | `last_build_state` | Yes | 120px | `SBadge` — see state map |
| Last Built | `last_build_at` | Yes | 120px | Relative time or `--` |
| Actions | — | No | 120px | `SButton` ghost "Build" + `SDropdown` |

**Build state badge map**:

| State | Variant | Label |
|-------|---------|-------|
| `idle` | `neutral` | `$t('agents.graphragList.states.idle')` |
| `running` | `info` | `$t('agents.graphragList.states.running')` (with dot animation) |
| `neo4j_committed` | `info` | `$t('agents.graphragList.states.neo4jCommitted')` |
| `qdrant_committed` | `info` | `$t('agents.graphragList.states.qdrantCommitted')` |
| `failed` | `danger` | `$t('agents.graphragList.states.failed')` |
| `failed_compensating` | `warning` | `$t('agents.graphragList.states.compensating')` |

**Build button**: `SButton` ghost sm. Calls `POST /api/graphrag/{configId}/build`. Disabled when `last_build_state === 'running'`. On success (202): update state to `running`, toast info. On error: toast danger.

**Actions dropdown**:

| Key | Label | Icon | Variant |
|-----|-------|------|---------|
| `status` | `$t('agents.graphragList.viewStatus')` | `EyeIcon` | default |
| `edit` | `$t('common.edit')` | `PencilSquareIcon` | default |
| `divider` | — | — | divider |
| `delete` | `$t('common.delete')` | `TrashIcon` | danger |

**Status action**: opens `SDrawer` showing detailed status (see 5.5).

**Delete**: `SConfirmDialog` variant=danger. Warns that the Neo4j subgraph and Qdrant vectors will be permanently purged.

### 5.4 Create Modal

**Component**: `SModal` size=md, title=`$t('agents.graphragList.create')`

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `agent_id` | `SSelect` | UUID | required, must be an agent without existing GraphRAG config | `agents.graphragForm.agent` |
| `builder_key_group_id` | `SSelect` | UUID | required, must differ from agent's key_group_id | `agents.graphragForm.builderKeyGroup` |
| `trigger_config` | See below | dict | — | `agents.graphragForm.trigger` |

**agent_id options**: agents in the current project that do not already have a GraphRAG config attached (1:1 constraint).

**builder_key_group_id validation**: if the user selects the same key group as the selected agent's `key_group_id`, show inline error: `$t('agents.graphragForm.builderKeyGroupSameError')` ("Builder key group must differ from the agent's key group.").

**trigger_config sub-fields** (displayed as an `SAccordion` "Trigger Settings"):

| Dict Key | Component | Type | i18n |
|----------|-----------|------|------|
| `every_n_messages` | `SInput` type=number | int or null | `agents.graphragForm.triggerEveryN` |
| `silence_minutes` | `SInput` type=number | int or null | `agents.graphragForm.triggerSilence` |
| `manual` | `SToggle` | boolean | `agents.graphragForm.triggerManual` |

Help text: `$t('agents.graphragForm.triggerHelp')` ("When to automatically rebuild the knowledge graph.").

### 5.5 Status Drawer

**Component**: `SDrawer` side=right, size=md, title=`$t('agents.graphragList.statusTitle')`

Displays detailed build status from `GET /api/graphrag/{configId}/status`.

```
+----------------------------+
| GraphRAG Status        [X] |
+----------------------------+
|                             |
| Build State                 |
| SBadge [idle]               |
|                             |
| Last Build                  |
| 2024-12-01 14:30 UTC        |
|                             |
| Last Error                  |
| (none)                      |
|                             |
| [Trigger Build]             |
|                             |
+----------------------------+
```

### 5.6 Empty State

`SEmptyState` icon=`CircleStackIcon`, title=`$t('agents.graphragList.emptyTitle')` ("No GraphRAG configurations"), description=`$t('agents.graphragList.emptyDescription')` ("Create a GraphRAG config to build a knowledge graph from agent conversations."), action: Create button.

### 5.7 Polling

When any config has `last_build_state` in `['running', 'neo4j_committed', 'qdrant_committed', 'failed_compensating']`, the view polls `GET /api/graphrag/{configId}/status` every 5 seconds until the state settles to `idle` or `failed`. Polling uses TanStack Query's `refetchInterval` option.

### 5.8 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 1024px | Full table |
| 768-1023px | Hide Builder Key Group column |
| < 768px | Card list layout: each config as `SCard` with agent name, state badge, build button |

### 5.9 Components Used

`SPageHeader`, `STable`, `SBadge`, `SButton`, `SDropdown`, `SModal`, `SDrawer`, `SFormField`, `SInput`, `SSelect`, `SToggle`, `SAccordion`, `SEmptyState`, `SConfirmDialog`, `SPagination`, `SSkeleton`, `SAlert`

---

## 6. AgentMcpView

**File**: `src/slices/agents/views/AgentMcpView.vue`
**Route**: `/agents/:agentId/mcp`
**API**: `GET/POST/PATCH/DELETE /api/agents/{agentId}/mcp`, `POST /api/agents/{agentId}/mcp/{bindingId}/test`

### 6.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|  [< Agent: My Agent] / MCP Bindings          [+ Add Binding]    |
+------------------------------------------------------------------+
|                                                                    |
| SAlert info                                                       |
| "MCP bindings let this agent call external tool servers.          |
|  Egress must be allowlisted at project level."   [Manage ->]     |
|                                                                    |
| STable                                                             |
| +--------+---------------------------+-----------+--------+------+|
| | Source | Reference                 | Tools     | Status |Action||
| +--------+---------------------------+-----------+--------+------+|
| |builtin | web_search                | All       |        |[...] ||
| |url     | https://mcp.example.com   | 3 allowed |        |[...] ||
| |package | @mcp/server-filesystem    | All       |        |[...] ||
| +--------+---------------------------+-----------+--------+------+|
|                                                                    |
+------------------------------------------------------------------+
```

### 6.2 Page Header

**Component**: `SPageHeader`

| Prop | Value |
|------|-------|
| `breadcrumbs` | `[{ label: agent.name, to: agentDetailRoute }, { label: $t('agents.mcp.title') }]` |
| `title` | `$t('agents.mcp.title')` ("MCP Bindings") |
| Actions slot | `SButton` primary `PlusIcon`: `$t('agents.mcp.add')` |

### 6.3 Info Alert

`SAlert` variant=info. Description: `$t('agents.mcp.infoAlert')`. Actions slot: `SButton` variant=link navigating to `/projects/{projectId}/mcp/egress-allowlist` ("Manage Egress Allowlist").

### 6.4 Table Columns

**Component**: `STable`

| Column | Key | Sortable | Width | Renderer |
|--------|-----|----------|-------|----------|
| Source | `source` | Yes | 90px | `SBadge` neutral |
| Reference | `reference` | No | auto | Monospace text. Truncated with tooltip for URLs |
| Tools | `allowed_tools` | No | 120px | "All tools" if array empty; otherwise `"{n} allowed"` with tooltip listing names |
| Actions | — | No | 120px | Test button + `SDropdown` |

**Source badge styling**:

| Source | Badge text | Help |
|--------|-----------|------|
| `builtin` | "Built-in" | `file`, `web_search`, `code_exec` |
| `url` | "URL" | SSE/HTTP MCP server endpoint |
| `package` | "Package" | npx/uvx package spec |

**Actions dropdown**:

| Key | Label | Icon | Variant |
|-----|-------|------|---------|
| `test` | `$t('agents.mcp.test')` | `PlayIcon` | default |
| `edit` | `$t('common.edit')` | `PencilSquareIcon` | default |
| `divider` | — | — | divider |
| `delete` | `$t('common.delete')` | `TrashIcon` | danger |

### 6.5 Test Flow

**Trigger**: "Test" action from dropdown or inline test button.

**API**: `POST /api/agents/{agentId}/mcp/{bindingId}/test`

**UI flow**:
1. Button shows `SLoadingSpinner` while testing
2. On success (`ok: true`): toast success with `$t('agents.mcp.testOk', { count: tool_names.length, ms: duration_ms })`. Example: "Connection OK — 5 tools discovered in 230ms"
3. On failure (`ok: false`): `SModal` size=sm showing error details. Title: `$t('agents.mcp.testFailed')`. Body: error message in `SCodeEditor` readonly. Duration shown in muted text.
4. On network error: toast danger with generic error message.

### 6.6 Add Binding Modal

**Component**: `SModal` size=lg, title=`$t('agents.mcp.add')`

```
+-----------------------------------------------------+
| Add MCP Binding                                 [X]  |
+-----------------------------------------------------+
|                                                       |
|  SFormField  Source Type *                            |
|  [builtin v]                                          |
|                                                       |
|  SFormField  Reference *                             |
|  [web_search                                    ]    |
|  Help: (changes based on source type)                |
|                                                       |
|  SFormField  Allowed Tools                           |
|  STextarea (comma-separated, one per line)           |
|  [                                              ]    |
|  Help: "Leave empty to allow all tools."             |
|                                                       |
|  SAccordion  "Advanced Configuration"                |
|    SCodeEditor (JSON)                                |
|    { }                                                |
|                                                       |
+-----------------------------------------------------+
|                          [Cancel]   [Add Binding]    |
+-----------------------------------------------------+
```

#### Form Fields

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `source` | `SSelect` | enum | required | `agents.mcp.source` |
| `reference` | `SInput` | string | required, 1-2000 chars | `agents.mcp.reference` |
| `allowed_tools` | `STextarea` | string[] | optional, max 200 items | `agents.mcp.allowedTools` |
| `config` | `SCodeEditor` language=json | dict | valid JSON | `agents.mcp.config` |

`source` options:

| Value | Label | Reference help text |
|-------|-------|---------------------|
| `builtin` | `$t('agents.mcp.sources.builtin')` ("Built-in") | `$t('agents.mcp.referenceHelp.builtin')` ("Enter built-in name: file, web_search, or code_exec") |
| `url` | `$t('agents.mcp.sources.url')` ("URL") | `$t('agents.mcp.referenceHelp.url')` ("SSE or HTTP endpoint URL for the MCP server") |
| `package` | `$t('agents.mcp.sources.package')` ("Package") | `$t('agents.mcp.referenceHelp.package')` ("npx or uvx package specifier, e.g. @modelcontextprotocol/server-filesystem") |

**Builtin validation**: when `source === 'builtin'`, `reference` must be one of `file`, `web_search`, `code_exec`.

**Allowed tools input**: `STextarea` with rows=3. User enters tool names separated by commas or newlines. Parsed into `string[]` on submit. Help text: `$t('agents.mcp.allowedToolsHelp')` ("Leave empty to allow all discovered tools. Enter specific tool names to restrict access.").

**Config field**: inside `SAccordion` labeled `$t('agents.mcp.advancedConfig')` ("Advanced Configuration"), default collapsed. `SCodeEditor` language=json, rows=6. Validated as valid JSON on submit.

### 6.7 Edit Binding

Same modal as Add, but with pre-populated values. Only `allowed_tools` and `config` are editable for existing bindings (`source` and `reference` are read-only, displayed as muted text). API: `PATCH /api/agents/{agentId}/mcp/{bindingId}`.

### 6.8 Delete Binding

`SConfirmDialog` variant=danger. On confirm: `DELETE /api/agents/{agentId}/mcp/{bindingId}`.

### 6.9 Empty State

`SEmptyState` icon=`ServerIcon`, title=`$t('agents.mcp.emptyTitle')` ("No MCP bindings"), description=`$t('agents.mcp.emptyDescription')` ("Add an MCP server to give this agent access to external tools."), action: "Add Binding" button.

### 6.10 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 1024px | Full table |
| 768-1023px | Reference column truncated to 200px max |
| < 768px | Card layout: each binding as `SCard` with source badge, reference, tool count, test/edit/delete buttons in a row |

### 6.11 Components Used

`SPageHeader`, `STable`, `SBadge`, `SButton`, `SDropdown`, `SModal`, `SFormField`, `SInput`, `SSelect`, `STextarea`, `SCodeEditor`, `SAccordion`, `SAlert`, `SEmptyState`, `SConfirmDialog`, `SSkeleton`, `SLoadingSpinner`

---

## 7. McpEgressAllowlistView

**File**: `src/slices/agents/views/McpEgressAllowlistView.vue`
**Route**: `/projects/:projectId/mcp/egress-allowlist`
**API**: `GET/POST/DELETE /api/projects/{project_id}/mcp/egress-allowlist`, `PUT /api/projects/{project_id}/mcp/egress-allowlist`

### 7.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|  [< Projects] / ProjectName / MCP Egress Allowlist               |
+------------------------------------------------------------------+
|                                                                    |
| SAlert info                                                       |
| "Only MCP servers connecting to these hostnames are allowed.      |
|  Project owners can manage this list."                            |
|                                                                    |
| SCard "Add Hostname"                                              |
|  SFormField  Hostname *          SFormField  Note                 |
|  [ api.example.com      ]       [ API server       ]  [+ Add]   |
|                                                                    |
| SCard "Allowed Hostnames"                                         |
|  STable                                                           |
|  +----------------------+------------------+----------+---------+ |
|  | Hostname             | Note             | Added    | Actions | |
|  +----------------------+------------------+----------+---------+ |
|  | api.example.com      | API server       | 12/01    | [x]     | |
|  | mcp.internal.io      | Internal MCP     | 11/28    | [x]     | |
|  | cdn.provider.net     | --               | 11/15    | [x]     | |
|  +----------------------+------------------+----------+---------+ |
|                                                                    |
+------------------------------------------------------------------+
```

### 7.2 Page Header

**Component**: `SPageHeader`

| Prop | Value |
|------|-------|
| `breadcrumbs` | `[{ label: $t('nav.projects'), to: '/projects' }, { label: projectName }, { label: $t('agents.egress.title') }]` |
| `title` | `$t('agents.egress.title')` ("MCP Egress Allowlist") |

No action buttons in the header — the add form is inline.

### 7.3 Info Alert

`SAlert` variant=info. Description: `$t('agents.egress.infoAlert')` ("Only MCP server connections to hostnames in this list are permitted. All other egress is blocked. Only project owners can manage this list.").

### 7.4 Add Hostname Form

**Component**: `SCard` containing an inline form row.

| Field | Component | Type | Validation | i18n |
|-------|-----------|------|------------|------|
| `hostname` | `SInput` | string | required, 1-253 chars, RFC 1123 hostname | `agents.egress.hostname` |
| `note` | `SInput` | string | optional, max 500 chars | `agents.egress.note` |

**Layout**: three items in a horizontal flex row with 12px gap. Hostname input (flex: 2), Note input (flex: 1), Add button.

**Add button**: `SButton` variant=primary size=md `PlusIcon`: `$t('agents.egress.add')` ("Add"). Calls `POST /api/projects/{projectId}/mcp/egress-allowlist`.

**Hostname validation regex**: `^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$`

On validation error: `SFormField` error message below hostname input.

On duplicate hostname (409): toast warning `$t('agents.egress.duplicateError')` ("This hostname is already in the allowlist.").

**AuthZ**: the add form is only visible to project owners. Non-owners see a muted info line: `$t('agents.egress.ownerOnly')` ("Only project owners can modify the egress allowlist.").

### 7.5 Table Columns

**Component**: `STable`

| Column | Key | Sortable | Width | Renderer |
|--------|-----|----------|-------|----------|
| Hostname | `hostname` | Yes | auto | Monospace text |
| Note | `note` | No | 200px | Text or `--` muted if null |
| Added | `added_at` | Yes | 120px | Relative time |
| Added By | `added_by_user_id` | No | 140px | Resolved user email (admin view only, hidden for regular users) |
| Actions | — | No | 48px | `SButton` ghost icon-only `TrashIcon` |

**Delete action**: `SConfirmDialog` variant=danger. On confirm: `DELETE /api/projects/{projectId}/mcp/egress-allowlist/{hostname}`.

**Delete button**: only visible to project owners.

### 7.6 Empty State

`SEmptyState` icon=`ShieldCheckIcon`, title=`$t('agents.egress.emptyTitle')` ("No hostnames allowed"), description=`$t('agents.egress.emptyDescription')` ("Add hostnames that MCP servers in this project are allowed to connect to. Without entries, all MCP egress is blocked."), action: focus the hostname input (no separate button — the add form is always visible for owners).

### 7.7 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 1024px | Inline add form row, full table |
| 768-1023px | Add form stacks vertically (hostname full width, note full width, button full width) |
| < 768px | Hide Note and Added By columns. Add form stacks vertically |

### 7.8 Components Used

`SPageHeader`, `SCard`, `STable`, `SFormField`, `SInput`, `SButton`, `SAlert`, `SEmptyState`, `SConfirmDialog`, `SSkeleton`

---

## 8. Files Summary

### View Files

| File | View | Phase |
|------|------|-------|
| `src/slices/agents/views/AgentListView.vue` | Agent list | U3 |
| `src/slices/agents/views/AgentDetailView.vue` | Agent CRUD with tabs | U3 |
| `src/slices/agents/views/RagConfigListView.vue` | RAG config list | U3 |
| `src/slices/agents/views/RagConfigDetailView.vue` | RAG config + documents | U3 |
| `src/slices/agents/views/GraphragConfigListView.vue` | GraphRAG config list | U3 |
| `src/slices/agents/views/AgentMcpView.vue` | MCP bindings per agent | U3 |
| `src/slices/agents/views/McpEgressAllowlistView.vue` | Project MCP egress allowlist | U3 |

### Component Files

| File | Description | Phase |
|------|-------------|-------|
| `src/slices/agents/components/AgentFormFields.vue` | Reusable agent form fields (existing — restyle) | U3 |

### Supporting Files

| File | Description |
|------|-------------|
| `src/slices/agents/routes.ts` | Route definitions for all agent views |
| `src/slices/agents/api/index.ts` | API client functions (TanStack Query wrappers) |
| `src/slices/agents/types/schemas.ts` | Zod validation schemas |
| `src/slices/agents/types/index.ts` | TypeScript interfaces |
| `src/slices/agents/queries/index.ts` | TanStack Query key factory and query hooks |
| `src/slices/agents/composables/useRagConfigSocket.ts` | WebSocket composable for RAG ingestion |
| `src/slices/agents/locales/en.json` | English translations |
| `src/slices/agents/locales/zh-TW.json` | Traditional Chinese translations |

### Route Map

| Route | View | Sidebar | Layout |
|-------|------|---------|--------|
| `/projects/:projectId/agents` | AgentListView | Normal | AppShell, 24px padding |
| `/agents/:agentId` | AgentDetailView | Normal | AppShell, 24px padding |
| `/agents/new?projectId=:pid` | AgentDetailView (create) | Normal | AppShell, 24px padding |
| `/projects/:projectId/rag-configs` | RagConfigListView | Normal | AppShell, 24px padding |
| `/projects/:projectId/rag-configs/:configId` | RagConfigDetailView | Normal | AppShell, 24px padding |
| `/projects/:projectId/graphrag-configs` | GraphragConfigListView | Normal | AppShell, 24px padding |
| `/agents/:agentId/mcp` | AgentMcpView | Normal | AppShell, 24px padding |
| `/projects/:projectId/mcp/egress-allowlist` | McpEgressAllowlistView | Normal | AppShell, 24px padding |

### API Endpoints Referenced

| Method | Path | Used By |
|--------|------|---------|
| `GET` | `/api/projects/{pid}/agents` | AgentListView |
| `POST` | `/api/projects/{pid}/agents` | AgentDetailView (create) |
| `GET` | `/api/agents/{aid}` | AgentDetailView |
| `PATCH` | `/api/agents/{aid}` | AgentDetailView (edit) |
| `DELETE` | `/api/agents/{aid}` | AgentListView, AgentDetailView |
| `GET` | `/api/agents/{aid}/mcp` | AgentDetailView (MCP tab), AgentMcpView |
| `POST` | `/api/agents/{aid}/mcp` | AgentMcpView |
| `PATCH` | `/api/agents/{aid}/mcp/{mid}` | AgentMcpView |
| `DELETE` | `/api/agents/{aid}/mcp/{mid}` | AgentMcpView |
| `POST` | `/api/agents/{aid}/mcp/{mid}/test` | AgentDetailView (MCP tab), AgentMcpView |
| `GET` | `/api/projects/{pid}/rag-configs` | RagConfigListView, AgentDetailView (Knowledge tab) |
| `POST` | `/api/projects/{pid}/rag-configs` | RagConfigListView (create modal) |
| `GET` | `/api/rag-configs/{cid}` | RagConfigDetailView |
| `PATCH` | `/api/rag-configs/{cid}` | RagConfigDetailView |
| `DELETE` | `/api/rag-configs/{cid}` | RagConfigListView, RagConfigDetailView |
| `GET` | `/api/rag-configs/{cid}/documents` | RagConfigDetailView |
| `POST` | `/api/rag-configs/{cid}/documents` | RagConfigDetailView |
| `DELETE` | `/api/rag-documents/{did}` | RagConfigDetailView |
| `GET` | `/api/projects/{pid}/graphrag-configs` | GraphragConfigListView, AgentDetailView (Knowledge tab) |
| `POST` | `/api/projects/{pid}/graphrag-configs` | GraphragConfigListView (create modal) |
| `GET` | `/api/graphrag/{cid}/status` | GraphragConfigListView, AgentDetailView (Knowledge tab) |
| `POST` | `/api/graphrag/{cid}/build` | GraphragConfigListView |
| `PATCH` | `/api/graphrag/{cid}` | GraphragConfigListView (edit) |
| `DELETE` | `/api/graphrag/{cid}` | GraphragConfigListView |
| `GET` | `/api/projects/{pid}/mcp/egress-allowlist` | McpEgressAllowlistView |
| `POST` | `/api/projects/{pid}/mcp/egress-allowlist` | McpEgressAllowlistView |
| `DELETE` | `/api/projects/{pid}/mcp/egress-allowlist/{hostname}` | McpEgressAllowlistView |
| `WS` | `/ws/rag-configs/{cid}` | RagConfigDetailView |

### Design System Components Used Across All Views

| Component | Used In |
|-----------|---------|
| `SPageHeader` | All 7 views |
| `SButton` | All 7 views |
| `STable` | AgentList, RagConfigList, RagConfigDetail, GraphragConfigList, AgentMcp, McpEgress |
| `SCard` | AgentDetail, RagConfigDetail, GraphragConfigList, AgentMcp, McpEgress |
| `SFormField` | AgentDetail, RagConfigList (modal), RagConfigDetail, GraphragConfigList (modal), AgentMcp, McpEgress |
| `SBadge` | AgentList, RagConfigList, RagConfigDetail, GraphragConfigList, AgentDetail, AgentMcp |
| `SSelect` | AgentList, AgentDetail, RagConfigList (modal), RagConfigDetail, GraphragConfigList (modal), AgentMcp |
| `SInput` | AgentDetail, RagConfigList (modal), RagConfigDetail, GraphragConfigList (modal), AgentMcp, McpEgress |
| `SModal` | RagConfigList, GraphragConfigList, AgentMcp |
| `SDropdown` | AgentList, GraphragConfigList, AgentMcp |
| `SEmptyState` | All 7 views |
| `SConfirmDialog` | All 7 views (delete actions) |
| `SAlert` | AgentDetail, RagConfigDetail, AgentMcp, McpEgress |
| `SSkeleton` | All 7 views (loading states) |
| `STabs` | AgentDetail (5 tabs), RagConfigDetail (2 tabs) |
| `SToggle` | AgentDetail (A2A, wakeup, workflow), RagConfigDetail (rerank) |
| `SCodeEditor` | AgentDetail (prompt), AgentMcp (config) |
| `SFileUpload` | RagConfigDetail (documents) |
| `SProgressBar` | RagConfigDetail (ingestion) |
| `STextarea` | AgentMcp (allowed tools) |
| `SAccordion` | GraphragConfigList (trigger), AgentMcp (advanced config) |
| `SDrawer` | GraphragConfigList (status) |
| `SRadio` | AgentDetail (context mode) |
| `SSearchInput` | AgentList, RagConfigList |
| `SPagination` | AgentList, RagConfigList |
