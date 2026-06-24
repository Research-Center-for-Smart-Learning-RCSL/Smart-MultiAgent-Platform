# 08 -- Workflow

> Visual DAG editor, run inspector, and orchestration admin views for designing
> and monitoring multi-agent workflows. The editor is the most complex view in
> the platform -- a full-screen graph canvas backed by Vue Flow, real-time
> WebSocket run tracking, and 11 node-type-specific configuration forms.

---

## 1. WorkflowListView

**File**: `src/slices/workflow/views/WorkflowListView.vue`
**Route**: `/workspaces/:workspaceId/workflows` (name: `workflow.list`)
**Layout**: AppShell, sidebar normal, content padding 24px

### 1.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                      |
|   Workflows                                      [+ New Workflow] |
+------------------------------------------------------------------+
|                                                                   |
|  Create Workflow                                                  |
|  Name: [_________________________________] [Create]               |
|                                                                   |
+-------------------------------------------------------------------+
| Name           | Version | Created          | Actions             |
|----------------|---------|------------------|---------------------|
| Daily Report   | 3       | 2026-06-20 09:14 | Runs | Delete       |
| Onboarding     | 7       | 2026-06-18 14:30 | Runs | Delete       |
| Alert Pipeline | 1       | 2026-06-24 08:00 | Runs | Delete       |
+-------------------------------------------------------------------+
|                                                                   |
|  SEmptyState (when no workflows)                                  |
|  "No workflows yet. Create one above."                            |
+-------------------------------------------------------------------+
```

### 1.2 Header

`SPageHeader` with title `$t('workflow.list.title')`. No right-side actions in the header itself -- the create form is inline below.

### 1.3 Create Form

- `SFormField` label `$t('workflow.list.name')` wrapping an `<input>` (class `wf-input`)
- Submit button: `btn btn-primary` with text `$t('workflow.list.create')`
- On submit: `POST /api/workspaces/{wid}/workflows` with `{ name, definition: <empty scaffold> }`
- On success: invalidate query `wfKeys.workflows(workspaceId)`, show toast
- Validation: name required, 1-200 characters

### 1.4 Workflows Table

| Column | Source | Notes |
|--------|--------|-------|
| Name | `workflow.name` | Link to `workflow.editor` route, class `text-accent` |
| Version | `workflow.version` | Plain integer |
| Created | `workflow.created_at` | Formatted via `$d()` (vue-i18n date) |
| Actions | -- | "Runs" link to `workflow.runs`; "Delete" danger button |

**Delete action**: opens `SConfirmDialog` (variant: `warning`), calls `DELETE /api/workflows/{wfid}`. Soft-deletes the workflow.

### 1.5 Empty State

`SEmptyState` with `RectangleGroupIcon` (24/outline), title `$t('workflow.list.empty')`, description `$t('workflow.list.emptyHint')`.

### 1.6 Loading / Error

- Loading: `SLoadingSpinner` centered in content area
- Error: `SAlert` variant `danger` with retry link

### 1.7 Data Fetching

```
useQuery(wfKeys.workflows(workspaceId)) -> listWorkflows(workspaceId)
useMutation -> createWorkflow(workspaceId, { name, definition })
useMutation -> deleteWorkflow(workflowId)
```

Query key: `['workflow', 'list', workspaceId]`

---

## 2. WorkflowEditorView

**File**: `src/slices/workflow/views/WorkflowEditorView.vue`
**Route**: `/workspaces/:workspaceId/workflows/:workflowId/edit` (name: `workflow.editor`)
**Layout**: AppShell, sidebar collapsed, content padding 0

This is the most complex view in the application. The editor fills the full viewport below the top bar and manages its own internal layout. On screens narrower than 1024px, the canvas is read-only with a blue info banner.

### 2.1 Canvas Layout

```
+----------------------------------------------------------------------+
| <- Workflows   Daily Report (*)  [+ Add Node] [U][R] [Val] [Save] [DR]|  Toolbar (40px)
+----------------------------------------------------------------------+
| ! 0 errors, 1 warning                                                |  Lint bar (24px, conditional)
+----------------------------------------------------------------------+
|                                                                 | w  |
|                                                                 | 3  |
|           +--------------+                                      | 2  |
|           |   trigger    |                                      | 0  |
|           |   (manual)   |                                      | p  |
|           +------+-------+                                      | x  |
|                  |                                               |    |
|                  v                                               | C  |
|           +--------------+        +------------------+          | o  |
|           | agent_invoc  |------->| approval_gate    |          | n  |
|           |   GPT-4o     |  fail  |   Team Review    |          | f  |
|           +---+------+---+        +--+-----+----+----+          | i  |
|               |      |              |     |    |               | g  |
|             succ   failure       apprvd rejctd timeout          |    |
|               |                      |                          | P  |
|               v                      v                          | a  |
|           +--------------+     +-----------+                    | n  |
|           |    end       |     | set_var   |                    | e  |
|           |  (success)   |     | status=ok |                    | l  |
|           +--------------+     +-----------+                    |    |
|                                                                 |    |
|   +-------+  +----------+                                      |    |
|   |minimap|  | controls |                                      |    |
|   +-------+  | [+][-][F]|                                      |    |
|              +----------+                                      |    |
+----------------------------------------------------------------------+
```

**Grid structure**:

```css
.workflow-editor {
  display: flex;
  flex-direction: column;
  height: 100%;           /* fills AppShell content area */
}
```

The editor is a vertical flex container with three zones:

| Zone | Height | Content |
|------|--------|---------|
| Toolbar | `auto` (shrink-0) | Breadcrumb, title, dirty badge, action buttons |
| Lint bar | `auto` (conditional) | Validation result summary, visible after first lint run |
| Canvas + Panel | `flex: 1` | Horizontal flex: Vue Flow canvas (flex-1) + config panel (w-80, conditional) |

### 2.2 Node Palette

The node palette is a dropdown triggered by the "+ Add Node" button in the toolbar. It is not a persistent sidebar panel.

**Trigger**: `btn btn-sm` labeled `$t('workflow.palette.addNode')`, toggles `paletteOpen` ref.

**Dropdown**: absolutely positioned below the button, `w-52`, white background, `shadow-lg`, `z-50`, border + rounded corners.

**Groups** (4 sections, matching `NODE_PALETTE_GROUPS` from `constants.ts`):

| Group Label (i18n) | Node Types |
|---------------------|------------|
| `workflow.palette.agents` | `agent_invocation`, `instruct`, `subagent_spawn`, `approval_gate` |
| `workflow.palette.logic` | `condition`, `set_variable`, `parallel`, `join` |
| `workflow.palette.events` | `wait_for_event` |
| `workflow.palette.terminal` | `end` |

The `trigger` node type is excluded from the palette -- every workflow has exactly one trigger node created automatically with the workflow scaffold.

**Each palette item**:
- Full-width block button, `text-sm`, `px-3 py-1.5`
- Hover: `bg-accent/10`
- Text: `$t(NODE_TYPE_LABELS[nodeType])`
- On click: calls `addNode(type)` which creates a node with auto-generated ID `{type}_{counter}`, default config from `NODE_DEFAULTS`, and auto-positioned on canvas

**Group headers**: uppercase, `text-2xs`, `font-semibold`, `text-muted`, `tracking-wide`, `px-3 py-1`.

### 2.3 Node Visual Design

**File**: `src/slices/workflow/components/WorkflowNodeComponent.vue`

Each node on the canvas is a custom Vue Flow node rendered by `WorkflowNodeComponent`. Nodes are registered as type `'workflow-node'` via `markRaw()`.

#### 2.3.1 Node Body

```
+----+--------------------------+
|    | My Agent Task            |  <- label (font-semibold, truncate, max 180px)
|    | agent_invocation         |  <- node type (11px, text-muted)
+----+--------------------------+
      [success]    [failure]       <- port labels (9px, text-muted)
         o            o            <- source handles (8px circles)
```

**Dimensions**: `min-w-[140px]`, `px-3 py-2`, `text-xs`, `rounded`, `border-l-4`.

**Background**: `var(--color-bg)` (white in light theme, dark surface in dark theme).

**Selection ring**: when selected, `ring-2 ring-accent` (2px solid `#2563eb` outline).

**Hover**: `box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-accent) 30%, transparent)`. Transition: `box-shadow 0.15s ease`.

**Cursor**: `pointer`.

#### 2.3.2 Node-Type Colors (left border)

Each node type has a distinct left-border color for instant visual identification:

| Node Type | Border Color | Hex | Tailwind Equivalent |
|-----------|-------------|-----|---------------------|
| `trigger` | Purple | `#c084fc` | purple-400 |
| `agent_invocation` | Blue | `#60a5fa` | blue-400 |
| `approval_gate` | Orange | `#fb923c` | orange-400 |
| `condition` | Amber | `#fbbf24` | amber-400 |
| `instruct` | Cyan | `#22d3ee` | cyan-400 |
| `subagent_spawn` | Emerald | `#34d399` | emerald-400 |
| `wait_for_event` | Rose | `#fb7185` | rose-400 |
| `parallel` | Teal | `#2dd4bf` | teal-400 |
| `join` | Teal | `#2dd4bf` | teal-400 (same as parallel) |
| `set_variable` | Indigo | `#818cf8` | indigo-400 |
| `end` | Grey | `#6b7280` | gray-500 |

Parallel and join share the same teal color to visually communicate that they are a matched pair (fork/merge).

#### 2.3.3 Live Step State Overlays

During a run (when `store.liveSteps[nodeId]` exists), the node background changes to a semi-transparent overlay via `color-mix()`:

| Step State | CSS Class | Background |
|------------|-----------|------------|
| `running` | `wf-node--running` | `color-mix(in srgb, #3b82f6 12%, var(--color-bg))` |
| `succeeded` | `wf-node--succeeded` | `color-mix(in srgb, #22c55e 12%, var(--color-bg))` |
| `failed` | `wf-node--failed` | `color-mix(in srgb, #ef4444 12%, var(--color-bg))` |
| `pending` | (none) | Default background |
| `skipped` | (none) | Default background |
| `cancelled` | (none) | Default background |

The 12% mix ensures the left-border type color remains clearly visible.

#### 2.3.4 Handles (Connection Points)

**Target handle** (input): positioned at `Position.Top`, centered. Present on every node type except `trigger` (which has no inbound connections).

**Source handles** (output): positioned at `Position.Bottom`. Count and names vary by node type:

| Node Type | Source Ports | Notes |
|-----------|-------------|-------|
| `trigger` | `['default']` | Single output |
| `agent_invocation` | `['success', 'failure']` | Two outcomes |
| `instruct` | `['success', 'failure']` | Two outcomes |
| `subagent_spawn` | `['success', 'failure']` | Two outcomes |
| `approval_gate` | `['approved', 'rejected', 'timeout']` | Three outcomes |
| `condition` | Dynamic from `config.branches[].port` + `config.default_port` | Variable count |
| `wait_for_event` | `['default', 'timeout']` | Event received or timed out |
| `join` | `['default', 'timeout']` | All joined or timed out |
| `parallel` | `['default']` | Single fan-out (edges connect to multiple targets) |
| `set_variable` | `['default']` | Single output |
| `end` | `[]` | No output handles |

**Handle positioning for multi-port nodes**: handles are horizontally distributed using the formula `left: ((index + 1) / (total + 1)) * 100%`. For example, with 3 ports: 25%, 50%, 75%.

**Handle styling** (scoped CSS override on `.vue-flow__handle`):
- Size: `8px` x `8px`
- Border: `2px solid var(--color-accent)`
- Background: `var(--color-bg)`
- Shape: `border-radius: 50%` (circle)

**Port labels**: positioned `10px` below the handle, centered via `transform: translateX(-50%)`. Font: `0.5625rem` (9px), `var(--color-muted)`, `white-space: nowrap`, `pointer-events: none`.

### 2.4 Edge Design

Edges are rendered by Vue Flow's default edge renderer with customizations.

#### 2.4.1 Edge Creation Rules

Connections are validated in `useWorkflowEditor.onConnect()`:

| Rule | Description |
|------|-------------|
| No outbound from `end` | `end` nodes have no source handles; connections from end are blocked |
| No inbound to `trigger` | `trigger` nodes have no target handle; connections to trigger are blocked |
| No duplicates | Same `source + sourceHandle + target` combination is rejected |
| Source and target required | Both ends of the connection must be valid node IDs |

**Edge ID format**: `e_{sourceNodeId}_{sourceHandle}_{targetNodeId}`

#### 2.4.2 Edge Labels

Edges display a label when the source handle is not `'default'`. The label text matches the port name (e.g., "success", "failure", "approved", "timeout"). No label is shown for `'default'` port connections.

#### 2.4.3 Visual Style

- **Stroke**: `var(--color-border)` (1.5px)
- **Stroke on hover/selected**: `var(--color-accent)` (2px)
- **Path type**: Vue Flow default (smooth step bezier)
- **Animation**: `animated: false` by default (static lines); during a live run, edges along the active execution path can be animated via future enhancement
- **Arrow**: Vue Flow default marker-end arrowhead

#### 2.4.4 Edge Interaction

- Click an edge to select it (highlights in accent color, sets `selectedEdgeId`)
- Press `Delete` or `Backspace` while an edge is selected to remove it
- Clicking the canvas pane deselects any selected edge

### 2.5 Config Panel

**File**: `src/slices/workflow/components/NodeConfigPanel.vue`

The config panel is an `<aside>` element that appears on the right side of the canvas when a node is selected (desktop only). It is not an `SDrawer` -- it is a fixed-width inline panel within the flex layout.

#### 2.5.1 Panel Layout

```
+----------------------------------+
| Node ID: agent_invocation_3      |  <- read-only, text-xs text-muted
|                                  |
| Label                            |
| [My Agent Task______________]    |  <- SFormField + wf-input
|                                  |
| --- Config Form (dynamic) ---    |
|                                  |
| Agent *                          |
| [GPT-4o Analyst__________ v]    |  <- SFormField + <select>
|                                  |
| Input Template *                 |
| +------------------------------+ |
| | { "query": "{{input}}" }     | |  <- textarea with wf-input-code
| +------------------------------+ |
|                                  |
| Output Variable                  |
| [result____________________]    |
|                                  |
| Timeout (seconds)                |
| [120__]                          |
|                                  |
| > On Error (collapsible)         |
|   Strategy: [fail_________v]     |
|                                  |
| [Delete Node] (danger)           |
+----------------------------------+
```

**Dimensions**: `w-80` (320px), `border-l`, `bg-surface` (`#f8fafc`), `p-4`, `overflow-y-auto`, `shrink-0`.

**Visibility**: only when `selectedNode !== null && isDesktop`.

#### 2.5.2 Panel Header

- **Node ID**: read-only display of `node.id` (e.g., `agent_invocation_3`), `text-xs text-muted`
- **Label field**: `SFormField` with label `$t('workflow.config.label')`, input class `wf-input`

#### 2.5.3 Dynamic Form Dispatch

`NodeConfigPanel` maps each `NodeType` to its config form component via `CONFIG_FORM_MAP`:

```ts
const CONFIG_FORM_MAP: Record<NodeType, Component> = {
  trigger:           TriggerConfigForm,
  agent_invocation:  AgentInvocationConfigForm,
  approval_gate:     ApprovalGateConfigForm,
  condition:         ConditionConfigForm,
  instruct:          InstructConfigForm,
  subagent_spawn:    SubagentSpawnConfigForm,
  wait_for_event:    WaitForEventConfigForm,
  parallel:          ParallelConfigForm,
  join:              JoinConfigForm,
  set_variable:      SetVariableConfigForm,
  end:               EndConfigForm,
}
```

The form is rendered via `<component :is="configComponent">` with standard props:

| Prop | Type | Description |
|------|------|-------------|
| `modelValue` | `Record<string, unknown>` | Current node config |
| `agents` | `Array<{ id: string; name: string }>` | Agents in the project (for select dropdowns) |
| `chatrooms` | `Array<{ id: string; name: string }>` | Chatrooms in the workspace |
| `allNodeIds` | `string[]` | All node IDs (for fallback_node_id selectors) |

All forms emit `update:modelValue` with the full config object on any field change.

#### 2.5.4 Delete Button

- `btn btn-danger`, full width, bottom of panel
- Text: `$t('workflow.config.deleteNode')`
- Disabled for `trigger` node type (cannot delete the entry point)
- Opens `SConfirmDialog` with variant `warning` before deletion
- On confirm: removes node and all connected edges, deselects

#### 2.5.5 Shared Form State Pattern

All config forms use the `useConfigModel(props, emit)` composable:

```ts
const { local, update } = useConfigModel(props, emit)
```

- `local`: deep-cloned reactive copy of `modelValue`
- `update(field, value)`: sets `local[field] = value` and emits the full config object
- `safeNumber(raw, fallback)`: prevents empty-string-to-0 conversion on number inputs

#### 2.5.6 Config Form Specifications

##### TriggerConfigForm

**File**: `components/config/TriggerConfigForm.vue`

| Field | Type | Condition | Constraints |
|-------|------|-----------|-------------|
| `trigger_type` | `<select>` | Always | `manual`, `cron`, `message_received`, `a2a_event`, `wakeup_signal` |
| `allowed_roles[]` | Checkboxes | `trigger_type === 'manual'` | `Admin`, `OrgOwner`, `OrgMember`, `ProjectOwner`, `ProjectMember` |
| `cron_expression` | Text | `trigger_type === 'cron'` | Required, e.g. `0 9 * * MON-FRI` |
| `timezone` | Text | `trigger_type === 'cron'` | e.g. `UTC` |
| `chatroom_id` | `<select>` | `trigger_type === 'message_received'` | From chatrooms list |
| `sender_filter` | `<select>` | `trigger_type === 'message_received'` | `any`, `user`, `agent`, `guest` |
| `content_regex` | Text | `trigger_type === 'message_received'` | Regex pattern |
| `agent_id` | `<select>` | `trigger_type === 'a2a_event'` or `'wakeup_signal'` | From agents list |
| `event_types[]` | Checkboxes | `trigger_type === 'a2a_event'` | `call`, `reply`, `notify`, `instruct` |

##### AgentInvocationConfigForm

**File**: `components/config/AgentInvocationConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `agent_id` | `<select>` (required) | From agents list |
| `input_template` | Textarea (`wf-input-code`) (required) | JSON/expression template |
| `output_variable` | Text | Variable name to store result |
| `target_chatroom_id` | `<select>` | From chatrooms + "default" option |
| `stream_to_chatroom` | Checkbox | Default: `true` |
| `timeout_seconds` | Number | 1-600, default 120, parsed via `safeNumber()` |
| On Error | Nested `OnErrorConfigForm` | Collapsible |

##### ApprovalGateConfigForm

**File**: `components/config/ApprovalGateConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `mode` | `<select>` (required) | `single`, `majority`, `consensus` |
| `leader_agent_id` | `<select>` (required) | From agents list |
| `approvers[]` | Checkboxes | Selected from agents list |
| `timeout_seconds` | Number | 1-86400, default 3600 |
| `question_template` | Textarea (`wf-input-code`) (required) | Approval question |
| On Error | Nested `OnErrorConfigForm` | Collapsible |

##### ConditionConfigForm

**File**: `components/config/ConditionConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `branches[]` | Dynamic array | Min 1 branch enforced |
| `branches[n].when` | Textarea (`wf-input-code`) | Condition expression |
| `branches[n].port` | Text | Output port name |
| `default_port` | Text | Fallback port, default: `'default'` |

Add/remove buttons for branch management. Adding a branch dynamically adds a new source handle to the node.

##### InstructConfigForm

**File**: `components/config/InstructConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `issuer_agent_id` | `<select>` | From agents list |
| `target_agent_id` | `<select>` | From agents list |
| `instruction_template` | Textarea (`wf-input-code`) | Instruction text |
| `wait_for_completion` | Checkbox | Default: `true` |
| `completion_timeout_seconds` | Number | 1-600, shown only when `wait_for_completion === true` |
| `output_variable` | Text | Result storage variable |
| On Error | Nested `OnErrorConfigForm` | Collapsible |

##### SubagentSpawnConfigForm

**File**: `components/config/SubagentSpawnConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `parent_agent_id` | `<select>` | From agents list |
| `task_template` | Textarea (`wf-input-code`) | Task description |
| `max_alive_simultaneously` | Number | 1-20, default 3 |
| `wait_for_all` | Checkbox | Default: `true` |
| `timeout_seconds` | Number | 1-600, default 180 |
| `output_variable` | Text | Result storage variable |
| On Error | Nested `OnErrorConfigForm` | Collapsible |

##### WaitForEventConfigForm

**File**: `components/config/WaitForEventConfigForm.vue`

| Field | Type | Condition | Constraints |
|-------|------|-----------|-------------|
| `event_type` | `<select>` | Always | `message_in_room`, `a2a_message`, `timer`, `variable_matches` |
| `timeout_seconds` | Number | Always | 1-86400, default 300 |
| `chatroom_id` | `<select>` | `event_type === 'message_in_room'` | From chatrooms |
| `sender_filter` | `<select>` | `event_type === 'message_in_room'` | `any`, `user`, `agent`, `guest` |
| `content_regex` | Text | `event_type === 'message_in_room'` | Regex pattern |
| `agent_id` | `<select>` | `event_type === 'a2a_message'` | From agents |
| `types[]` | Checkboxes | `event_type === 'a2a_message'` | `call`, `reply`, `notify`, `instruct` |
| `delay_seconds` | Number | `event_type === 'timer'` | 1-86400, default 60 |
| `expression` | Textarea (`wf-input-code`) | `event_type === 'variable_matches'` | Boolean expression |

##### ParallelConfigForm

**File**: `components/config/ParallelConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `description` | Textarea | Optional, min-height 60px |

Simplest form. The parallel node acts as a fan-out point; its behavior is defined by its outbound edges.

##### JoinConfigForm

**File**: `components/config/JoinConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `mode` | `<select>` | `all`, `any`, `count` |
| `count` | Number | 1-50, shown only when `mode === 'count'` |
| `timeout_seconds` | Number | 1-86400, default 600 |

##### SetVariableConfigForm

**File**: `components/config/SetVariableConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `assignments[]` | Dynamic array | Min 1 assignment enforced |
| `assignments[n].variable` | Text | Variable name |
| `assignments[n].expression` | Textarea (`wf-input-code`) | Value expression |

Add/remove buttons for assignment management.

##### EndConfigForm

**File**: `components/config/EndConfigForm.vue`

| Field | Type | Constraints |
|-------|------|-------------|
| `status` | `<select>` | `success`, `failure` |
| `return_variables` | Text | Comma-separated variable names (converted to array) |
| `failure_reason` | Textarea (`wf-input-code`) | Shown only when `status === 'failure'` |

##### OnErrorConfigForm (nested, reusable)

**File**: `components/config/OnErrorConfigForm.vue`

Embedded inside `AgentInvocationConfigForm`, `ApprovalGateConfigForm`, `InstructConfigForm`, and `SubagentSpawnConfigForm` as a collapsible `<details>` section.

| Field | Type | Condition | Constraints |
|-------|------|-----------|-------------|
| `strategy` | `<select>` (required) | Always | `fail`, `continue`, `retry`, `fallback` |
| `retry_max` | Number | `strategy === 'retry'` | 0-10 |
| `retry_backoff_ms` | Number | `strategy === 'retry'` | 0-60000, step 100 |
| `fallback_node_id` | `<select>` | `strategy === 'fallback'` | From `allNodeIds` + null option |

Switching strategy resets the strategy-specific fields.

### 2.6 Toolbar

The toolbar is a horizontal flex bar at the top of the editor, `px-4 py-2`, `border-b`, `bg-bg`, `shrink-0`.

```
+----------------------------------------------------------------------+
| <- Workflows   Daily Report (*)  |  [+Add] [U] [R]  [Val] [Save] [DR] |
+----------------------------------------------------------------------+
  ^              ^            ^       ^       ^    ^    ^      ^      ^
  |              |            |       |       |    |    |      |      |
  back link      title        dirty   palette undo redo valid  save  dry-run
```

#### 2.6.1 Left Zone

| Element | Component | Behavior |
|---------|-----------|----------|
| Back link | `<router-link>` | Navigates to `workflow.list`, text: `$t('workflow.editor.backToList')`, class `text-sm text-muted hover:underline` |
| Workflow name | `<h2>` | `font-semibold truncate`, max-width 180px (sm: 300px) |
| Dirty indicator | `<span>` | `text-xs text-warning`, text: `$t('workflow.editor.unsaved')`, visible when `store.dirty === true` |

#### 2.6.2 Right Zone (desktop only unless noted)

| Button | Class | Condition | Action |
|--------|-------|-----------|--------|
| + Add Node | `btn btn-sm` | Desktop only | Toggles palette dropdown |
| Undo | `btn btn-sm` | Desktop only; disabled when `!store.canUndo` | Calls `onUndo()` |
| Redo | `btn btn-sm` | Desktop only; disabled when `!store.canRedo` | Calls `onRedo()` |
| Validate | `btn btn-sm` | Always visible | Calls `onValidate()` |
| Save | `btn btn-primary btn-sm` | Desktop only; disabled when `saveMutation.isPending` or `!store.dirty` | Calls `onSave()` |
| Dry Run | `btn btn-sm` | Desktop only; disabled when `dryRunBusy` or `store.dirty` | Calls `onDryRun()` |

**Undo/Redo icons**: Unicode arrows `↶` and `↷`, wrapped in `<span aria-hidden="true">`.

**Undo stack**: max 50 snapshots (implemented via `slice(-49)` in `pushUndo`). Each snapshot is a full `WorkflowDefinition` deep clone. Pushing to undo clears the redo stack.

**Debounced undo for config edits**: config and label changes use a 600ms debounce window (`pushUndoDebounced`). Only one undo snapshot is captured per edit burst -- prevents flooding the undo stack during rapid typing.

**Save behavior**: `PATCH /api/workflows/{wfid}` with `If-Match: {version}` header for optimistic concurrency. On success, updates `currentVersion`, clears dirty flag, invalidates query cache.

**Validate behavior**: `POST /api/workspaces/{wid}/workflows/validate` with current graph definition. Updates `store.lintErrors` and `store.lintWarnings`. Shows toast on clean validation.

**Dry Run behavior**: `POST /api/workflows/{wfid}/dry-run`. On success, navigates to `workflow.run` view for the created run. Disabled when unsaved (must save first). Shows toast on error.

### 2.7 Lint Panel

The lint panel is a thin status bar below the toolbar, visible only after the first validation run (`store.lintRan === true`).

```
+----------------------------------------------------------------------+
| 0 errors, 2 warnings                                                |
+----------------------------------------------------------------------+
```

**Height**: auto (single line, `py-1`), `px-4`, `text-xs`, `border-b`.

**Background color** (conditional):

| Condition | Background | Text |
|-----------|------------|------|
| Errors present | `bg-danger-tint` | `text-danger-on` |
| Warnings only | `bg-warning-tint` | `text-warning-on` |
| Clean (0 errors, 0 warnings) | `bg-success-tint` | `text-success-on` |

**Content format**:
- Clean: `$t('workflow.editor.valid')` (e.g., "Valid")
- Errors only: `{count} {$t('workflow.editor.errors')}`
- Warnings only: `{count} {$t('workflow.editor.warnings')}`
- Both: `{errorCount} {errors} . {warningCount} {warnings}` (middle-dot separator)

**Automatic linting**: `useWorkflowLint` composable runs validation with a 500ms debounce on every node/edge change. It calls the backend validate endpoint and updates the store. Non-fatal on API errors (lint is silently skipped).

**Lint issue structure** (`LintIssue`):

```ts
{
  rule: number        // lint rule ID
  level: 'error' | 'warning'
  message: string     // human-readable description
  node_id?: string    // affected node (optional)
  edge_id?: string    // affected edge (optional)
}
```

### 2.8 Keyboard Shortcuts

| Key | Context | Action |
|-----|---------|--------|
| `Delete` | Node selected | Opens delete confirmation dialog (blocked for trigger nodes) |
| `Backspace` | Node selected | Same as Delete |
| `Delete` | Edge selected | Removes edge immediately |
| `Backspace` | Edge selected | Same as Delete |
| `Ctrl+Z` / `Cmd+Z` | Canvas focused | Undo (via Vue Flow's built-in or toolbar button) |
| `Ctrl+Shift+Z` / `Cmd+Shift+Z` | Canvas focused | Redo |

**Input guard**: keyboard shortcuts are suppressed when the active element is `INPUT`, `TEXTAREA`, or `SELECT` to prevent accidental deletion while editing config forms.

**Canvas keyboard handler**: `@keydown="onCanvasKeydown"` on the Vue Flow component.

### 2.9 Canvas Controls

**Vue Flow built-in controls** (via `@vue-flow/controls`):

| Control | Function |
|---------|----------|
| `+` button | Zoom in |
| `-` button | Zoom out |
| Fit view | Zoom to fit all nodes in viewport |

**Default viewport**: `{ x: 50, y: 50, zoom: 0.85 }` with `fit-view-on-init`.

**Background**: `@vue-flow/background` component renders a subtle dot grid pattern.

**Pan**: click-and-drag on empty canvas area.

**Zoom**: mouse wheel / trackpad pinch.

### 2.10 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 1024px (desktop) | Full editor: palette, undo/redo, save, dry-run, config panel |
| < 1024px (mobile/tablet) | Read-only mode: canvas visible but no editing controls |

**Read-only mode** (< 1024px):
- Info banner: `bg-info-tint text-info-on`, `px-4 py-3`, `text-sm`, `border-b`
- Text: `$t('workflow.editor.readOnlyNotice')`
- Hidden elements: Add Node button, undo/redo, save, dry-run
- Visible: Validate button (for inspection), back link, title
- Config panel: hidden (no node selection on mobile)
- Canvas: still pannable and zoomable for viewing

**Detection**: `useBreakpoint()` composable, `isDesktop` computed property.

### 2.11 Error Banner

When workflow loading fails, a full-width error banner replaces the canvas:

```
+----------------------------------------------------------------------+
| [!] Failed to load workflow.  [Retry]                                |
+----------------------------------------------------------------------+
```

- `role="alert"`, `bg-danger-tint text-danger-on`, `px-4 py-2 text-sm border-b`
- Retry: `<button>` with underline, calls `loadWorkflow()` again

### 2.12 Unsaved Changes Guard

`onBeforeRouteLeave` and `onBeforeRouteUpdate` hooks call `confirmUnsaved()` which opens `SConfirmDialog` (variant: `warning`) if `store.dirty === true`:
- Title: `$t('workflow.editor.unsavedConfirmTitle')`
- Message: `$t('workflow.editor.unsavedConfirm')`
- Confirm label: `$t('workflow.editor.leaveAnyway')`
- Cancel label: `$t('app.cancel')`

### 2.13 State Management

**Pinia store** (`useWorkflowStore`):

| State | Type | Purpose |
|-------|------|---------|
| `dirty` | `ref<boolean>` | Whether graph has unsaved changes |
| `currentVersion` | `ref<number>` | For `If-Match` header on PATCH |
| `lintErrors` | `ref<LintIssue[]>` | Validation errors |
| `lintWarnings` | `ref<LintIssue[]>` | Validation warnings |
| `lintRan` | `ref<boolean>` | Whether lint has run at least once |
| `selectedNodeId` | `ref<string \| null>` | Currently selected node |
| `undoStack` | `shallowRef<WorkflowDefinition[]>` | Undo history (max 50) |
| `redoStack` | `shallowRef<WorkflowDefinition[]>` | Redo history |
| `liveSteps` | `reactive<Record<string, WorkflowStep>>` | Live step states keyed by node_id |
| `runEvents` | `ref<WorkflowRunEvent[]>` | Event log during run |

| Computed | Type | Description |
|----------|------|-------------|
| `canUndo` | `boolean` | `undoStack.length > 0` |
| `canRedo` | `boolean` | `redoStack.length > 0` |
| `hasErrors` | `boolean` | `lintErrors.length > 0` |

Store is registered with `registerCleanup()` for automatic reset on session clear.

### 2.14 Context Data Loading

On mount, the editor loads two sets of reference data for config form dropdowns:

1. **Agents**: fetched via `agentsApi.list(projectId)` -- provides `{ id, name }` pairs for agent selectors
2. **Chatrooms**: fetched via `listChatrooms(workspaceId)` -- provides `{ id, name }` pairs for chatroom selectors

These are loaded via dynamic import from the `agents` and `conversation` slices (cross-slice boundary respected via public API). Loading failure is non-fatal -- config form selectors will be empty.

---

## 3. WorkflowRunsListView

**File**: `src/slices/workflow/views/WorkflowRunsListView.vue`
**Route**: `/workspaces/:workspaceId/workflows/:workflowId/runs` (name: `workflow.runs`)
**Layout**: AppShell, sidebar normal, content padding 24px

### 3.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                      |
|   Runs for "Daily Report"                     [Trigger Manual Run]|
+------------------------------------------------------------------+
|                                                                   |
|  [x] Include archived runs                                       |
|                                                                   |
+-------------------------------------------------------------------+
| State        | Trigger  | Started            | Ended      | --    |
|--------------|----------|--------------------|------------|-------|
| [succeeded]  | manual   | 2026-06-24 09:14   | 09:14:32   | View  |
| [running]    | cron     | 2026-06-24 08:00   | --         | View  |
| [failed]     | manual   | 2026-06-23 14:30   | 14:30:45   | View  |
+-------------------------------------------------------------------+
```

### 3.2 Header

`SPageHeader` with breadcrumb back to `workflow.list`. Title includes the workflow name.

**Manual trigger button**: `btn btn-primary btn-sm`, text `$t('workflow.runs.triggerManual')`. Calls `POST /api/workflows/{wfid}/runs` with empty `trigger_payload`. On success, invalidates runs query.

### 3.3 Archive Toggle

Checkbox: `$t('workflow.runs.includeArchive')`. Toggles `showArchive` ref which modifies the query key, triggering a refetch with `include_archive=true`. The query key is reactive on `showArchive`.

### 3.4 Runs Table

| Column | Source | Component |
|--------|--------|-----------|
| State | `run.state` | `SStatusBadge` with state-to-variant mapping |
| Trigger | `run.trigger_type` | Plain text |
| Started | `run.started_at` | `$d()` formatted |
| Ended | `run.ended_at` | `$d()` formatted, or `--` if null |
| Actions | -- | "View" link to `workflow.run` route, class `text-accent` |

**SStatusBadge variants** for run states:

| State | Variant | Color |
|-------|---------|-------|
| `running` | `info` | Blue |
| `waiting` | `warning` | Amber |
| `succeeded` | `success` | Green |
| `failed` | `danger` | Red |
| `cancelled` | `muted` | Grey |

### 3.5 Empty State

`SEmptyState` with `PlayCircleIcon`, title `$t('workflow.runs.empty')`, description `$t('workflow.runs.emptyHint')`.

### 3.6 Data Fetching

```
useQuery(wfKeys.runs(workflowId), { queryKey: reactive on showArchive })
  -> listRuns(workflowId, { includeArchive })
```

Query parameters: `limit` (default 50), `offset`, `include_archive`.

---

## 4. WorkflowRunView

**File**: `src/slices/workflow/views/WorkflowRunView.vue`
**Route**: `/workflow-runs/:runId` (name: `workflow.run`)
**Layout**: AppShell, sidebar normal, content padding 24px

### 4.1 Wireframe

```
+------------------------------------------------------------------+
| <- Back to Runs                                                  |
|                                                                   |
| Run abc12345              [succeeded]              [*] Connected  |
+------------------------------------------------------------------+
|                                                                   |
| Trigger: manual     Started: 2026-06-24 09:14:00                 |
|                     Ended:   2026-06-24 09:14:32                  |
|                                                                   |
|                                            [Cancel Run] (danger)  |
+------------------------------------------------------------------+
|                                                                   |
| Step Timeline                                                    |
+-------------------------------------------------------------------+
| Node ID            | State        | Started    | Ended   | Error  |
|--------------------|--------------|------------|---------|--------|
| trigger_1          | [succeeded]  | 09:14:00   | 09:14:01| --     |
| agent_invocation_2 | [succeeded]  | 09:14:01   | 09:14:28| --     |
| end_3              | [succeeded]  | 09:14:28   | 09:14:32| --     |
+-------------------------------------------------------------------+
```

### 4.2 Header

- **Back link**: `<router-link>` to `workflow.runs`, class `text-sm text-muted hover:underline`
- **Title**: Run ID (truncated or full UUID)
- **Status badge**: `SStatusBadge` showing `run.state`
- **WebSocket indicator**: green dot (`*`) + "Connected" text when `connected === true` (from `useWorkflowRunSocket`)

### 4.3 Run Details

Two-column layout showing:
- `trigger_type`: plain text
- `started_at`: formatted timestamp
- `ended_at`: formatted timestamp or `--`

### 4.4 Cancel Button

- `btn btn-danger btn-sm`, text: `$t('workflow.run.cancel')`
- Visible only when `run.state === 'running'` or `run.state === 'waiting'`
- Calls `POST /api/workflow-runs/{rid}/cancel`
- On success: invalidates run query, shows toast

### 4.5 Step Timeline Table

| Column | Source | Component |
|--------|--------|-----------|
| Node ID | `step.node_id` | Monospace text |
| State | `step.state` | `SStatusBadge` |
| Started | `step.started_at` | `$d()` formatted |
| Ended | `step.ended_at` | `$d()` or `--` |
| Error | `step.error` | `text-danger`, truncated, or `--` |

**SStatusBadge variants** for step states:

| State | Variant | Color |
|-------|---------|-------|
| `pending` | `muted` | Grey |
| `running` | `info` | Blue |
| `succeeded` | `success` | Green |
| `failed` | `danger` | Red |
| `skipped` | `muted` | Grey |
| `cancelled` | `muted` | Grey |

### 4.6 Real-Time Updates

**WebSocket**: `useWorkflowRunSocket(runId)` connects to `/ws/workflow-runs/{runId}` using JWT subprotocol authentication.

**Events handled**:

| Event | Action |
|-------|--------|
| `workflow.step_started` | Invalidates steps query |
| `workflow.step_finished` | Invalidates steps + run queries |
| `workflow.step_failed` | Invalidates steps + run queries |
| `workflow.run_finished` | Invalidates run query |
| `workflow.run_cancelled` | Invalidates run query |
| `approval.requested` | Invalidates approvals query |
| `approval.resolved` | Invalidates approvals query |

**Reconnection guard**: monotonic `syncGeneration` counter prevents stale data from old connections. On reconnect, `syncOnReconnect()` fetches the full steps snapshot from the API.

**Polling fallback**: `useQuery` with `refetchInterval` that automatically stops polling when the run reaches a terminal state (`succeeded`, `failed`, `cancelled`).

### 4.7 Data Fetching

```
useQuery(wfKeys.run(runId))   -> getRun(runId)       // with smart refetchInterval
useQuery(wfKeys.steps(runId)) -> listSteps(runId)     // with smart refetchInterval
useWorkflowRunSocket(runId)   -> WebSocket live events
useMutation                   -> cancelRun(runId)
```

---

## 5. WorkflowBackstageView

**File**: `src/slices/workflow/views/WorkflowBackstageView.vue`
**Route**: `/workspaces/:workspaceId/workflows/:workflowId/backstage` (name: `workflow.backstage`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Access**: Admin only (`meta: { requiredRoles: ['admin'] }`)

### 5.1 Wireframe

```
+------------------------------------------------------------------+
| Backstage Trace Viewer                                           |
| Internal debugging tool for workflow execution.                  |
+------------------------------------------------------------------+
|                                                                   |
| Run: [abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx  v]                   |
|                                                                   |
+------------------------------------------------------------------+
| Step Trace                                                       |
|                                                                   |
|  | trigger_1        [succeeded]   09:14:00 -> 09:14:01           |
|  | agent_invoc_2    [running]     09:14:01 -> --                 |
|  | approval_3       [failed]      09:14:15 -> 09:14:45           |
|    Error: Approval timeout                                       |
|                                                                   |
+------------------------------------------------------------------+
| Sub-Agent Tree                                                   |
|                                                                   |
|  [done] GPT-4o (abc12345)  "Analyze data..."                    |
|    [running] Claude (def678)  "Summarize findings..."            |
|                                                                   |
+------------------------------------------------------------------+
| Instruction Chain                                                |
|                                                                   |
|  Chain abc12...                                                  |
|  [completed] Agent A -> Agent B  depth:0  2026-06-24 09:14:02   |
|    [delivered] Agent B -> Agent C  depth:1  2026-06-24 09:14:05  |
|                                                                   |
+------------------------------------------------------------------+
| Approvals                                                        |
|                                                                   |
|  [ApprovalCard]  [ApprovalCard]                                  |
|                                                                   |
+------------------------------------------------------------------+
```

### 5.2 Run Selector

A `<select>` dropdown listing the last 100 runs (`limit: 100, includeArchive: true`). Selecting a run populates all sections below.

### 5.3 Step Trace Section

Displays `StepOut[]` for the selected run with state-based left border colors:

| Step State | Border Class |
|------------|-------------|
| `running` | `border-accent` (blue) |
| `succeeded` | `border-success` (green) |
| `failed` | `border-danger` (red) |
| `cancelled` | `border-muted` (grey) |
| Other | `border-border` (default) |

Each step shows: `node_id`, state badge, `started_at -> ended_at`, and `error` (if present, in `text-danger`). Steps use `border-l-2` left accent with `pl-3` indentation.

### 5.4 Sub-Agent Tree

**Component**: `SubagentTree.vue`
**Props**: `parentInstanceId`, `agentNames: Record<string, string>`
**API**: `GET /api/orchestration/instances/{parentInstanceId}/children`

Displays a hierarchical tree of spawned agent instances with:
- State indicator: `[...]` running, `[done]` completed, `[err]` error
- State colors: `text-accent` (running), `text-success` (completed), `text-danger` (error)
- Agent name (mono), instance ID (first 8 chars), task description (truncated)
- Timestamps: `spawned_at`, `destroyed_at`
- Indentation: `ml-4 pl-3 border-l-2 border-border`

### 5.5 Instruction Chain

**Component**: `InstructChainView.vue`
**Props**: `chainId`, `agentNames: Record<string, string>`
**API**: `GET /api/orchestration/chains/{chainId}/instructions`

Displays the instruction chain with depth-based indentation:

| Instruction State | CSS Class |
|-------------------|-----------|
| `completed` | `text-success` |
| `rejected_loop` | `text-danger` |
| `timeout` | `text-warning` |
| `delivered` | `text-accent` |
| `issued` | (default) |

Each instruction shows: state badge, issuer agent -> target agent, `issued_at`, depth level. Chain ID displayed as first 8 characters.

### 5.6 Approvals Section

Renders a list of `ApprovalCard` components, or a muted "No approvals" message if empty.

**Component**: `ApprovalCard.vue`
**Props**: `approval: ApprovalWithVotes`, `agentNames: Record<string, string>`
**API**: `GET /api/orchestration/workflow-runs/{rid}/approvals`

Each card shows:
- `SCard` with `variant="surface"` and `padding="compact"`
- Header: approval state + countdown timer (M:SS format, updates every 1000ms)
- Approval mode badge
- Voter list: agent name, vote (approved/rejected), rationale
- Leader agent badge (`info-tint` background)
- State color mapping: `pending` -> warning, `approved` -> success, `rejected` -> danger, `timeout_leader` -> warning

**Timer**: computed from `started_at + timeout_seconds * 1000`. Ticks via `setInterval(1000)`, cleaned up on unmount/deactivate.

### 5.7 Data Fetching

```
useQuery(wfKeys.runs(workflowId))            -> listRuns(workflowId, { limit: 100, includeArchive: true })
useQuery(wfKeys.steps(selectedRunId))         -> listSteps(selectedRunId)     // enabled when runId selected
useQuery(wfKeys.approvals(selectedRunId))     -> listApprovalsForRun(selectedRunId)
// Child components (SubagentTree, InstructChainView) manage their own queries
```

---

## 6. AgentOrchestrationView

**File**: `src/slices/workflow/views/AgentOrchestrationView.vue`
**Route**: `/agents/:agentId/orchestration` (name: `workflow.agentOrchestration`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Access**: Requires verified email

### 6.1 Wireframe

```
+------------------------------------------------------------------+
| Agent Orchestration                                              |
| Configure wakeup triggers and inspect the dead letter queue.     |
+------------------------------------------------------------------+
|                                                                   |
| Wakeup Configuration                                             |
| +--------------------------------------------------------------+ |
| | [ ] Every N messages          N: [10___]                      | |
| | [ ] Silence (minutes)        Minutes: [5___]                  | |
| |                               Auto-stop rounds: [3___]        | |
| |                               Auto-stop max default: [10__]   | |
| | [ ] Call only                                                 | |
| |                                                               | |
| | Allow self-open: [ ]                                          | |
| | Refresh every (hours): [24__]                                 | |
| +--------------------------------------------------------------+ |
| [Save Configuration]                                             |
|                                                                   |
+------------------------------------------------------------------+
| Dead Letter Queue                                                |
| v DLQ Entries (3)                                [Refresh]       |
| +--------------------------------------------------------------+ |
| | Stream ID     | Attempts | Last Error        | Moved At      | |
| |---------------|----------|-------------------|---------------| |
| | stream_abc123 | 3        | Connection timeout| 2026-06-24... | |
| | stream_def456 | 5        | Parse error       | 2026-06-23... | |
| +--------------------------------------------------------------+ |
+------------------------------------------------------------------+
```

### 6.2 Wakeup Configuration

**Component**: `WakeupConfigEditor.vue`
**Props**: `modelValue: WakeupConfig`, `readonly?: boolean`
**API**: `GET /agents/{agentId}` (returns wakeup config + version), `PATCH /agents/{agentId}` (updates config with `If-Match`)

The editor renders each trigger type as a `<fieldset>` with `<legend>`:

| Trigger | Fields | Range |
|---------|--------|-------|
| `every_n_messages` | `enabled` (checkbox), `n` (number) | n: 1-1000 |
| `silence_minutes` | `enabled` (checkbox), `t_minutes` (number), `autostop_rounds` (number), `autostop_max_default` (number) | t: 1-1440, max_default: 1-100 |
| `call_only` | `enabled` (checkbox) | -- |

Global settings:
- `allow_self_open`: checkbox
- `refresh_every_hours`: number, 1-720

**Inert warning**: when all triggers are disabled, shows a `bg-warning-tint text-warning` warning banner.

**Save button**: `btn btn-primary`, calls `patchAgentWakeupConfig(agentId, config, version)`. Version tracking for optimistic concurrency (`If-Match`).

**Reactive cloning**: uses `JSON.parse(JSON.stringify())` instead of `structuredClone()` to avoid Vue reactive proxy issues.

### 6.3 Dead Letter Queue

**Component**: `DlqViewer.vue`
**Props**: `agentId: string`
**API**: `GET /api/orchestration/agents/{agentId}/dlq` (admin-only)

- Collapsible section with toggle button (rotated arrow icon)
- Entry count badge next to title
- Lazy-loaded: entries are fetched only when section is expanded
- Manual refresh link

**DLQ Table**:

| Column | Source | Notes |
|--------|--------|-------|
| Stream ID | `entry.stream_id` | Monospace |
| Attempts | `entry.attempt_count` | Number |
| Last Error | `entry.last_error` | `text-danger`, truncated |
| Moved At | `entry.moved_at` | Formatted timestamp |

---

## 7. Shared Types Reference

### 7.1 Node Types (11)

```
trigger | agent_invocation | approval_gate | condition | instruct
subagent_spawn | wait_for_event | parallel | join | set_variable | end
```

### 7.2 Trigger Types (5)

```
manual | cron | message_received | a2a_event | wakeup_signal
```

### 7.3 Run States (5)

```
running | waiting | succeeded | failed | cancelled
```

### 7.4 Step States (6)

```
pending | running | succeeded | failed | skipped | cancelled
```

### 7.5 On-Error Strategies (4)

```
fail | continue | retry | fallback
```

### 7.6 Approval Modes (3) and States (4)

```
Modes:  single | majority | consensus
States: pending | approved | rejected | timeout_leader
```

### 7.7 Instruction States (5)

```
issued | delivered | completed | rejected_loop | timeout
```

### 7.8 Core Interfaces

```ts
interface WorkflowNode {
  id: string
  type: NodeType
  label?: string
  config: Record<string, unknown>
  position?: { x: number; y: number }
}

interface WorkflowEdge {
  id: string
  from: string
  to: string
  from_port?: string
  guard?: string | null
}

interface WorkflowDefinition {
  entry_node_id: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  variables?: Record<string, { type: string; default?: unknown }>
  timeouts?: { run_max_seconds?: number; idle_max_seconds?: number }
  loop_guard?: { max_visits_per_node?: number }
}
```

---

## 8. Backend API Reference

### 8.1 Workflow CRUD

| Method | Path | Body / Params | Response |
|--------|------|--------------|----------|
| `GET` | `/api/workspaces/{wid}/workflows` | `limit`, `offset` | `WorkflowOut[]` |
| `POST` | `/api/workspaces/{wid}/workflows` | `{ name, definition }` | `WorkflowOut` |
| `POST` | `/api/workspaces/{wid}/workflows/validate` | `{ definition }` | `{ valid, errors[], warnings[] }` |
| `PATCH` | `/api/workflows/{wfid}` | `{ name?, definition? }` + `If-Match` | `WorkflowOut` |
| `DELETE` | `/api/workflows/{wfid}` | -- | `204` |

### 8.2 Runs

| Method | Path | Body / Params | Response |
|--------|------|--------------|----------|
| `POST` | `/api/workflows/{wfid}/runs` | `{ trigger_payload? }` | `{ run_id }` |
| `POST` | `/api/workflows/{wfid}/dry-run` | `{ trigger_payload? }` | `{ run_id }` |
| `GET` | `/api/workflows/{wfid}/runs` | `limit` (1-200), `offset`, `include_archive` | `RunOut[]` or `ArchivedRunOut[]` |
| `GET` | `/api/workflow-runs/{rid}` | -- | `RunOut` |
| `POST` | `/api/workflow-runs/{rid}/cancel` | -- | `{ status: 'cancelled' }` |
| `GET` | `/api/workflow-runs/{rid}/steps` | -- | `StepOut[]` |

### 8.3 Orchestration

| Method | Path | Body / Params | Response | Access |
|--------|------|--------------|----------|--------|
| `GET` | `/api/orchestration/approvals/{aid}` | -- | Approval + votes | Project member |
| `GET` | `/api/orchestration/workflow-runs/{rid}/approvals` | -- | Approval[] | Project member |
| `GET` | `/api/orchestration/instructions/{iid}` | -- | Instruction | Admin only |
| `GET` | `/api/orchestration/chains/{cid}/instructions` | -- | Instruction[] | Admin only |
| `GET` | `/api/orchestration/instances/{pid}/children` | -- | AgentInstance[] | Project member |
| `GET` | `/api/orchestration/agents/{aid}/dlq` | -- | DlqEntry[] | Admin only |

### 8.4 WebSocket

| Path | Auth | Events |
|------|------|--------|
| `/ws/workflow-runs/{runId}` | JWT subprotocol | `workflow.run_started`, `workflow.step_started`, `workflow.step_finished`, `workflow.step_failed`, `workflow.run_finished`, `workflow.run_cancelled`, `approval.requested`, `approval.resolved` |

---

## 9. Route Summary

| Name | Path | View | Sidebar | Padding | Access |
|------|------|------|---------|---------|--------|
| `workflow.list` | `/workspaces/:wid/workflows` | WorkflowListView | Normal | 24px | Auth |
| `workflow.editor` | `/workspaces/:wid/workflows/:wfid/edit` | WorkflowEditorView | Collapsed | 0 | Auth |
| `workflow.runs` | `/workspaces/:wid/workflows/:wfid/runs` | WorkflowRunsListView | Normal | 24px | Auth |
| `workflow.run` | `/workflow-runs/:rid` | WorkflowRunView | Normal | 24px | Auth |
| `workflow.backstage` | `/workspaces/:wid/workflows/:wfid/backstage` | WorkflowBackstageView | Normal | 24px | Admin |
| `workflow.agentOrchestration` | `/agents/:aid/orchestration` | AgentOrchestrationView | Normal | 24px | Auth + verified email |

---

## 10. Files Summary

### Views

| File | Description |
|------|-------------|
| `src/slices/workflow/views/WorkflowListView.vue` | Workflow list with create/delete |
| `src/slices/workflow/views/WorkflowEditorView.vue` | Visual DAG editor (full-screen canvas) |
| `src/slices/workflow/views/WorkflowRunsListView.vue` | Run list with manual trigger and archive toggle |
| `src/slices/workflow/views/WorkflowRunView.vue` | Single run detail with live WebSocket step trace |
| `src/slices/workflow/views/WorkflowBackstageView.vue` | Admin trace viewer (steps, sub-agents, instructions, approvals) |
| `src/slices/workflow/views/AgentOrchestrationView.vue` | Agent wakeup config editor + DLQ viewer |

### Components

| File | Description |
|------|-------------|
| `src/slices/workflow/components/WorkflowNodeComponent.vue` | Custom Vue Flow node renderer |
| `src/slices/workflow/components/NodeConfigPanel.vue` | Dynamic config form dispatcher for selected node |
| `src/slices/workflow/components/ApprovalCard.vue` | Approval gate display with vote tracking and timer |
| `src/slices/workflow/components/DlqViewer.vue` | Collapsible dead letter queue table |
| `src/slices/workflow/components/InstructChainView.vue` | Instruction chain debugger with depth indentation |
| `src/slices/workflow/components/SubagentTree.vue` | Sub-agent instance hierarchy tree |
| `src/slices/workflow/components/WakeupConfigEditor.vue` | Agent wakeup trigger configuration editor |

### Config Forms

| File | Node Type | Key Fields |
|------|-----------|------------|
| `components/config/TriggerConfigForm.vue` | `trigger` | trigger_type, conditional fields per type |
| `components/config/AgentInvocationConfigForm.vue` | `agent_invocation` | agent_id, input_template, timeout, on-error |
| `components/config/ApprovalGateConfigForm.vue` | `approval_gate` | mode, leader, approvers, question, on-error |
| `components/config/ConditionConfigForm.vue` | `condition` | dynamic branches[], default_port |
| `components/config/InstructConfigForm.vue` | `instruct` | issuer, target, instruction, wait, on-error |
| `components/config/SubagentSpawnConfigForm.vue` | `subagent_spawn` | parent, task, max_alive, on-error |
| `components/config/WaitForEventConfigForm.vue` | `wait_for_event` | event_type, conditional fields per type |
| `components/config/ParallelConfigForm.vue` | `parallel` | description |
| `components/config/JoinConfigForm.vue` | `join` | mode, count, timeout |
| `components/config/SetVariableConfigForm.vue` | `set_variable` | dynamic assignments[] |
| `components/config/EndConfigForm.vue` | `end` | status, return_variables, failure_reason |
| `components/config/OnErrorConfigForm.vue` | (nested) | strategy, retry_max, backoff, fallback_node_id |

### Stores and Composables

| File | Description |
|------|-------------|
| `src/slices/workflow/stores/workflow.ts` | Pinia store: editor state (dirty, undo, lint, selection) + run inspector (live steps) |
| `src/slices/workflow/stores/orchestration.ts` | Re-export of shared orchestration store |
| `src/slices/workflow/composables/useWorkflowEditor.ts` | Graph serialization, node/edge CRUD, undo/redo |
| `src/slices/workflow/composables/useWorkflowRunSocket.ts` | WebSocket connection for live run updates |
| `src/slices/workflow/composables/useWorkflowLint.ts` | Debounced backend validation (500ms) |
| `src/slices/workflow/composables/useConfigModel.ts` | Shared config form state pattern (local + update + safeNumber) |

### Support Files

| File | Description |
|------|-------------|
| `src/slices/workflow/api/index.ts` | HTTP client (workflow CRUD, runs, orchestration, DLQ, wakeup) |
| `src/slices/workflow/queries/index.ts` | TanStack Vue Query key factory (`wfKeys`) |
| `src/slices/workflow/types/index.ts` | TypeScript types mirroring backend DTOs |
| `src/slices/workflow/constants.ts` | Node defaults, type labels (i18n keys), palette groups |
| `src/slices/workflow/utils/wakeup.ts` | Wakeup config validation utilities |
| `src/slices/workflow/routes.ts` | Route definitions with auth/role meta |
| `src/slices/workflow/index.ts` | Public API barrel export (components, composables, stores, types) |
