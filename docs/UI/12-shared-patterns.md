# 12 — Shared Patterns

> Cross-cutting UI patterns used across all slices: forms, tables, modals, errors, loading, empty states, optimistic updates, and real-time integration.

---

## 1. Form Patterns

### 1.1 Standard Form Layout

All forms use vee-validate + Zod for validation. Every form field wraps its input in `SFormField`.

**Structure**:

```vue
<form @submit="onSubmit">
  <SFormField :label="$t('...')" :error="errors.name" required html-for="name">
    <SInput id="name" v-model="name" :error="!!errors.name" />
  </SFormField>

  <SFormField :label="$t('...')" :error="errors.email" html-for="email">
    <SInput id="email" v-model="email" type="email" :error="!!errors.email" />
  </SFormField>

  <div class="form-actions">
    <SButton type="submit" variant="primary" :loading="submitting">
      {{ $t('...save') }}
    </SButton>
    <SButton variant="ghost" @click="cancel">
      {{ $t('...cancel') }}
    </SButton>
  </div>
</form>
```

**Validation flow**:
1. **Client-side**: Zod schema validates on blur and submit
2. **Server-side**: RFC 7807 `detail.field_errors` mapped to vee-validate via `useServerErrors()`
3. **Display**: error message appears below the field in `--color-danger`, field border turns danger
4. **Clearing**: field error clears when user modifies the value

### 1.2 Create/Edit Modal Pattern

For creating and editing resources (orgs, projects, agents, keys, etc.):

```
┌─────────────────────────────────────────┐
│ Create Organization              [X]    │
├─────────────────────────────────────────┤
│                                         │
│ Name *                                  │
│ ┌─────────────────────────────────┐     │
│ │                                 │     │
│ └─────────────────────────────────┘     │
│ Organization name must be unique.       │
│                                         │
│ Description                             │
│ ┌─────────────────────────────────┐     │
│ │                                 │     │
│ └─────────────────────────────────┘     │
│                                         │
├─────────────────────────────────────────┤
│                    [Cancel]  [Create]    │
└─────────────────────────────────────────┘
```

- Modal size: `md` (560px) for simple forms, `lg` (720px) for complex
- Title: "Create X" or "Edit X"
- Footer: Cancel (ghost) + Submit (primary)
- Loading: Submit button shows spinner, all inputs disabled
- Success: modal closes, toast "X created successfully", list refreshes
- Error: SAlert at top of modal body for general errors, field errors inline

### 1.3 Inline Edit Pattern

For quick edits (rename org, rename project, rename agent):

```
State 1 (display):  My Organization  [pencil icon]
State 2 (editing):  [My Organization     ] [check] [x]
```

Uses `useInlineRename()` composable:
- Click pencil icon or double-click text -> enters edit mode
- Enter or check icon -> saves
- Escape or X icon -> cancels
- Shows inline error below if server rejects

### 1.4 Multi-Step Form Pattern

For complex creation flows (agent setup, workflow creation):

```
┌─────────────────────────────────────────┐
│ Create Agent                     [X]    │
├─────────────────────────────────────────┤
│  [1. General]  [2. Prompt]  [3. Knowledge]  │
│                                         │
│  Step content here...                   │
│                                         │
├─────────────────────────────────────────┤
│        [Back]              [Next]       │
│                   (or [Create] on last) │
└─────────────────────────────────────────┘
```

- Uses STabs for step indicators
- Each step validates before allowing Next
- Back preserves filled values
- Can jump to any completed step by clicking tab
- Final step shows summary before create

### 1.5 Idempotency

All POST create operations include `Idempotency-Key` header (UUID generated per form submission). The `idempotency.ts` transport module handles this. If user double-clicks submit, only one request is processed.

---

## 2. Table Patterns

### 2.1 Standard Data Table

```
┌─────────────────────────────────────────────────────────┐
│ [Search...                      ]       [+ Create]      │
├─────────────────────────────────────────────────────────┤
│ Name          │ Status     │ Created      │ Actions     │
│───────────────│────────────│──────────────│─────────────│
│ My Org        │ [Active]   │ 2026-01-15   │ [...] menu  │
│ Test Org      │ [Active]   │ 2026-03-22   │ [...] menu  │
│ Old Org       │ [Deleted]  │ 2025-11-01   │ [...] menu  │
├─────────────────────────────────────────────────────────┤
│ Showing 1-10 of 42       [<] 1 2 3 4 5 [>]             │
└─────────────────────────────────────────────────────────┘
```

**Components used**: SPageHeader (title + create button), SSearchInput (filter), STable (data), SPagination.

**Action menu**: SDropdown with items like "View", "Edit", "Delete". Destructive items in `--color-danger`.

### 2.2 Column Types

| Type | Rendering |
|------|-----------|
| Text | Plain text, truncated with ellipsis if > column width |
| Status | SBadge with semantic color (active=success, deleted=danger, pending=warning) |
| Date | Relative ("2 hours ago") with tooltip showing absolute. Uses built-in `Intl.RelativeTimeFormat` |
| User | SAvatar + name |
| Actions | SDropdown with EllipsisVerticalIcon trigger |
| Boolean | SBadge "Yes"/"No" or CheckIcon/XMarkIcon |
| Provider | CapabilityChip or provider icon + name |

### 2.3 Empty Table State

```
┌─────────────────────────────────────────┐
│                                         │
│           [FolderOpenIcon]              │
│                                         │
│       No organizations yet              │
│   Create your first organization        │
│        to get started.                  │
│                                         │
│        [+ Create Organization]          │
│                                         │
└─────────────────────────────────────────┘
```

Uses SEmptyState with icon, title, description, and action button.

### 2.4 Loading State

STable with `loading=true` shows 5 skeleton rows matching column layout. Each cell is an SSkeleton with variant matching column type (text -> text skeleton, badge -> circle + text skeleton).

### 2.5 Sortable Columns

- Click column header to sort ascending
- Click again for descending
- Click again to clear sort
- Active sort: header text in `--color-accent`, arrow icon indicating direction
- Sort state managed via `sortBy` and `sortOrder` props, emits `sort` event
- For server-side sort: update query params, re-fetch

### 2.6 Bulk Actions

When `selectable=true` and rows are selected, a toolbar appears above the table:

```
┌─────────────────────────────────────────┐
│ 3 selected    [Deselect All] [Delete]   │
├─────────────────────────────────────────┤
```

On mobile (< md): bulk actions render as a bottom sheet.

---

## 3. Modal & Dialog Patterns

### 3.1 Confirmation Dialog

For destructive actions (delete, ban, revoke):

```
┌─────────────────────────────────────────┐
│ Delete Organization              [X]    │
├─────────────────────────────────────────┤
│                                         │
│  Are you sure you want to delete        │
│  "My Organization"? This will soft-     │
│  delete the org and all its projects.   │
│  You have 60 days to restore it.        │
│                                         │
├─────────────────────────────────────────┤
│                [Cancel]  [Delete]        │
└─────────────────────────────────────────┘
```

- Uses `useConfirmDialog()` composable (already exists)
- Confirm button: `variant="danger"` for destructive, `variant="primary"` for non-destructive
- Cancel button: `variant="ghost"`
- Persistent modal: cannot close by clicking backdrop
- Escape key still closes (returns false)

### 3.2 Type-to-Confirm Pattern

For high-impact destructive actions (hard delete, force transfer):

```
┌─────────────────────────────────────────┐
│ Hard Delete User                 [X]    │
├─────────────────────────────────────────┤
│                                         │
│  [!] This action is irreversible.       │
│                                         │
│  Type "DELETE" to confirm:              │
│  ┌─────────────────────────────────┐    │
│  │                                 │    │
│  └─────────────────────────────────┘    │
│                                         │
├─────────────────────────────────────────┤
│                [Cancel]  [Hard Delete]   │
└─────────────────────────────────────────┘
```

- Confirm button disabled until confirmation text matches
- SAlert variant="danger" with warning message at top
- Confirm button variant="danger"

### 3.3 Detail Drawer Pattern

For viewing resource details without leaving the list:

```
                              ┌──────────────────┐
                              │ Agent Details [X] │
 [Table continues behind]     ├──────────────────┤
                              │ Name: Summarizer  │
                              │ Model: Claude     │
                              │ Status: [Active]  │
                              │                   │
                              │ System Prompt:    │
                              │ ┌──────────────┐  │
                              │ │ You are...   │  │
                              │ └──────────────┘  │
                              ├──────────────────┤
                              │     [Edit] [Del] │
                              └──────────────────┘
```

- SDrawer from right, size `md` (420px)
- Read-only by default with "Edit" button -> opens edit modal
- "Delete" button with confirmation

---

## 4. Error Handling Patterns

### 4.1 Error Hierarchy

| Level | Component | Trigger | Handling |
|-------|-----------|---------|----------|
| **Global** | ErrorBoundary | Uncaught render error | Retry button + fallback UI |
| **Page** | View-level | API 500/network error | SAlert banner at top of content |
| **Section** | Component-level | Partial data load failure | Inline error with retry |
| **Field** | SFormField | Validation / server error | Inline error text below field |
| **Toast** | Toaster | Transient success/error | Auto-dismiss 4s (error: 6s) |

### 4.2 API Error Mapping

All API errors return RFC 7807 `application/problem+json`. The transport layer (`problem-json.ts`) parses these:

| `type` suffix | UI handling |
|---------------|-------------|
| `auth/token-expired` | Silent refresh, retry original request |
| `auth/invalid-credentials` | Field error on login form |
| `auth/account-locked` | SAlert danger: "Account locked, try in X minutes" |
| `validation-error` | Map `detail.field_errors` to form fields |
| `not-found` | Toast error or redirect to 404 |
| `forbidden` | Toast error: "You don't have permission" |
| `conflict` | Toast warning: "Resource was modified, please refresh" |
| `rate-limited` | Toast warning with `Retry-After` countdown |
| Network error | SAlert danger at page top: "Connection lost. Retrying..." |

### 4.3 Optimistic Concurrency (409 Conflict)

Entities with version fields (agents, workflows, chatrooms, orgs, projects):
1. Client sends `If-Match: <version>` on PATCH
2. Server returns 409 if version mismatch
3. UI: toast warning "This item was modified by someone else. Your changes have been refreshed."
4. Auto-refresh the data (re-fetch via TanStack Query invalidation)
5. User re-applies changes on fresh data

### 4.4 Network Error Recovery

```
┌─────────────────────────────────────────────────────┐
│ [!] Connection lost. Attempting to reconnect...     │
│                                        [Retry Now]  │
└─────────────────────────────────────────────────────┘
```

- SAlert variant="warning" fixed at top of content area
- Auto-retry with exponential backoff (1s, 2s, 4s, 8s, max 30s)
- "Retry Now" button forces immediate retry
- Disappears when connection restored
- For WebSocket disconnection: same pattern, plus "reconnecting" badge on chatroom status

---

## 5. Loading Patterns

### 5.1 Page-Level Loading

First load of any page shows skeleton layout matching the page structure:

```
┌─────────────────────────────────────────┐
│ ████████████████           ████████     │  <- SPageHeader skeleton
├─────────────────────────────────────────┤
│ ████████  │ ████  │ ████████ │ ████    │  <- Table header
│ ░░░░░░░░  │ ░░░░  │ ░░░░░░░░ │ ░░░░    │  <- Skeleton rows
│ ░░░░░░░░  │ ░░░░  │ ░░░░░░░░ │ ░░░░    │
│ ░░░░░░░░  │ ░░░░  │ ░░░░░░░░ │ ░░░░    │
│ ░░░░░░░░  │ ░░░░  │ ░░░░░░░░ │ ░░░░    │
│ ░░░░░░░░  │ ░░░░  │ ░░░░░░░░ │ ░░░░    │
└─────────────────────────────────────────┘
```

Each view provides its own skeleton structure by using SSkeleton components in the same layout grid as the real content. This avoids layout shift when data loads.

### 5.2 Section-Level Loading

When part of a page is loading (e.g., a card refreshing, a tab panel loading):

- SLoadingSpinner centered in the section
- Existing content stays visible but dimmed (50% opacity) if re-fetching
- New sections show skeleton

### 5.3 Button Loading

When a form submits or an action is in progress:

- SButton with `loading=true`: spinner replaces icon-left, text stays visible but dimmed
- All form inputs disabled during submission
- No other loading indicator needed (the button is the indicator)

### 5.4 Inline Loading

For inline actions (retest key, trigger build, delete item):

- Action button shows spinner
- Row/card shows subtle opacity reduction
- On complete: toast success/error, button returns to normal

### 5.5 Progressive Loading

For paginated content (messages, audit logs):

- "Load more" button at the top (messages: load earlier) or bottom (lists: load more)
- Shows SLoadingSpinner when loading
- New items animate in (slide down/up, 200ms)

---

## 6. Empty State Patterns

### 6.1 Standard Empty State

Every list view has a contextual empty state:

| View | Icon | Title | Description | Action |
|------|------|-------|-------------|--------|
| OrgList | BuildingOffice2Icon | No organizations yet | Create your first organization to start collaborating. | Create Organization |
| ProjectList | FolderIcon | No projects yet | Projects organize your agents, keys, and chatrooms. | Create Project |
| KeyList | KeyIcon | No API keys yet | Upload your provider API key to get started. | Upload Key |
| AgentList | CpuChipIcon | No agents yet | Create your first AI agent for this project. | Create Agent |
| ChatroomList | ChatBubbleLeftIcon | No chatrooms yet | Create a chatroom to start conversations with agents. | Create Chatroom |
| WorkflowList | RectangleGroupIcon | No workflows yet | Build visual workflows to orchestrate your agents. | Create Workflow |
| NotificationList | BellSlashIcon | No notifications | You're all caught up. | (none) |
| AdminUsers (filtered) | MagnifyingGlassIcon | No results | No users match your search criteria. | Clear Filters |
| InboxInvites | InboxIcon | No pending invites | When someone invites you, it will appear here. | (none) |

### 6.2 Error Empty State

When a list fails to load:

```
┌─────────────────────────────────────────┐
│                                         │
│        [ExclamationCircleIcon]          │
│                                         │
│        Failed to load data              │
│   Something went wrong. Please try      │
│              again.                     │
│                                         │
│            [Retry]                      │
│                                         │
└─────────────────────────────────────────┘
```

Uses SEmptyState with `ExclamationCircleIcon`, retry action button.

---

## 7. Real-Time Integration Patterns

### 7.1 WebSocket Connection Lifecycle

```
Connected ──── [reconnecting] ──── Connected
     │                                  │
     │ (server close / error)           │
     │                                  │
     └──── Reconnecting ───────────────┘
              │
              │ (3 failures)
              │
              └──── Degraded (polling fallback)
```

- Connection status shown as badge in chatroom header (green dot = live, yellow = reconnecting, red = offline)
- Automatic reconnect with exponential backoff
- After 3 failed reconnects: fall back to polling (10s interval), show banner
- On reconnect: request delta via REST to catch missed events

### 7.2 Optimistic Updates

For immediate feedback on user actions:

| Action | Optimistic behavior | Rollback on error |
|--------|--------------------|--------------------|
| Send message | Message appears immediately in list with "sending" state | Remove message, show error toast |
| Edit message | Text updates immediately | Revert to original text, show error toast |
| Delete message | Message fades out immediately | Message reappears, show error toast |
| Mark notification read | Badge count decrements, item dims | Restore count and item, show error toast |

Implementation via TanStack Query `onMutate` / `onError` / `onSettled` pattern.

### 7.3 Agent Streaming Display

```
┌─────────────────────────────────────────┐
│ [Avatar] Agent Name       ● thinking    │
│                                         │
│ The answer to your question is that     │
│ the primary cause of... █               │
│                                         │
└─────────────────────────────────────────┘
```

- "thinking" indicator: pulsing dot, appears on `agent.thinking` event
- Streaming text: tokens append in real-time via `agent.token` events
- Rendered through markdown pipeline (debounced at 120ms to avoid jitter)
- Blinking cursor (block cursor character) at end of streaming text
- On `agent.finished`: cursor removed, message becomes a normal message in the list
- On `agent.finished{error}` or timeout: error toast, thinking indicator removed

### 7.4 Presence Updates

```
┌────────────────┐
│ Online (3)     │
│ ○ Alice        │
│ ○ Bob          │
│ ● Agent-1 ...  │ <- pulsing dot when thinking
│                │
│ Typing:        │
│ Alice          │
└────────────────┘
```

- Presence list updates on `presence.joined` / `presence.left` events
- Typing indicator: shows user names, disappears after 3s of no typing events
- Agent thinking: pulsing dot animation next to agent name

### 7.5 Real-Time List Updates

For views that show lists affected by external changes (e.g., admin audit log, workflow runs):

- New items: appear at top with slide-in animation (200ms)
- Status changes: badge transitions smoothly (color change, 150ms)
- No auto-scroll: new items appear above viewport; a "N new items" banner appears at top for user to click and scroll up

---

## 8. Navigation Patterns

### 8.1 Breadcrumb Convention

Every page within a hierarchy shows breadcrumbs:

```
Organizations > My Org > Members
Projects > My Project > Agents > Summarizer
Admin > Users > john@example.com
Workspaces > Dev > Chatrooms > general
```

Breadcrumbs are rendered by SPageHeader when `breadcrumbs` prop is provided. Each item is a router-link except the last (current page, plain text).

### 8.2 Context Persistence

The Org/Project context switcher persists selection to `localStorage`:
- Key: `smap:activeOrgId`, `smap:activeProjectId`
- On login: restore from localStorage
- On org change: clear project selection, navigate to project list
- On project change: navigate to first project route (agents list)
- Routes under `/projects/:pid/` read `pid` from URL, not store (URL is authoritative)
- Sidebar project section shows items for the URL project, not the store project

### 8.3 Back Navigation

Views that are "detail" pages (OrgDetail, AgentDetail, KeyGroupDetail) include a back link in the breadcrumb that returns to the parent list. No browser-back dependency.

---

## 9. Toast Notification Patterns

Using vue-sonner via `useToast()` composable:

| Type | Duration | Usage |
|------|----------|-------|
| `success` | 4s | Resource created/updated/deleted successfully |
| `error` | 6s | Action failed (non-field errors) |
| `warning` | 5s | Rate limited, version conflict, degraded state |
| `info` | 4s | Informational (e.g., "Export started") |

**Rules**:
- One toast per action (no stacking of "saving..." + "saved!")
- Error toasts include a brief description (not a stack trace)
- Rate limit toast shows countdown: "Rate limited. Retry in Xs"
- Never use toast for form field errors (use inline errors)
- Never use toast for persistent states (use SAlert banner)

---

## 10. Theme Integration Pattern

All components use CSS custom properties exclusively. No conditional class logic for themes.

**Do**:
```css
.card { background: var(--color-surface); }
.card:hover { background: var(--color-border); }
```

**Do not**:
```vue
<div :class="{ 'bg-white': !isDark, 'bg-gray-800': isDark }">
```

Theme switching is handled by toggling `data-theme` attribute on `<html>`. All CSS custom properties cascade automatically.

For component-specific dark mode adjustments (rare), use:
```css
:root[data-theme="dark"] .component {
  /* override */
}
```

---

## 11. i18n Patterns

### 11.1 String Convention

All user-facing strings go through `$t()` with keys organized by slice:

```
app.title
identity.login.title
identity.login.emailLabel
tenancy.orgs.createConfirm
keys.upload.providerLabel
conversation.chatroom.sendButton
workflow.editor.saveButton
admin.users.banConfirm
notifications.bell.ariaLabel
```

### 11.2 Pluralization

Use vue-i18n built-in plural syntax:

```json
{
  "items": "No items | 1 item | {count} items"
}
```

```vue
{{ $t('items', orgs.length) }}
```

### 11.3 Date/Time Formatting

Use `Intl.DateTimeFormat` and `Intl.RelativeTimeFormat` (locale-aware):
- Absolute: "Jun 24, 2026, 3:45 PM"
- Relative: "2 hours ago", "3 days ago"
- Tooltip: absolute date on hover over relative date
- Always display in user's browser locale

### 11.4 Number Formatting

Use `Intl.NumberFormat`:
- Token counts: "1,234,567"
- Percentages: "80%"
- File sizes: "1.2 MB" (using helper function, not i18n)
