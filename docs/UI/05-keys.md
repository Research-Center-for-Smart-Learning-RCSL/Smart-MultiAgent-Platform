# 05 — Keys

> API key and search key management: personal key vault, project key
> carrying, key group rotation/failover, search provider keys, and usage
> dashboards. BYO-key model — users bring their own provider API keys;
> plaintext is never returned after upload (R7.03). All screens use the
> AppShell layout with 24px content padding.

---

## 1. KeyListView

**File**: `src/slices/keys/views/KeyListView.vue`
**Route**: `/keys` (name: `keys.list`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Auth**: `requiresAuth: true`, `requiresVerifiedEmail: true`

### 1.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|                                                                   |
| API Keys                                        [ Upload Key ]    |
| Manage your personal API keys for LLM and                         |
| embedding providers.                                              |
+------------------------------------------------------------------+
|                                                                   |
| STable                                                            |
| +--------------------------------------------------------------+ |
| | Provider     | Name        | Preview    | Status   | Actions | |
| |--------------|-------------|------------|----------|---------|  |
| | [chip]       |             |            |          |         |  |
| | Claude       | prod-claude | ****Xk9z   | [valid]  | [...] | |
| |  llm         |             |            |          |         |  |
| |--------------|-------------|------------|----------|---------|  |
| | [chip]       |             |            |          |         |  |
| | OpenAI       | my-gpt4     | ****aB3f   | [error]  | [...] | |
| |  llm embed   |             |            | 401      |         |  |
| |--------------|-------------|------------|----------|---------|  |
| | [chip]       |             |            |          |         |  |
| | Voyage       | voyage-prod | ****mN7q   |[untested]| [...] | |
| |  embed       |             |            |          |         |  |
| +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+
```

### 1.2 Page Header

| Element | Detail |
|---------|--------|
| Title | `$t('keys.list.title')` -- "API Keys" |
| Description | `$t('keys.list.description')` -- "Manage your personal API keys for LLM and embedding providers." |
| Action (slot) | `SButton` variant `primary`, `icon-left: PlusIcon`, label `$t('keys.form.submit')` -- "Upload Key" |
| Action click | Opens the Key Upload Modal (section 7) |

### 1.3 Table Columns

Rendered via `STable` with the following `Column[]` configuration.

| Column Key | Label (`$t`) | Width | Align | Sortable | Cell Content |
|------------|-------------|-------|-------|----------|--------------|
| `provider` | `keys.list.provider` -- "Provider" | 160px | left | No | `CapabilityChip` component: provider name + capability badges |
| `name` | `keys.list.name` -- "Name" | auto | left | No | Plain text, truncated with ellipsis at 40 chars |
| `masked_preview` | `keys.list.preview` -- "Preview" | 120px | left | No | Monospace `<code>` element showing last 4 chars with asterisk prefix: `****Xk9z` |
| `test_status` | `keys.list.status` -- "Status" | 120px | center | No | `SStatusBadge` with test status mapping (section 1.5) |
| `actions` | -- | 80px | right | No | `SDropdown` with action items |

### 1.4 CapabilityChip Rendering

**File**: `src/slices/keys/components/CapabilityChip.vue`

The chip renders the provider name followed by inline capability badges.
Each badge is an `SBadge` (size `sm`, variant `neutral`).

**Provider capabilities**:

| Provider | Display Name | Capabilities | Badge Labels |
|----------|-------------|--------------|--------------|
| `claude` | Claude | `llm_chat` | "llm" |
| `openai` | OpenAI | `llm_chat`, `embedding` | "llm", "embed" |
| `gemini` | Gemini | `llm_chat`, `embedding` | "llm", "embed" |
| `voyage` | Voyage | `embedding` | "embed" |
| `cohere` | Cohere | `rerank` | "rerank" |

**Visual spec**:
- Provider name: 14px, 500 weight, `--color-fg`
- Badges inline after name, gap 4px
- Badge text: abbreviated capability (`llm`, `embed`, `rerank`)
- Badge variant: `neutral`
- Entire chip is inline-flex, vertically centered

### 1.5 Test Status Mapping

`SStatusBadge` maps `ProbeStatus` values to visual treatments.

| `test_status` | Badge Variant | Label (`$t`) | Dot |
|---------------|---------------|-------------|-----|
| `ok` | `success` | "Valid" | Yes |
| `failed` | `danger` | "Invalid" | Yes |
| `untested` | `neutral` | "Untested" | Yes |

When `test_status === 'failed'` and `test_error` is non-null, the error
detail renders below the badge as a `<small>` element in 12px
`--color-muted` text, truncated to 60 chars with tooltip showing the full
message.

### 1.6 Row Actions

Each row's actions column renders an `SDropdown` triggered by an icon-only
`SButton` (variant `ghost`, `EllipsisVerticalIcon`).

| Item Key | Label (`$t`) | Icon | Danger | Action |
|----------|-------------|------|--------|--------|
| `detail` | `keys.list.viewDetail` -- "View Detail" | `EyeIcon` | No | Navigate to `{ name: 'keys.detail', params: { id } }` |
| `retest` | `keys.list.retest` -- "Retest" | `ArrowPathIcon` | No | Call `retest(id)`, show toast on result |
| `delete` | `keys.list.delete` -- "Delete" | `TrashIcon` | Yes | Open `SConfirmDialog` (section 1.7) |

### 1.7 Delete Confirmation

| Property | Value |
|----------|-------|
| Component | `SConfirmDialog` (variant `danger`) |
| Title | `$t('keys.list.deleteTitle')` -- "Delete API Key" |
| Body | `$t('keys.list.deleteBody', { name })` -- "Are you sure you want to delete {name}? This will withdraw the key from all projects." |
| Confirm label | `$t('keys.list.deleteConfirm')` -- "Delete" |
| Cancel label | `$t('keys.list.deleteCancel')` -- "Cancel" |
| On confirm | Call `remove(id)`, on success toast `$t('keys.list.deleted')`, on failure toast error |

### 1.8 Empty State

Displayed inside `STable` via the `empty` slot when `keys.length === 0`
and loading is complete.

```
+------------------------------------------+
|                                          |
|            [KeyIcon]                     |
|             48x48, muted                 |
|                                          |
|      No API keys uploaded yet.           |
|                                          |
|   Upload your first provider API key     |
|   to start using LLM agents.            |
|                                          |
|         [ Upload Key ]                   |
|                                          |
+------------------------------------------+
```

| Property | Value |
|----------|-------|
| Component | `SEmptyState` |
| Icon | `KeyIcon` (24/outline), 48x48px, `--color-muted` |
| Title | `$t('keys.list.emptyTitle')` -- "No API keys uploaded yet." |
| Description | `$t('keys.list.emptyDescription')` -- "Upload your first provider API key to start using LLM agents." |
| Action | `SButton` variant `primary`, label `$t('keys.form.submit')` -- "Upload Key", opens Key Upload Modal |
| Max-width | 400px, centered |

### 1.9 Loading State

While `loading` is true (initial fetch), `STable` renders 5 skeleton rows
via its built-in `loading` prop. Each skeleton row shows:

```
Skeleton row:
+--------------------------------------------------------------+
| [rect 80x24]  | [rect 120x14] | [rect 80x14] | [rect 60x20] |
+--------------------------------------------------------------+
```

### 1.10 Error Handling

| Error | Source | UI Treatment |
|-------|--------|-------------|
| List fetch fails | `useMyKeys` `error` ref | `SAlert` variant `danger` above table: `$t('keys.list.fetchError')` |
| Retest fails | `retest()` catch | Toast error via `useToast`: `$t('keys.list.retestFailed')` |
| Retest succeeds (ok) | `retest()` then | Toast success: `$t('keys.list.retestValid')` |
| Retest succeeds (failed) | `retest()` then | Toast warning: `$t('keys.list.retestInvalid')` with `test_error` |
| Delete fails | `remove()` catch | Toast error: `$t('keys.list.deleteFailed')` |

### 1.11 Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| >= 1024px | Full table layout with all columns |
| 768-1023px | Hide Preview column; tighter horizontal padding |
| < 768px | Switch to card list layout: each key as an `SCard` with stacked fields; provider + name on first line, status badge on second, actions dropdown on third |

**Mobile card layout**:

```
+------------------------------+
| Claude  [llm]                |
| prod-claude                  |
| ****Xk9z                     |
| [valid]           [...]     |
+------------------------------+
```

### 1.12 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Page title + "Upload Key" action button |
| `STable` | Key list with columns, sorting, loading, empty state |
| `SButton` | "Upload Key" primary, row action triggers, empty state CTA |
| `SDropdown` | Row actions menu (View Detail, Retest, Delete) |
| `SStatusBadge` | Test status display (ok/failed/untested) |
| `SBadge` | Capability badges within `CapabilityChip` |
| `SEmptyState` | No-keys placeholder with icon and CTA |
| `SConfirmDialog` | Delete confirmation |
| `SAlert` | Fetch error banner |
| `STooltip` | Full test_error text on hover |
| `SModal` | Key Upload Modal (section 7) |

### 1.13 Accessibility

| Element | Attributes |
|---------|------------|
| Table | Standard `STable` semantics with `<thead>`/`<tbody>` |
| Status badges | `aria-label` includes full status text: "Status: Valid" |
| Dropdown trigger | `aria-label="$t('keys.list.actions')"` -- "Actions" |
| Delete confirm | Focus trap in `SConfirmDialog`, Escape to cancel |
| Masked preview | `aria-label="$t('keys.list.maskedKey')"` -- "Masked API key" |

---

## 2. KeyDetailView

**File**: `src/slices/keys/views/KeyDetailView.vue`
**Route**: `/keys/:id` (name: `keys.detail`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Auth**: `requiresAuth: true`, `requiresVerifiedEmail: true`

### 2.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|                                                                   |
| < API Keys   /   prod-claude           [ Retest ]  [ Delete ]    |
|                                                                   |
+------------------------------------------------------------------+
|                                                                   |
| SCard (elevated)                                                  |
| +--------------------------------------------------------------+ |
| |                                                                | |
| | Provider       Claude                                          | |
| |                [llm]                                           | |
| |                                                                | |
| | Name           prod-claude                                     | |
| |                                                                | |
| | API Key        ****Xk9z                                        | |
| |                                                                | |
| | Status         [valid]                                         | |
| |                                                                | |
| | Last Tested    2026-06-24 14:30 UTC                            | |
| |                                                                | |
| | Created        2026-06-01 09:15 UTC                            | |
| |                                                                | |
| +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+
```

### 2.2 Page Header

| Element | Detail |
|---------|--------|
| Breadcrumbs | `SBreadcrumb` items: [{ label: "API Keys", to: { name: 'keys.list' } }, { label: key.name }] |
| Title | Key name from `key.name` |
| Actions (slot) | Two `SButton` elements: "Retest" (variant `secondary`, icon `ArrowPathIcon`) and "Delete" (variant `danger`, icon `TrashIcon`) |

### 2.3 Detail Fields

Rendered as a definition list (`<dl>`) inside an `SCard` (variant `elevated`,
padding `lg`). Each field pair is a horizontal `<dt>`/`<dd>` row.

| Field | Label (`$t`) | Value Rendering |
|-------|-------------|-----------------|
| Provider | `keys.detail.provider` -- "Provider" | Provider name + `CapabilityChip` |
| Name | `keys.detail.name` -- "Name" | Plain text |
| API Key | `keys.detail.preview` -- "API Key" | Monospace `<code>`: `****{last4}` |
| Status | `keys.detail.status` -- "Status" | `SStatusBadge` + test_error (if failed) |
| Last Tested | `keys.detail.lastTested` -- "Last Tested" | Locale-formatted datetime or `$t('keys.detail.never')` -- "Never" |
| Created | `keys.detail.created` -- "Created" | Locale-formatted datetime |

**Definition list visual spec**:
- `<dt>`: 14px, 500 weight, `--color-muted`, width 140px, flex-shrink 0
- `<dd>`: 14px, 400 weight, `--color-fg`
- Row height: min 40px, vertically centered
- Row gap: 0 (rows separated by 1px `--color-border` bottom border)
- Card padding: 24px

### 2.4 Retest Behavior

| Step | Detail |
|------|--------|
| Click | `SButton` variant `secondary`, icon `ArrowPathIcon`, label `$t('keys.detail.retest')` |
| In-progress | Button shows `loading: true` (spinner replaces icon) |
| Success (ok) | Toast success: `$t('keys.detail.retestValid')`, status badge updates |
| Success (failed) | Toast warning: `$t('keys.detail.retestInvalid')` with test_error |
| Failure | Toast error: `$t('keys.detail.retestFailed')` |

### 2.5 Delete Behavior

| Step | Detail |
|------|--------|
| Click | Opens `SConfirmDialog` variant `danger` |
| Title | `$t('keys.detail.deleteTitle')` -- "Delete API Key" |
| Body | `$t('keys.detail.deleteBody', { name })` -- "Are you sure? This key will be withdrawn from all projects." |
| Confirm | Calls `remove(id)`, on success navigates to `{ name: 'keys.list' }` via `router.replace` |
| Failure | Toast error: `$t('keys.detail.deleteFailed')` |

### 2.6 Not Found State

When no key matches the route `:id` parameter (key not in list or deleted),
display an `SEmptyState`.

```
+------------------------------------------+
|                                          |
|        [ExclamationTriangleIcon]         |
|             48x48, muted                 |
|                                          |
|         Key not found.                   |
|                                          |
|   This key may have been deleted or      |
|   you may not have access.               |
|                                          |
|         [ Back to Keys ]                 |
|                                          |
+------------------------------------------+
```

| Property | Value |
|----------|-------|
| Icon | `ExclamationTriangleIcon`, 48x48, `--color-muted` |
| Title | `$t('keys.detail.notFound')` -- "Key not found." |
| Description | `$t('keys.detail.notFoundDescription')` |
| Action | `SButton` variant `secondary`, navigates to `keys.list` |

### 2.7 Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| >= 768px | Horizontal `<dt>`/`<dd>` layout, actions inline in page header |
| < 768px | Stacked `<dt>` above `<dd>`, actions stack below title as full-width buttons |

### 2.8 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Title with breadcrumbs and action buttons |
| `SBreadcrumb` | Navigation path: API Keys > key name |
| `SCard` | Detail card container (variant `elevated`) |
| `SButton` | Retest (secondary), Delete (danger), Back to Keys (secondary) |
| `SStatusBadge` | Test status display |
| `SConfirmDialog` | Delete confirmation |
| `SEmptyState` | Key not found state |
| `CapabilityChip` | Provider + capabilities |

---

## 3. ProjectKeysView

**File**: `src/slices/keys/views/ProjectKeysView.vue`
**Route**: `/projects/:projectId/keys` (name: `keys.projectKeys`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Auth**: `requiresAuth: true`, `requiresVerifiedEmail: true`

### 3.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|                                                                   |
| Project Keys                                                      |
| Keys carried into this project for agent use.                     |
|                                                                   |
+------------------------------------------------------------------+
|                                                                   |
| STabs  [ Carried Keys ]  [ Carry a Key ]                         |
|                                                                   |
| -- Tab: Carried Keys ------------------------------------        |
|                                                                   |
| STable                                                            |
| +--------------------------------------------------------------+ |
| | Provider   | Name       | Preview   | Status  | Usage |  Act | |
| |------------|------------|-----------|---------|-------|------|  |
| | Claude     | prod-clau  | ****Xk9z  | [valid] |  [v]  |[w/d]|  |
| |  llm       |            |           |         |  1h   |      |  |
| |------------|------------|-----------|---------|-------|------|  |
| | OpenAI     | my-gpt4    | ****aB3f  | [valid] |  [v]  |[w/d]|  |
| |  llm embed |            |           |         |  24h  |      |  |
| +--------------------------------------------------------------+ |
|                                                                   |
| -- Expanded Usage Row (inline) -------------------------         |
| +--------------------------------------------------------------+ |
| |  Requests: 1,248   Input: 3.2M tokens   Output: 890K tokens | |
| |  Errors: 12        Window: 24h                                | |
| +--------------------------------------------------------------+ |
|                                                                   |
| -- Tab: Carry a Key ----------------------------------------    |
|                                                                   |
| Available personal keys not yet carried into this project.        |
|                                                                   |
| +--------------------------------------------------------------+ |
| | Provider   | Name       | Preview   | Status  |   Action     | |
| |------------|------------|-----------|---------|-------------- | |
| | Gemini     | gemini-pro | ****rT5w  | [valid] |  [ Carry ]   | |
| |  llm embed |            |           |         |              | |
| +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+
```

### 3.2 Page Header

| Element | Detail |
|---------|--------|
| Title | `$t('keys.project.title')` -- "Project Keys" |
| Description | `$t('keys.project.description')` -- "Keys carried into this project for agent use." |

### 3.3 Tab Configuration

`STabs` with two tabs:

| Tab Key | Label (`$t`) | Icon | Badge |
|---------|-------------|------|-------|
| `carried` | `keys.project.carried` -- "Carried Keys" | `KeyIcon` | Count of carried keys |
| `available` | `keys.project.carry` -- "Carry a Key" | `PlusCircleIcon` | Count of available keys |

### 3.4 Carried Keys Table

`STable` within the `carried` tab panel.

| Column Key | Label (`$t`) | Width | Cell Content |
|------------|-------------|-------|--------------|
| `provider` | `keys.project.provider` -- "Provider" | 140px | `CapabilityChip` |
| `name` | `keys.project.name` -- "Name" | auto | Plain text |
| `masked_preview` | `keys.project.preview` -- "Preview" | 110px | Monospace `<code>` |
| `test_status` | `keys.project.status` -- "Status" | 100px | `SStatusBadge` |
| `usage` | `keys.project.usage` -- "Usage" | 120px | Usage trigger (section 3.5) |
| `actions` | -- | 100px | Withdraw button |

### 3.5 Usage Inline Expansion

Each carried key row has a usage trigger that expands an inline detail panel.

**Trigger control**:
- `SSelect` (size `sm`, width 80px) for time window: options `1h`, `24h`,
  `7d`, `30d`
- `SButton` (variant `ghost`, size `sm`, icon `ChartBarIcon`) to toggle
  the usage panel

**Expanded panel** (renders as a full-width row below the key row):

```
+--------------------------------------------------------------+
| Usage (24h)                                                   |
|                                                               |
| Requests     Input Tokens     Output Tokens     Errors       |
| 1,248        3,241,560        892,340           12           |
|                                                               |
+--------------------------------------------------------------+
```

| Stat | Label (`$t`) | Format |
|------|-------------|--------|
| `requests` | `keys.project.requests` -- "Requests" | Locale number (e.g., "1,248") |
| `input_tokens` | `keys.project.inputTokens` -- "Input Tokens" | Abbreviated (e.g., "3.2M") |
| `output_tokens` | `keys.project.outputTokens` -- "Output Tokens" | Abbreviated (e.g., "892K") |
| `errors` | `keys.project.errors` -- "Errors" | Locale number |

**Visual spec**:
- Background: `--color-surface`
- Border: 1px `--color-border` top and bottom
- Padding: 16px 24px
- Stats layout: horizontal flex, gap 32px
- Each stat: value (20px, 600 weight, `--color-fg`) over label (12px,
  `--color-muted`)
- Errors stat: value in `--color-danger` when > 0

### 3.6 Withdraw Action

| Property | Value |
|----------|-------|
| Trigger | `SButton` variant `ghost`, size `sm`, icon `ArrowUturnLeftIcon`, label `$t('keys.project.withdraw')` -- "Withdraw" |
| Confirmation | `SConfirmDialog`: "Withdraw this key from the project? Agents using this key will lose access." |
| On confirm | Call `withdraw(projectId, keyId)` |
| On success | Toast success: `$t('keys.project.withdrawn')`, table row removed |
| On failure | Toast error: `$t('keys.project.withdrawFailed')` |

### 3.7 Available Keys Table (Carry Tab)

`STable` showing the user's personal keys that are not yet carried into
this project. Computed by filtering `myKeys` against `carriedKeys`.

| Column Key | Label | Width | Cell Content |
|------------|-------|-------|--------------|
| `provider` | "Provider" | 140px | `CapabilityChip` |
| `name` | "Name" | auto | Plain text |
| `masked_preview` | "Preview" | 110px | Monospace `<code>` |
| `test_status` | "Status" | 100px | `SStatusBadge` |
| `actions` | -- | 100px | Carry button |

**Carry button**: `SButton` variant `primary`, size `sm`, label
`$t('keys.project.carryAction')` -- "Carry". On click calls
`carry(projectId, keyId)`. On success: toast success, key moves from
available table to carried table.

### 3.8 Empty States

**Carried keys empty**:

| Property | Value |
|----------|-------|
| Icon | `InboxIcon` (24/outline), 48x48, `--color-muted` |
| Title | `$t('keys.project.emptyCarried')` -- "No keys carried yet." |
| Description | `$t('keys.project.emptyCarriedDescription')` -- "Carry your personal API keys into this project to make them available for agents." |
| Action | `SButton` "Carry a Key" -- switches to the Carry tab |

**Available keys empty** (all personal keys already carried or user has
no keys):

| Property | Value |
|----------|-------|
| Icon | `CheckCircleIcon` (24/outline), 48x48, `--color-success` |
| Title | `$t('keys.project.emptyAvailable')` -- "All keys carried." |
| Description | `$t('keys.project.emptyAvailableDescription')` -- "All your personal API keys are already in this project. Upload more keys from the API Keys page." |
| Action | `SButton` "Upload Key" -- navigates to `{ name: 'keys.list' }` |

### 3.9 Error Handling

| Error | Source | UI Treatment |
|-------|--------|-------------|
| Carried keys fetch fails | `useProjectKeys` error | `SAlert` variant `danger` above tabs |
| Personal keys fetch fails | `useMyKeys` error | `SAlert` variant `danger` in carry tab |
| Carry fails (409 conflict) | `carry()` catch | Toast error: key already carried |
| Withdraw fails | `withdraw()` catch | Toast error |
| Usage fetch fails | `usage()` catch | Inline error text in expanded panel |

### 3.10 Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| >= 1024px | Full table layout, usage panel horizontal |
| 768-1023px | Hide Preview column; usage stats wrap to 2x2 grid |
| < 768px | Card list layout; each key as `SCard`; usage stats stack vertically; tabs remain horizontal but use full width |

### 3.11 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Page title and description |
| `STabs` | Carried Keys / Carry a Key tabs |
| `STable` | Both key tables (carried and available) |
| `SSelect` | Usage time window selector |
| `SButton` | Carry, Withdraw, usage toggle, Upload Key CTA |
| `SStatusBadge` | Test status |
| `SBadge` | Tab badge counts, capability badges |
| `SEmptyState` | Empty carried and empty available states |
| `SConfirmDialog` | Withdraw confirmation |
| `SAlert` | Error banners |
| `SCard` | Mobile card layout, usage expansion panel |
| `CapabilityChip` | Provider + capabilities |

---

## 4. KeyGroupListView

**File**: `src/slices/keys/views/KeyGroupListView.vue`
**Route**: `/projects/:projectId/key-groups` (name: `keys.groupList`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Auth**: `requiresAuth: true`, `requiresVerifiedEmail: true`

### 4.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|                                                                   |
| Key Groups                                   [ Create Group ]     |
| Organize keys into priority groups for                            |
| rotation and failover.                                            |
|                                                                   |
+------------------------------------------------------------------+
|                                                                   |
| STable                                                            |
| +--------------------------------------------------------------+ |
| | Name               | Members  | Created          | Actions   | |
| |--------------------|----------|------------------|-----------|  |
| | production-llm     | 3 keys   | Jun 1, 2026      | [...]    |  |
| | staging-embedding   | 2 keys   | Jun 10, 2026     | [...]    |  |
| | fallback-rerank    | 1 key    | Jun 15, 2026     | [...]    |  |
| +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+

Create Group Modal:
+------------------------------------+
| Create Key Group            [X]    |
|------------------------------------|
|                                    |
| Group Name                         |
| [_______________________________]  |
|                                    |
|------------------------------------|
|              [ Cancel ] [ Create ] |
+------------------------------------+
```

### 4.2 Page Header

| Element | Detail |
|---------|--------|
| Title | `$t('keys.groups.listTitle')` -- "Key Groups" |
| Description | `$t('keys.groups.listDescription')` -- "Organize keys into priority groups for rotation and failover." |
| Action | `SButton` variant `primary`, icon `PlusIcon`, label `$t('keys.groups.create')` -- "Create Group" |
| Action click | Opens Create Group modal |

### 4.3 Table Columns

| Column Key | Label (`$t`) | Width | Cell Content |
|------------|-------------|-------|--------------|
| `name` | `keys.groups.name` -- "Name" | auto | `<router-link>` to group detail, `--color-accent` text, hover underline |
| `members` | `keys.groups.members` -- "Members" | 120px | `$t('keys.groups.memberCount', { n })` -- "N keys" |
| `created_at` | `keys.groups.created` -- "Created" | 160px | Locale date format |
| `actions` | -- | 80px | `SDropdown` |

### 4.4 Row Actions

| Item Key | Label | Icon | Danger | Action |
|----------|-------|------|--------|--------|
| `view` | "View Group" | `EyeIcon` | No | Navigate to `{ name: 'keys.groupDetail', params: { id } }` |
| `delete` | "Delete Group" | `TrashIcon` | Yes | `SConfirmDialog` |

### 4.5 Create Group Modal

| Property | Value |
|----------|-------|
| Component | `SModal` size `sm` (400px) |
| Title | `$t('keys.groups.createTitle')` -- "Create Key Group" |
| Field | `SFormField` label `$t('keys.groups.nameLabel')` -- "Group Name", required |
| Input | `SInput` placeholder `$t('keys.groups.namePlaceholder')` -- "e.g., production-llm" |
| Validation | Name required, 1-200 chars, validated via zod schema |
| Footer | Cancel (`SButton` secondary) + Create (`SButton` primary, loading state during submit) |
| On submit | Call `create(projectId, name)`, close modal, navigate to new group detail |
| On failure | Inline error below input via `SFormField` error prop |

### 4.6 Delete Confirmation

| Property | Value |
|----------|-------|
| Title | `$t('keys.groups.deleteTitle')` -- "Delete Key Group" |
| Body | `$t('keys.groups.deleteBody', { name })` -- "Delete {name}? Member keys will not be deleted, but agents referencing this group will lose their rotation config." |
| Variant | `danger` |
| On confirm | Call `remove(groupId)`, refresh list |

### 4.7 Empty State

```
+------------------------------------------+
|                                          |
|       [RectangleGroupIcon]               |
|           48x48, muted                   |
|                                          |
|     No key groups created yet.           |
|                                          |
|   Key groups let you configure           |
|   rotation and failover across           |
|   multiple API keys.                     |
|                                          |
|       [ Create Group ]                   |
|                                          |
+------------------------------------------+
```

### 4.8 Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| >= 768px | Full table layout |
| < 768px | Card list: group name as card title, member count and date as secondary lines, actions dropdown top-right |

### 4.9 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Title + Create action |
| `STable` | Group list |
| `SModal` | Create group form |
| `SFormField` | Group name field |
| `SInput` | Group name input |
| `SButton` | Create, Cancel, empty state CTA |
| `SDropdown` | Row actions |
| `SConfirmDialog` | Delete confirmation |
| `SEmptyState` | No groups placeholder |

---

## 5. KeyGroupDetailView

**File**: `src/slices/keys/views/KeyGroupDetailView.vue`
**Route**: `/projects/:projectId/key-groups/:id` (name: `keys.groupDetail`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Auth**: `requiresAuth: true`, `requiresVerifiedEmail: true`

### 5.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|                                                                   |
| < Key Groups  /  production-llm [pencil]     [ Delete Group ]     |
|                                                                   |
+------------------------------------------------------------------+
|                                                                   |
| Members (3)                              [ Add Member v ]         |
|                                                                   |
| Drag-reorderable member list:                                     |
| +--------------------------------------------------------------+ |
| | [=] #1  Claude  prod-claude  ****Xk9z       [expand] [X]    | |
| |                                                                | |
| |     Expanded: Rotation & Limits                                | |
| |     +------------------------------------------------------+  | |
| |     | Rotation                                              |  | |
| |     |   Error codes:  [429] [500] [502] [503]              |  | |
| |     |   Rotate on quota:  [toggle on]                      |  | |
| |     |   Retry on error:   [toggle on]                      |  | |
| |     |   Initial delay:    [500] ms                         |  | |
| |     |   Multiplier:       [2.0]                            |  | |
| |     |   Max delay:        [30000] ms                       |  | |
| |     |   Max retries:      [3]                              |  | |
| |     |   Jitter:           [20] %                           |  | |
| |     |                                                      |  | |
| |     | Hourly Limits                                        |  | |
| |     |   Max input tokens/h:   [__________]                |  | |
| |     |   Max output tokens/h:  [__________]                |  | |
| |     |   Max requests/h:       [__________]                |  | |
| |     |                                                      |  | |
| |     |                               [ Save ]              |  | |
| |     +------------------------------------------------------+  | |
| +--------------------------------------------------------------+ |
|                                                                   |
| +--------------------------------------------------------------+ |
| | [=] #2  OpenAI  my-gpt4  ****aB3f           [expand] [X]    | |
| +--------------------------------------------------------------+ |
|                                                                   |
| +--------------------------------------------------------------+ |
| | [=] #3  Gemini  gemini-pro  ****rT5w         [expand] [X]   | |
| +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+
```

### 5.2 Page Header

| Element | Detail |
|---------|--------|
| Breadcrumbs | [{ label: "Key Groups", to: `keys.groupList` }, { label: group.name }] |
| Title | Group name with inline rename (section 5.3) |
| Actions | `SButton` variant `danger`, icon `TrashIcon`, label `$t('keys.groups.deleteGroup')` -- "Delete Group" |

### 5.3 Inline Rename

The group name in the page header supports inline editing via the
`useInlineRename` composable.

**Interaction flow**:
1. Title text displays with a `PencilIcon` (16px, `--color-muted`) button
   adjacent
2. Click pencil: title text becomes an `SInput` with the current name,
   auto-focused, with a checkmark (`CheckIcon`) and cancel (`XMarkIcon`)
   button
3. Enter or checkmark click: calls `rename(groupId, newName)`, reverts to
   display mode
4. Escape or cancel click: reverts to display mode without saving
5. Validation: 1-200 chars, same zod schema as create

**Visual spec**:
- Display mode: title text (h1 style) + pencil icon, cursor pointer on
  icon
- Edit mode: `SInput` inline, same font size as title, max-width 400px
- Transition: none (instant swap)

### 5.4 Member List

Members are rendered as a vertically-stacked list of draggable cards. Each
card has a priority badge, key info, and expandable configuration.

**Member card anatomy**:

```
+--------------------------------------------------------------+
| [drag-handle]  #N   Provider  key-name  ****last4  [>] [X]  |
+--------------------------------------------------------------+
```

| Element | Detail |
|---------|--------|
| Drag handle | `Bars2Icon` (20/solid), `--color-muted`, cursor `grab` (cursor `grabbing` while dragging) |
| Priority badge | `SBadge` variant `info`, showing `#N` where N is the 1-indexed priority |
| Provider | `CapabilityChip` (compact: provider name + badges) |
| Key name | Plain text, 14px, `--color-fg`, truncated at 30 chars |
| Masked preview | Monospace `<code>`, `--color-muted` |
| Expand toggle | `SButton` ghost, icon-only, `ChevronDownIcon` (rotates to `ChevronUpIcon` when expanded) |
| Remove button | `SButton` ghost, icon-only, `XMarkIcon`, `--color-danger` on hover |

**Card visual spec**:
- Background: `--color-bg` (white)
- Border: 1px `--color-border`
- Border-radius: `--radius-md`
- Padding: 12px 16px
- Margin-bottom: 8px
- Hover: `--shadow-sm`
- Dragging state: `--shadow-md`, opacity 0.9, border `--color-accent`
- Drop target indicator: 2px `--color-accent` top border on the card
  below the cursor

### 5.5 Drag-Reorder Interaction

The member list supports HTML5 native drag-and-drop for reordering
priority.

**Implementation**:
1. `draggable="true"` on each member card
2. Drag starts on the drag handle only (`Bars2Icon`) -- not the entire
   card -- to avoid conflicts with expand/remove interactions
3. `dragstart`: set `dataTransfer` with member index, apply dragging
   visual state
4. `dragover`: compute drop position, show insertion indicator (2px
   `--color-accent` line between cards)
5. `drop`: perform optimistic reorder in local state, call
   `reorder(groupId, { priorities: { [keyId]: newPriority, ... } })` API
6. `dragend`: clear visual states

**Optimistic reorder**:
- Immediately update local member order and priority numbers
- On API success: no action (local state already correct)
- On API failure: revert to previous order, show toast error

**Keyboard reorder (accessibility)**:
- Focus on drag handle
- `Space` or `Enter`: enter reorder mode (visual indicator on card)
- `ArrowUp`/`ArrowDown`: move card up/down one position
- `Space` or `Enter`: confirm new position, call reorder API
- `Escape`: cancel and revert

**Responsive**: On mobile (< 768px), drag handles remain functional but
touch drag may use a long-press (500ms) to initiate, avoiding conflict
with scroll.

### 5.6 Expanded Member Configuration

When a member's expand toggle is clicked, a configuration panel slides
down below the card. The panel has two sections: Rotation and Hourly
Limits.

#### 5.6.1 Rotation Section

| Field | Label (`$t`) | Input Type | Default | Validation |
|-------|-------------|-----------|---------|-----------|
| `rotate_on_error_codes` | `keys.groups.errorCodes` -- "Error codes" | Multi-select chips | `[429, 500, 502, 503]` | Array of integers |
| `rotate_on_token_quota` | `keys.groups.rotateOnQuota` -- "Rotate on quota" | `SToggle` | `true` | Boolean |
| `retry_on_error` | `keys.groups.retryOnError` -- "Retry on error" | `SToggle` | `true` | Boolean |
| `retry_initial_delay_ms` | `keys.groups.initialDelay` -- "Initial delay (ms)" | `SInput` type `number` | `500` | Integer >= 100 |
| `retry_multiplier` | `keys.groups.multiplier` -- "Multiplier" | `SInput` type `number` | `2.0` | Decimal >= 1.0 |
| `retry_max_delay_ms` | `keys.groups.maxDelay` -- "Max delay (ms)" | `SInput` type `number` | `30000` | Integer >= 1000 |
| `retry_max` | `keys.groups.maxRetries` -- "Max retries" | `SInput` type `number` | `3` | Integer 0-10 |
| `retry_jitter_pct` | `keys.groups.jitter` -- "Jitter (%)" | `SInput` type `number` | `20` | Integer 0-100 |

**Error codes multi-select**:
- Renders as horizontal row of `SBadge` elements (variant `neutral`,
  `removable: true`)
- `SInput` (type `number`, size `sm`, width 80px) + `SButton` (ghost,
  `PlusIcon`) to add new codes
- Removing a badge removes the code from the array
- Pre-populated with `[429, 500, 502, 503]`

#### 5.6.2 Hourly Limits Section

| Field | Label (`$t`) | Input Type | Validation | Help Text |
|-------|-------------|-----------|-----------|-----------|
| `max_input_tokens_per_hour` | `keys.groups.maxInputTokens` -- "Max input tokens/h" | `SInput` type `number` | Integer >= 0 or empty (null = unlimited) | "Sliding 60-minute window. Leave empty for unlimited." |
| `max_output_tokens_per_hour` | `keys.groups.maxOutputTokens` -- "Max output tokens/h" | `SInput` type `number` | Same | Same |
| `max_requests_per_hour` | `keys.groups.maxRequests` -- "Max requests/h" | `SInput` type `number` | Same | Same |

**80% threshold indicator**: When a limit is set and current usage
exceeds 80% of the limit, an `SAlert` (variant `warning`, inline) appears
below the field:

```
+--------------------------------------------------------------+
| [!] Input token usage is at 82% of the hourly limit.         |
+--------------------------------------------------------------+
```

#### 5.6.3 Configuration Panel Visual Spec

- Background: `--color-surface`
- Border: 1px `--color-border` on all sides, no top border (merges with
  card above)
- Border-radius: 0 0 `--radius-md` `--radius-md`
- Padding: 20px 24px
- Section headings: 14px, 600 weight, `--color-fg`, 16px margin-bottom
- Field layout: `SFormField` stacked, gap 16px between fields
- Section divider: `SDivider` between Rotation and Hourly Limits
- Save button: `SButton` variant `primary`, size `sm`, right-aligned
- Slide-down animation: `max-height` transition, 200ms ease

#### 5.6.4 Save Behavior

| Step | Detail |
|------|--------|
| Dirty tracking | Compare current form values against original member data |
| Save enabled | Only when form is dirty and valid |
| On save | Call `patchMember(groupId, keyId, patchData)` |
| Success | Toast success: `$t('keys.groups.memberUpdated')`, collapse panel |
| Failure | Toast error with backend error message |

### 5.7 Add Member

**Trigger**: `SSelect` (or `SDropdown`) in the section header area labeled
`$t('keys.groups.addMember')` -- "Add Member".

**Options**: list of carried project keys not already in the group.
Each option shows `provider - key-name`. Keys already in the group are
shown as disabled with "(already added)" suffix.

**On select**: immediately calls `addMember(groupId, keyId)`. New member
appears at the bottom of the list with the lowest priority. The list
re-renders with the new member.

**No available keys**: dropdown shows a disabled placeholder
`$t('keys.groups.noAvailableKeys')` -- "No available keys. Carry keys
into the project first."

### 5.8 Remove Member

| Property | Value |
|----------|-------|
| Trigger | `XMarkIcon` button on member card |
| Confirmation | `SConfirmDialog`: `$t('keys.groups.removeMemberBody', { name })` -- "Remove {name} from this group?" |
| On confirm | Call `removeMember(groupId, keyId)`, card removed with fade-out |
| Re-priority | Remaining members re-number automatically (#1, #2, ...) |

### 5.9 Empty State (No Members)

```
+------------------------------------------+
|                                          |
|         [UsersIcon]                      |
|          48x48, muted                    |
|                                          |
|    No members in this group.             |
|                                          |
|   Add carried project keys to this       |
|   group to enable rotation and           |
|   failover.                              |
|                                          |
|       [ Add Member v ]                   |
|                                          |
+------------------------------------------+
```

### 5.10 Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| >= 1024px | Full member card layout, expanded config in 2-column grid (Rotation left, Limits right) |
| 768-1023px | Single-column config layout, all fields stacked |
| < 768px | Member cards: drag handle + priority on left, key info wraps below, actions on right; config panel full-width stacked; inline rename input full-width |

### 5.11 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Title with breadcrumbs and Delete action |
| `SBreadcrumb` | Key Groups > group name |
| `SBadge` | Priority badges (#1, #2, ...), error code chips |
| `SButton` | Delete Group, expand toggle, remove member, save config, add code |
| `SCard` | Member cards (custom, not `SCard` -- uses `<div>` with card-like styling for drag compatibility) |
| `SInput` | Inline rename, rotation fields, limit fields, error code input |
| `SFormField` | All config fields with labels, help text, and errors |
| `SToggle` | Rotate on quota, retry on error toggles |
| `SDivider` | Between Rotation and Limits sections |
| `SSelect` | Add Member dropdown |
| `SConfirmDialog` | Delete group, remove member confirmations |
| `SEmptyState` | No members placeholder |
| `SAlert` | 80% threshold warning |
| `STooltip` | Rotation field help tooltips |
| `CapabilityChip` | Provider display in member cards |

---

## 6. SearchKeyView

**File**: `src/slices/keys/views/SearchKeyView.vue`
**Route**: `/projects/:projectId/search-keys` (name: `keys.searchKeys`)
**Layout**: AppShell, sidebar normal, content padding 24px
**Auth**: `requiresAuth: true`, `requiresVerifiedEmail: true`

### 6.1 Wireframe

```
+------------------------------------------------------------------+
| SPageHeader                                                       |
|                                                                   |
| Search Keys                              [ Add Search Key ]       |
| Configure search provider keys for                                |
| web search capabilities.                                          |
|                                                                   |
+------------------------------------------------------------------+
|                                                                   |
| STable                                                            |
| +--------------------------------------------------------------+ |
| | Provider    | Preview   | Status   | Active | Actions        | |
| |-------------|-----------|----------|--------|----------------|  |
| | Brave       | ****pQ2r  | [valid]  | (o)    | [...]         |  |
| | Serper      | ****kL8m  | [valid]  | ( )    | [...]         |  |
| | Google CSE  | ****wN4t  | [error]  | ( )    | [...]         |  |
| |   cx: abc123|           | 403      |        |               |  |
| | Tavily      | ****jR6s  |[untested]| ( )    | [...]         |  |
| |   basic     |           |          |        |               |  |
| +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+

Add Search Key Modal:
+----------------------------------------+
| Add Search Key                  [X]    |
|----------------------------------------|
|                                        |
| Provider                               |
| [ Brave                          v ]   |
|                                        |
| API Key                               |
| [____________________________________] |
|                                        |
| -- (if Google CSE) --                  |
| Custom Search Engine ID (CX)          |
| [____________________________________] |
|                                        |
| -- (if Tavily) --                      |
| Search Depth                           |
| [ Basic                          v ]   |
|                                        |
|----------------------------------------|
|                [ Cancel ] [ Upload ]   |
+----------------------------------------+
```

### 6.2 Page Header

| Element | Detail |
|---------|--------|
| Title | `$t('keys.search.title')` -- "Search Keys" |
| Description | `$t('keys.search.description')` -- "Configure search provider keys for web search capabilities." |
| Action | `SButton` variant `primary`, icon `PlusIcon`, label `$t('keys.search.add')` -- "Add Search Key" |
| Action click | Opens Add Search Key modal (section 6.6) |

### 6.3 Table Columns

| Column Key | Label (`$t`) | Width | Cell Content |
|------------|-------------|-------|--------------|
| `provider` | `keys.search.provider` -- "Provider" | 160px | Provider name + config subtitle (section 6.4) |
| `masked_preview` | `keys.search.preview` -- "Preview" | 110px | Monospace `<code>` |
| `test_status` | `keys.search.status` -- "Status" | 120px | `SStatusBadge` + error detail |
| `is_active` | `keys.search.active` -- "Active" | 80px | `SRadio` (section 6.5) |
| `actions` | -- | 80px | `SDropdown` |

### 6.4 Provider-Specific Display

Each provider row may show additional configuration below the provider
name.

| Provider | Display Name | Subtitle | Config |
|----------|-------------|----------|--------|
| `brave` | "Brave" | None | -- |
| `serper` | "Serper" | None | -- |
| `tavily` | "Tavily" | Search depth | Shown as `SBadge` neutral: "basic" or "advanced" |
| `google_cse` | "Google CSE" | CX identifier | Shown as small text: `cx: {value}`, truncated at 20 chars |

**Provider subtitle visual spec**:
- 12px, `--color-muted`
- Indented below provider name, 4px gap
- Tavily depth badge: `SBadge` size `sm`, variant `neutral`

### 6.5 Active Radio Behavior

Each provider type can have exactly one active key per project. The Active
column uses `SRadio` buttons grouped by provider type.

**Rules**:
- Only one key per provider type can be active at a time
- Clicking a radio on an inactive key calls
  `activate(projectId, searchKeyId)`
- The active key's radio is checked; all others of the same provider type
  are unchecked
- Keys with `test_status === 'failed'` show a disabled radio with tooltip:
  `$t('keys.search.cannotActivateInvalid')` -- "Fix validation errors
  before activating."

**Activation flow**:
1. User clicks inactive radio
2. Optimistic: immediately check the new radio, uncheck the old
3. Call `POST /api/projects/{pid}/search-keys/{kid}/activate`
4. On success: no additional action
5. On failure (409 SearchActivationConflict): revert radio state, toast
   error

### 6.6 Add Search Key Modal

| Property | Value |
|----------|-------|
| Component | `SModal` size `md` (560px) |
| Title | `$t('keys.search.addTitle')` -- "Add Search Key" |

**Fields**:

| Field | Label (`$t`) | Component | Validation | Notes |
|-------|-------------|-----------|-----------|-------|
| `provider` | `keys.search.providerLabel` -- "Provider" | `SSelect` | Required | Options: Brave, Serper, Tavily, Google CSE |
| `secret` | `keys.search.secret` -- "API Key" | `SInput` type `password` | Required, 1-4096 chars | `autocomplete="off"` |
| `config.cx` | `keys.search.cx` -- "Custom Search Engine ID (CX)" | `SInput` type `text` | Required when `google_cse` | Visible only when provider is `google_cse` |
| `config.depth` | `keys.search.depth` -- "Search Depth" | `SSelect` | Optional, default `basic` | Visible only when provider is `tavily`; options: "Basic", "Advanced" |

**Provider select options**:

| Value | Label (`$t`) |
|-------|-------------|
| `brave` | `keys.search.brave` -- "Brave" |
| `serper` | `keys.search.serper` -- "Serper" |
| `tavily` | `keys.search.tavily` -- "Tavily" |
| `google_cse` | `keys.search.googleCse` -- "Google CSE" |

**Conditional field rendering**: the `config.cx` and `config.depth` fields
appear/disappear with a CSS transition (opacity + max-height, 200ms)
when the provider selection changes.

**Footer**: Cancel (`SButton` secondary) + Upload (`SButton` primary,
loading state).

**On submit**:
1. Construct payload: `{ provider, secret, config: { cx?, depth? } }`
2. Call `upload(projectId, payload)`
3. On success: close modal, toast success, key appears in table
4. On failure: show inline error via `SFormField` error prop

### 6.7 Row Actions

| Item Key | Label | Icon | Danger | Action |
|----------|-------|------|--------|--------|
| `retest` | `$t('keys.search.retest')` -- "Retest" | `ArrowPathIcon` | No | Call `retest(projectId, searchKeyId)` |
| `delete` | `$t('keys.search.delete')` -- "Delete" | `TrashIcon` | Yes | `SConfirmDialog` |

### 6.8 Delete Confirmation

| Property | Value |
|----------|-------|
| Title | `$t('keys.search.deleteTitle')` -- "Delete Search Key" |
| Body | `$t('keys.search.deleteBody', { provider })` -- "Delete this {provider} search key? Agents using this provider for search will stop working." |
| Warning (when active) | Additional `SAlert` variant `warning` inside the dialog body: "This key is currently active. Deleting it will disable {provider} search for this project." |
| Variant | `danger` |

### 6.9 Empty State

```
+------------------------------------------+
|                                          |
|       [MagnifyingGlassIcon]              |
|           48x48, muted                   |
|                                          |
|     No search keys configured.           |
|                                          |
|   Add search provider keys to enable     |
|   web search capabilities for your       |
|   agents.                                |
|                                          |
|       [ Add Search Key ]                 |
|                                          |
+------------------------------------------+
```

### 6.10 Error Handling

| Error | Source | UI Treatment |
|-------|--------|-------------|
| List fetch fails | `useSearchKeys` error | `SAlert` variant `danger` above table |
| Upload fails (duplicate provider) | `upload()` catch | Inline form error: "A key for this provider already exists." |
| Retest fails | `retest()` catch | Toast error |
| Activate fails (409) | `activate()` catch | Toast error + revert radio state |
| Delete fails | `remove()` catch | Toast error |

### 6.11 Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| >= 768px | Full table layout |
| < 768px | Card list: provider + config on first line, preview + status on second, active radio + actions on third; modal becomes full-width with 16px padding |

### 6.12 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Title and Add action |
| `STable` | Search key list |
| `SModal` | Add Search Key form |
| `SFormField` | All form fields |
| `SInput` | API Key, CX |
| `SSelect` | Provider, Depth |
| `SRadio` | Active key selection |
| `SButton` | Add, Retest (via dropdown), Upload, Cancel |
| `SStatusBadge` | Test status |
| `SBadge` | Tavily depth indicator |
| `SDropdown` | Row actions |
| `SConfirmDialog` | Delete confirmation |
| `SAlert` | Fetch error, active key delete warning |
| `SEmptyState` | No keys placeholder |
| `STooltip` | Disabled radio explanation |

---

## 7. Key Upload Modal (shared)

**File**: `src/slices/keys/components/KeyUploadForm.vue`

The Key Upload Modal is shared between `KeyListView` (via page header
action) and `ProjectKeysView` (via empty state CTA). It handles uploading
a new personal API key.

### 7.1 Wireframe

```
+--------------------------------------------+
| Upload API Key                      [X]    |
|--------------------------------------------|
|                                            |
| Provider                                   |
| [ Select provider...                 v ]   |
|                                            |
|   Selected: OpenAI                         |
|   Capabilities: [llm] [embed]             |
|                                            |
| Name                                       |
| [ e.g., production-gpt4              _ ]   |
|                                            |
| API Key                                   |
| [ Paste your API key here            _ ]   |
|                                            |
| SAlert (info):                             |
| Your key will be envelope-encrypted via    |
| Vault Transit. The plaintext will never    |
| be stored or returned after upload.        |
|                                            |
|--------------------------------------------|
|                 [ Cancel ] [ Upload Key ]   |
+--------------------------------------------+
```

### 7.2 Modal Properties

| Property | Value |
|----------|-------|
| Component | `SModal` size `md` (560px), title `$t('keys.form.title')` -- "Upload API Key" |
| Closable | Yes (X button + Escape) |
| Persistent | No |

### 7.3 Form Fields

| Field | Label (`$t`) | Component | Props | Validation |
|-------|-------------|-----------|-------|-----------|
| `provider` | `keys.form.provider` -- "Provider" | `SSelect` | Options: capability-annotated provider list | Required |
| `name` | `keys.form.name` -- "Name" | `SInput` | placeholder: `keys.form.namePlaceholder` -- "e.g., production-gpt4", maxlength 200 | Required, 1-200 chars |
| `secret` | `keys.form.secret` -- "API Key" | `SInput` type `password` | `autocomplete="new-password"`, maxlength 4096 | Required, 1-4096 chars |

**Provider select options** (with capability annotations):

| Value | Label |
|-------|-------|
| `claude` | "Claude (llm)" |
| `openai` | "OpenAI (llm, embedding)" |
| `gemini` | "Gemini (llm, embedding)" |
| `voyage` | "Voyage (embedding)" |
| `cohere` | "Cohere (rerank)" |

When a provider is selected, its capabilities display below the select
as inline `SBadge` elements (variant `neutral`, size `sm`).

### 7.4 Security Notice

An `SAlert` (variant `info`, not dismissible) is permanently displayed
below the API Key field.

| Property | Value |
|----------|-------|
| Title | `$t('keys.form.securityTitle')` -- "Encrypted storage" |
| Body | `$t('keys.form.securityBody')` -- "Your key will be envelope-encrypted via Vault Transit. The plaintext will never be stored or returned after upload." |
| Icon | `LockClosedIcon` |

### 7.5 Validation Schema

```
z.object({
  provider: z.enum(['claude', 'openai', 'gemini', 'voyage', 'cohere']),
  name: z.string().min(1).max(200),
  secret: z.string().min(1).max(4096),
})
```

Validation is performed via `vee-validate` + `zod` + `toTypedSchema()`.
Errors display inline via `SFormField` error prop.

### 7.6 Submit Behavior

| Step | Detail |
|------|--------|
| Button | `SButton` variant `primary`, label `$t('keys.form.submit')` -- "Upload Key", loading state during API call |
| On submit | Emits `submit({ provider, name, secret })` to parent view |
| Parent handler | Calls `upload(payload)` via `useMyKeys` composable |
| On success | Close modal, reset form, toast success: `$t('keys.form.uploaded')`, reload key list |
| On failure (422) | Show inline error: duplicate name, invalid key format |
| On failure (network) | Toast error: `$t('keys.form.uploadFailed')` |

### 7.7 Post-Upload Auto-Test

After a successful upload, the backend automatically triggers a key
validation test. The new key appears in the list with `test_status:
'untested'` and transitions to `ok` or `failed` within seconds. The list
view refreshes via TanStack Query invalidation to pick up the updated
status.

### 7.8 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SModal` | Form container |
| `SFormField` | Provider, Name, API Key fields |
| `SSelect` | Provider selection |
| `SInput` | Name (text), API Key (password) |
| `SButton` | Upload Key (primary), Cancel (secondary) |
| `SBadge` | Capability annotations |
| `SAlert` | Security notice (info) |

---

## 8. Usage Dashboard (component)

**File**: `src/slices/keys/components/UsageDashboard.vue` (new)

The Usage Dashboard is an embeddable component used within
`ProjectKeysView` (inline expansion) and potentially the
`KeyGroupDetailView` member panels. It visualizes per-key usage metrics
over configurable time windows.

### 8.1 Wireframe

```
+------------------------------------------------------------------+
| Usage Dashboard                                                   |
|                                                                   |
| Time Window:  [1h]  [24h]  [7d]  [30d]                          |
|                                                                   |
| +------+--------------+--------------+-----------+----------+    |
| | Stat | Requests     | Input Tokens | Out Tokens| Errors   |    |
| |------+--------------+--------------+-----------+----------|    |
| | Value| 1,248        | 3,241,560    | 892,340   | 12       |    |
| +------+--------------+--------------+-----------+----------+    |
|                                                                   |
| SProgressBar (hourly limit usage):                                |
|                                                                   |
| Input tokens/h:   [===========>            ] 62%   3.2M / 5M    |
| Output tokens/h:  [==================>     ] 82%   820K / 1M    |
| Requests/h:       [======>                 ] 35%   350 / 1000   |
|                                                                   |
| SAlert (warning, when >= 80%):                                   |
| Output token usage is at 82% of the hourly limit.                |
|                                                                   |
+------------------------------------------------------------------+
```

### 8.2 Props

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `projectId` | `string` | -- | Project UUID |
| `keyId` | `string` | -- | Key UUID |
| `limits` | `HourlyLimits \| null` | `null` | Hourly limits from key group config; null means no limits set |
| `compact` | `boolean` | `false` | Compact layout for inline table expansion |

### 8.3 Time Window Selector

Rendered as a segmented button group (4 `SButton` elements, variant
`ghost` for unselected, variant `secondary` for selected).

| Window | Label | API Parameter |
|--------|-------|---------------|
| 1h | "1h" | `?window=1h` |
| 24h | "24h" | `?window=24h` |
| 7d | "7d" | `?window=7d` |
| 30d | "30d" | `?window=30d` |

Default selection: `1h`.

**Visual spec**:
- Button group: inline-flex, no gap, shared border
- First button: border-radius `--radius-md` 0 0 `--radius-md`
- Last button: border-radius 0 `--radius-md` `--radius-md` 0
- Middle buttons: border-radius 0
- Selected: `--color-accent` bg, white text
- Unselected: `--color-bg` bg, `--color-fg` text

### 8.4 Stat Cards

Four stat cards in horizontal flex layout (wrapping on narrow viewports).

| Stat | Label | Format | Color Condition |
|------|-------|--------|-----------------|
| Requests | "Requests" | Locale number: `1,248` | Default `--color-fg` |
| Input Tokens | "Input Tokens" | Abbreviated: `3.2M`, `892K`, `1,248` | Default `--color-fg` |
| Output Tokens | "Output Tokens" | Abbreviated | Default `--color-fg` |
| Errors | "Errors" | Locale number | `--color-danger` when > 0, else `--color-muted` |

**Number abbreviation rules**:
- < 1,000: locale number (e.g., "892")
- 1,000 - 999,999: `{n/1000:.1f}K` (e.g., "892.3K")
- >= 1,000,000: `{n/1000000:.1f}M` (e.g., "3.2M")

**Stat card visual spec** (compact = false):
- Each card: min-width 140px, flex 1
- Value: 24px, 600 weight
- Label: 12px, `--color-muted`
- Gap between value and label: 4px
- Card: `--color-surface` bg, `--radius-md`, padding 16px

**Compact layout** (compact = true):
- Horizontal flex without cards, gap 32px
- Value: 16px, 600 weight
- Label: 12px, `--color-muted`

### 8.5 Hourly Limit Progress Bars

When `limits` prop is provided and at least one limit is non-null, show
progress bars comparing current usage against limits. Only visible when
the selected time window is `1h` (since limits are hourly).

Each limit renders as:

```
Label:   [=========>              ] 62%    3.2M / 5M
```

| Element | Detail |
|---------|--------|
| Label | Limit field name (e.g., "Input tokens/h") |
| Progress bar | `SProgressBar` |
| Percentage | `Math.round((current / limit) * 100)` |
| Current / Limit | Abbreviated values |

**Progress bar variant mapping**:

| Percentage | Variant | Meaning |
|-----------|---------|---------|
| 0-59% | `info` | Normal usage |
| 60-79% | `warning` | Approaching limit |
| 80-100% | `danger` | Near or at limit |

### 8.6 Threshold Alert

When any limit is >= 80% utilized (during the `1h` window), display an
`SAlert` variant `warning`.

| Property | Value |
|----------|-------|
| Variant | `warning` |
| Dismissible | Yes |
| Title | `$t('keys.usage.thresholdTitle')` -- "Usage threshold warning" |
| Body | `$t('keys.usage.thresholdBody', { metric, pct })` -- "{metric} usage is at {pct}% of the hourly limit." |

This alert corresponds to the backend's `key_usage_threshold`
notification kind (section 10, notifications spec). The in-component
alert provides immediate visual feedback; the notification system
provides persistent, revisitable alerts.

### 8.7 Loading State

While usage data is loading, show a skeleton layout:

```
+------+---------+---------+---------+---------+
| [==] | [=====] | [=====] | [=====] | [=====] |
+------+---------+---------+---------+---------+
```

Four `SSkeleton` variant `rect` elements (140x60px each).

### 8.8 Error State

If the usage API call fails, show an inline `SAlert` variant `danger`
with retry button.

| Property | Value |
|----------|-------|
| Message | `$t('keys.usage.fetchFailed')` -- "Failed to load usage data." |
| Action | `SButton` variant `ghost`, size `sm`, label "Retry", calls `reload()` |

### 8.9 Data Retention Notice

Below the stat cards, a small info line in `--color-muted` 12px text:

`$t('keys.usage.retention')` -- "Usage data retained for 13 months
(raw), then aggregated daily."

This is informational only and not interactive.

### 8.10 Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| >= 1024px | Stat cards in single horizontal row, progress bars full-width |
| 768-1023px | Stat cards wrap to 2x2 grid |
| < 768px | Stat cards stack vertically; progress bars full-width; time window selector scrolls horizontally |

### 8.11 Design System Components Used

| Component | Usage |
|-----------|-------|
| `SButton` | Time window selector buttons, retry |
| `SProgressBar` | Hourly limit usage bars |
| `SAlert` | 80% threshold warning, error state |
| `SSkeleton` | Loading placeholders |
| `SBadge` | (optional) Percentage labels |
| `STooltip` | Hover on abbreviated numbers shows full value |

---

## 9. Files Summary

### 9.1 Existing Files (view/modify)

| File | Status | Changes |
|------|--------|---------|
| `src/slices/keys/views/KeyListView.vue` | Exists | Rewrite with `SPageHeader`, `STable`, `SDropdown`, `SStatusBadge`, `SEmptyState`, `SConfirmDialog`; replace raw `<table>` |
| `src/slices/keys/views/KeyDetailView.vue` | Exists | Add `SPageHeader` with breadcrumbs, `SCard` container, styled `<dl>`; replace raw `<h1>` |
| `src/slices/keys/views/ProjectKeysView.vue` | Exists | Rewrite with `STabs`, dual `STable`; add `UsageDashboard` inline expansion; replace raw `<ul>` |
| `src/slices/keys/views/KeyGroupListView.vue` | Exists | Rewrite with `SPageHeader`, `STable`, create group `SModal`; replace raw `<ul>` |
| `src/slices/keys/views/KeyGroupDetailView.vue` | Exists | Rewrite with `SPageHeader`+`SBreadcrumb`, draggable member cards, `SAccordion`-style expand panels, `SFormField` config forms; replace raw `<details>` and HTML5 drag |
| `src/slices/keys/views/SearchKeyView.vue` | Exists | Rewrite with `SPageHeader`, `STable`, `SRadio` active column, add `SModal`; replace raw `<table>` |
| `src/slices/keys/components/CapabilityChip.vue` | Exists | Migrate inner capability spans to `SBadge` components; keep same prop interface |
| `src/slices/keys/components/KeyUploadForm.vue` | Exists | Wrap in `SModal`; migrate to `SFormField`, `SSelect`, `SInput`; add security `SAlert`; keep zod validation |
| `src/slices/keys/locales/en.json` | Exists | Add new keys: descriptions, empty states, usage labels, threshold alerts, security notice, retention notice |
| `src/slices/keys/locales/zh-TW.json` | Exists | Add corresponding zh-TW translations for all new keys |
| `src/slices/keys/composables/useMyKeys.ts` | Exists | No changes needed |
| `src/slices/keys/composables/useProjectKeys.ts` | Exists | No changes needed |
| `src/slices/keys/composables/useKeyGroups.ts` | Exists | No changes needed |
| `src/slices/keys/composables/useSearchKeys.ts` | Exists | No changes needed |
| `src/slices/keys/api/keys.ts` | Exists | No changes needed |
| `src/slices/keys/api/key-groups.ts` | Exists | No changes needed |
| `src/slices/keys/api/search-keys.ts` | Exists | No changes needed |
| `src/slices/keys/api/project-keys.ts` | Exists | No changes needed |
| `src/slices/keys/queries/index.ts` | Exists | No changes needed |
| `src/slices/keys/routes.ts` | Exists | No changes needed |
| `src/slices/keys/index.ts` | Exists | No changes needed |
| `src/slices/keys/types/index.ts` | Exists | No changes needed |

### 9.2 New Files (create)

| File | Purpose |
|------|---------|
| `src/slices/keys/components/UsageDashboard.vue` | Embeddable usage visualization with time windows, stat cards, progress bars, threshold alerts |
| `src/slices/keys/lib/formatTokenCount.ts` | Pure function for number abbreviation (892 -> "892", 892340 -> "892.3K", 3241560 -> "3.2M") |

### 9.3 Test Files (modify)

| File | Changes |
|------|---------|
| `src/slices/keys/__tests__/KeyListView.test.ts` | Add tests: `STable` rendering, `SDropdown` action interactions, `SEmptyState` display, delete confirmation flow |
| `src/slices/keys/__tests__/KeyDetailView.test.ts` | Add tests: breadcrumb rendering, retest loading state, delete with navigation, not-found state |
| `src/slices/keys/__tests__/ProjectKeysView.test.ts` | Add tests: tab switching, carry/withdraw flows, usage inline expansion, empty states per tab |
| `src/slices/keys/__tests__/KeyGroupListView.test.ts` | Add tests: create modal validation, delete confirmation, table links |
| `src/slices/keys/__tests__/KeyGroupDetailView.test.ts` | Add tests: drag-reorder interaction, inline rename, member config expand/save, add/remove member |
| `src/slices/keys/__tests__/SearchKeyView.test.ts` | Add tests: provider-conditional fields, radio activation with optimistic update, delete active key warning |

### 9.4 Component Dependency Graph

```
KeyListView.vue
  imports: SPageHeader, STable, SDropdown, SButton, SStatusBadge,
           SEmptyState, SConfirmDialog, CapabilityChip, KeyUploadForm,
           useMyKeys, useConfirmDialog

KeyDetailView.vue
  imports: SPageHeader, SBreadcrumb, SCard, SButton, SStatusBadge,
           SConfirmDialog, SEmptyState, CapabilityChip,
           useMyKeys, useConfirmDialog

ProjectKeysView.vue
  imports: SPageHeader, STabs, STable, SButton, SSelect, SStatusBadge,
           SEmptyState, SConfirmDialog, SAlert, SCard, CapabilityChip,
           UsageDashboard, useMyKeys, useProjectKeys, useConfirmDialog

KeyGroupListView.vue
  imports: SPageHeader, STable, SModal, SFormField, SInput, SButton,
           SDropdown, SConfirmDialog, SEmptyState,
           useKeyGroups, useConfirmDialog

KeyGroupDetailView.vue
  imports: SPageHeader, SBreadcrumb, SBadge, SButton, SInput, SToggle,
           SFormField, SSelect, SDivider, SConfirmDialog, SEmptyState,
           SAlert, STooltip, CapabilityChip,
           useKeyGroupDetail, useProjectKeys, useInlineRename,
           useConfirmDialog

SearchKeyView.vue
  imports: SPageHeader, STable, SModal, SFormField, SSelect, SInput,
           SRadio, SButton, SStatusBadge, SBadge, SDropdown,
           SConfirmDialog, SEmptyState, SAlert, STooltip,
           useSearchKeys, useConfirmDialog

KeyUploadForm.vue (modal wrapper)
  imports: SModal, SFormField, SSelect, SInput, SButton, SBadge, SAlert

UsageDashboard.vue (new)
  imports: SButton, SProgressBar, SAlert, SSkeleton, STooltip,
           formatTokenCount, projectKeysApi

CapabilityChip.vue
  imports: SBadge

formatTokenCount.ts (new)
  imports: none (pure function)
```

---

## 10. Domain Rules Reference

| Rule | Code | UI Impact |
|------|------|-----------|
| Plaintext never returned | R7.03 | Preview column shows `****{last4}` only; no "copy key" button |
| 5 LLM providers | -- | Provider select in upload modal limited to: Claude, OpenAI, Gemini, Voyage, Cohere |
| 4 search providers | -- | Search key provider select: Brave, Serper, Tavily, Google CSE |
| Capability matrix | -- | CapabilityChip renders capabilities per provider |
| Test status enum | -- | SStatusBadge maps `ok`/`failed`/`untested` |
| Key groups ordered | -- | Members displayed in priority order with drag-reorder |
| Rotation error codes | -- | Default `[429, 500, 502, 503]`, editable in member config |
| Hourly sliding window | -- | 60-minute sliding window for token/request limits |
| 80% threshold notification | -- | SAlert warning + backend notification via `key_usage_threshold` |
| Usage retention 13 months | -- | Info text below usage stats |
| Search key one-active-per-provider | -- | Radio button enforces single active per provider type |
| Google CSE extra field | -- | CX field conditionally shown in upload modal |
| Vault Transit encryption | -- | Security notice in upload modal |

---

## 11. Error Mapping Reference

Backend domain errors and their UI treatment across all views.

| Domain Error | HTTP | Problem Type | User-Facing Message (`$t`) | UI Treatment |
|---|---|---|---|---|
| KeyNotFound | 404 | `keys/not-found` | "Key not found." | EmptyState (detail), toast (actions) |
| KeyNotOwnedByCaller | 403 | `keys/not-owned` | "You do not own this key." | Toast error |
| KeyRevoked | 410 | `keys/revoked` | "This key has been revoked." | Toast error, row disabled |
| CapabilityMismatch | 422 | `keys/capability-mismatch` | "Key capabilities do not match the required configuration." | Toast error |
| ProviderUnauthorized | 422 | `keys/provider-unauthorized` | "Provider rejected the API key." | Status badge `failed` + test_error |
| KeyGroupExhausted | 503 | `keys/group-exhausted` | "All keys in the group are exhausted." | SAlert in group detail |
| UsageQuotaExceeded | 429 | `keys/usage-quota-exceeded` | "Hourly usage quota exceeded." | SProgressBar at 100% + SAlert danger |
| SearchActivationConflict | 409 | `search/activation-conflict` | "Another key of this provider is already active." | Toast error, revert radio |
| GroupWrongProject | 422 | `keys/not-carried` | "Key is not carried into this project." | Toast error |
| GroupMemberConflict | 409 | `keys/member-conflict` | "Key is already a member of this group." | Toast error, dropdown option disabled |

---

## 12. API Contract Summary

### 12.1 Personal Keys

| Method | Path | Request | Response |
|--------|------|---------|----------|
| GET | `/api/keys` | -- | `KeyOut[]` |
| POST | `/api/keys` | `KeyUploadIn` | `KeyOut` |
| POST | `/api/keys/{id}/retest` | -- | `KeyOut` |
| DELETE | `/api/keys/{id}` | -- | 204 |

### 12.2 Project Keys

| Method | Path | Request | Response |
|--------|------|---------|----------|
| GET | `/api/projects/{pid}/keys` | -- | `KeyOut[]` |
| POST | `/api/projects/{pid}/keys` | `CarryIn` | `KeyOut` |
| DELETE | `/api/projects/{pid}/keys/{kid}` | -- | 204 |
| GET | `/api/projects/{pid}/keys/{kid}/usage` | `?window=1h\|24h\|7d\|30d` | `UsageOut` |

### 12.3 Key Groups

| Method | Path | Request | Response |
|--------|------|---------|----------|
| GET | `/api/projects/{pid}/key-groups` | -- | `GroupOut[]` |
| POST | `/api/projects/{pid}/key-groups` | `GroupIn` | `GroupOut` |
| GET | `/api/key-groups/{gid}` | -- | `GroupDetailOut` |
| PATCH | `/api/key-groups/{gid}` | `GroupPatchIn` | `GroupOut` |
| DELETE | `/api/key-groups/{gid}` | -- | 204 |
| POST | `/api/key-groups/{gid}/keys` | `AddMemberIn` | `MemberOut` |
| PATCH | `/api/key-groups/{gid}/keys/{kid}` | `MemberPatchIn` | `MemberOut` |
| DELETE | `/api/key-groups/{gid}/keys/{kid}` | -- | 204 |
| POST | `/api/key-groups/{gid}/reorder` | `ReorderIn` | `GroupDetailOut` |

### 12.4 Search Keys

| Method | Path | Request | Response |
|--------|------|---------|----------|
| GET | `/api/projects/{pid}/search-keys` | -- | `SearchKeyOut[]` |
| POST | `/api/projects/{pid}/search-keys` | `SearchKeyIn` | `SearchKeyOut` |
| POST | `/api/projects/{pid}/search-keys/{kid}/retest` | -- | `SearchKeyOut` |
| POST | `/api/projects/{pid}/search-keys/{kid}/activate` | -- | `SearchKeyOut` |
| DELETE | `/api/projects/{pid}/search-keys/{kid}` | -- | 204 |

---

## 13. Cross-References

- **[00-overview.md](00-overview.md)**: Phase U3 deliverables (item 1)
- **[01-design-system.md](01-design-system.md)**: All component specs
  referenced in this document
- **[02-layout-shell.md](02-layout-shell.md)**: AppShell layout, sidebar
  nav items (My Keys, Key Groups, Search Keys)
- **[10-notifications.md](10-notifications.md)**: `key_usage_threshold`
  and `key_test_failed` notification kinds
- **[12-shared-patterns.md](12-shared-patterns.md)**: Form validation,
  table patterns, modal patterns, error handling, loading/empty states
- **Backend**: `backend/contexts/keys/` -- domain models, API routes,
  error mapping
