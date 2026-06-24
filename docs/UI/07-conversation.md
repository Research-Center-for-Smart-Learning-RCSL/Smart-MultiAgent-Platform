# 07 — Conversation

> Workspaces, chatrooms, real-time messaging, agent streaming, presence, search, and export.
> The conversation slice is the operational core of SMAP — where users interact with bound AI agents in real time.

---

## 1. WorkspaceListView

**File**: `src/slices/conversation/views/WorkspaceListView.vue`

**Route**: `/projects/:projectId/workspaces` (`conversation.workspaces`)

**Layout**: AppShell, sidebar visible, content padding 24px.

### 1.1 Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  SPageHeader                                                 │
│  Workspaces                              [+ New Workspace]   │
├──────────────────────────────────────────────────────────────┤
│  SSearchInput (filter by name)                               │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────┐  ┌─────────────────────────┐    │
│  │ SCard                   │  │ SCard                   │    │
│  │ [Square3Stack3DIcon]    │  │ [Square3Stack3DIcon]    │    │
│  │ Production Workspace    │  │ Testing Workspace       │    │
│  │ 4 chatrooms             │  │ 2 chatrooms             │    │
│  │ Created 2025-12-01      │  │ Created 2025-12-15      │    │
│  └─────────────────────────┘  └─────────────────────────┘    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 Behavior

| Element | Action | Result |
|---------|--------|--------|
| `+ New Workspace` button | Click | Opens `SModal` with name input (Zod: 1-200 chars) |
| Workspace card | Click | Navigates to `/workspaces/:wid/chatrooms` |
| Workspace card | Context actions | `SDropdown` with "Rename", "Delete" (danger) |
| Delete action | Click | `SConfirmDialog`: "Delete workspace? All chatrooms inside will be removed." |
| Search input | Keystroke | Filters visible cards by name (client-side) |

### 1.3 Visual Spec

- Card grid: `grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`, gap 16px
- Card: `--color-bg` background, `--shadow-sm` elevation, `--radius-md` corners, padding 20px
- Card hover: `--shadow-md` transition (`--transition-fast`)
- Icon: `Square3Stack3DIcon` 24/outline, `--color-muted`, positioned top-left in card
- Workspace name: 16px 600 weight `--color-fg`
- Chatroom count: 14px 400 weight `--color-muted`
- Created date: 12px 400 weight `--color-muted`

### 1.4 States

| State | Display |
|-------|---------|
| Loading | 4 `SSkeleton` cards (height 120px) |
| Empty | `SEmptyState`: `Square3Stack3DIcon` 48px, title "No workspaces yet", description "Create a workspace to organize your chatrooms.", action "Create Workspace" |
| Error | `SAlert` variant danger with retry button |

### 1.5 Responsive

- **>= 1024px**: 2-3 column card grid
- **768-1023px**: 2 column card grid
- **< 768px**: single column, cards full width

---

## 2. ChatroomListView

**File**: `src/slices/conversation/views/ChatroomListView.vue`

**Route**: `/workspaces/:workspaceId/chatrooms` (`conversation.chatrooms`)

**Layout**: AppShell, sidebar visible, content padding 24px.

### 2.1 Wireframe

```
┌──────────────────────────────────────────────────────────────────────┐
│  SBreadcrumb: Workspaces > WorkspaceName                             │
├──────────────────────────────────────────────────────────────────────┤
│  SPageHeader                                                         │
│  WorkspaceName                                     [+ New Chatroom]  │
├──────────────────────────────────────────────────────────────────────┤
│  SSearchInput (filter)                                               │
├──────────────────────────────────────────────────────────────────────┤
│  STable                                                              │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Name             │ Access        │ Agents │ Last Active        │  │
│  ├──────────────────┼───────────────┼────────┼────────────────────┤  │
│  │ #general         │ [Members]     │ 3      │ 2 min ago          │  │
│  │ #dev-agents      │ [Owners Only] │ 1      │ 15 min ago         │  │
│  │ #testing         │ [Guest Link]  │ 2      │ 1 hour ago         │  │
│  └──────────────────┴───────────────┴────────┴────────────────────┘  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Table Columns

| Column | Content | Sort |
|--------|---------|------|
| Name | `ChatBubbleLeftRightIcon` + chatroom name | Alphabetical |
| Access | `SBadge` pills for active access flags | No |
| Agents | Count of bound agents | Numeric |
| Last Active | Relative time of latest message | Date desc (default) |
| Actions | `SDropdown`: "Settings", "Delete" (danger) | No |

### 2.3 Access Badges

| Flag | Badge Label | Badge Variant |
|------|-------------|---------------|
| `allow_org_members` | Org Members | info |
| `allow_project_members` | Members | info |
| `allow_project_owners_only` | Owners Only | warning |
| `allow_guest_links` | Guest Link | neutral |

### 2.4 Behavior

| Element | Action | Result |
|---------|--------|--------|
| `+ New Chatroom` button | Click | `SModal` with name + access flags form |
| Row click | Click | Navigates to `/chatrooms/:cid` |
| Settings action | Click | Navigates to `/chatrooms/:cid/settings` |
| Delete action | Click | `SConfirmDialog`: "Delete chatroom? All messages will be permanently removed." |
| Breadcrumb "Workspaces" | Click | Navigates back to workspace list |

### 2.5 Create Chatroom Modal

**Form fields** (Zod schema: `chatroomCreateSchema`):

| Field | Component | Validation |
|-------|-----------|------------|
| Name | `SFormField` + `SInput` | Required, 1-200 chars |
| Allow org members | `SFormField` + `SToggle` | Default: false |
| Allow project members | `SFormField` + `SToggle` | Default: true |
| Owners only | `SFormField` + `SToggle` | Default: false |
| Allow guest links | `SFormField` + `SToggle` | Default: false |

When "Owners only" is toggled on, the "Allow org members" and "Allow project members" toggles are visually dimmed (opacity 0.5) with a tooltip explaining the override.

### 2.6 States

| State | Display |
|-------|---------|
| Loading | `STable` with 3 skeleton rows |
| Empty | `SEmptyState`: `ChatBubbleLeftRightIcon` 48px, title "No chatrooms yet", description "Create a chatroom and bind agents to start conversations.", action "Create Chatroom" |
| Error | `SAlert` variant danger with retry button |

### 2.7 Responsive

- **>= 768px**: full table with all columns
- **< 768px**: card layout — each chatroom as `SCard` with name, badges, agent count stacked vertically

---

## 3. ChatroomView

**File**: `src/slices/conversation/views/ChatroomView.vue`

**Route**: `/chatrooms/:chatroomId` (`conversation.chatroom`)

**Layout**: AppShell, sidebar collapsed (`sidebarCollapsed: true`), content padding 0 (`contentPadding: 'none'`). The chatroom manages its own full-height layout.

This is the most complex view in the application. It combines real-time messaging, multi-agent interaction, file uploads, search, and presence tracking in a responsive 3-column layout.

### 3.1 Desktop Layout

**Breakpoint**: >= 1280px (all three columns visible)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Chatroom Header (48px)                                                      │
│  [<] [ChatBubbleIcon] #general     [Live]     [Search] [Settings] [Export]   │
├─────────────┬────────────────────────────────────────────────┬───────────────┤
│             │                                                │               │
│  Agent      │  ┌─ Load Earlier ──────────────────────────┐   │  Presence     │
│  Sidebar    │  │ [Load earlier messages]                  │   │  Panel        │
│  (220px)    │  └──────────────────────────────────────────┘   │  (200px)      │
│             │                                                │               │
│  ┌────────┐ │  ┌──────────────────────────────────────────┐   │  ┌─────────┐ │
│  │BOUND   │ │  │ Alice Chen                    10:23 AM   │   │  │ ONLINE  │ │
│  │AGENTS  │ │  │ Can you analyze this dataset?             │   │  │  (3)    │ │
│  │ (2)    │ │  └──────────────────────────────────────────┘   │  │         │ │
│  ├────────┤ │                                                │  │ [A] Ali │ │
│  │        │ │  ┌──────────────────────────────────────────┐   │  │ [A] Bob │ │
│  │[A]     │ │  │ |  GPT-4o Agent              10:24 AM    │   │  │ [A] Eve │ │
│  │GPT-4o  │ │  │ |  Based on the data, here are the key   │   │  │         │ │
│  │Idle    │ │  │ |  findings:                              │   │  ├─────────┤ │
│  │        │ │  │ |  1. Revenue up 15%                      │   │  │ AGENTS  │ │
│  ├────────┤ │  │ |  2. Engagement up 23%                   │   │  │         │ │
│  │        │ │  │ |                                          │   │  │ GPT-4o  │ │
│  │[A]     │ │  │ |  ```python                               │   │  │  Idle   │ │
│  │Claude  │ │  │ |  df = pd.read_csv(...)                   │   │  │ Claude  │ │
│  │Thinking│ │  │ |  ```                                     │   │  │  [...]  │ │
│  │  ...   │ │  └──────────────────────────────────────────┘   │  │         │ │
│  │        │ │                                                │  │         │ │
│  └────────┘ │  ┌──────────────────────────────────────────┐   │  │         │ │
│             │  │ [A] Claude                     Streaming  │   │  │         │ │
│             │  │ I'll complement that analysis with...     │   │  │         │ │
│             │  │ _                                          │   │  │         │ │
│             │  └──────────────────────────────────────────┘   │  └─────────┘ │
│             │                                                │               │
│             ├────────────────────────────────────────────────┤               │
│             │  Alice is typing...                            │               │
│             ├────────────────────────────────────────────────┤               │
│             │  [+] [Type a message...                ] [>]   │               │
│             │  ┌──────────────────────────────────────────┐  │               │
│             │  │ report.pdf  (uploading 45%) [x]          │  │               │
│             │  └──────────────────────────────────────────┘  │               │
└─────────────┴────────────────────────────────────────────────┴───────────────┘
```

**CSS Grid definition**:

```css
.chatroom {
  display: grid;
  grid-template-columns: 220px 1fr 200px;
  grid-template-rows: 48px 1fr auto auto;
  height: 100%;
  overflow: hidden;
}

.chatroom-header     { grid-column: 1 / -1; grid-row: 1; }
.agent-sidebar       { grid-column: 1;      grid-row: 2 / -1; }
.message-feed        { grid-column: 2;      grid-row: 2; }
.typing-indicator    { grid-column: 2;      grid-row: 3; }
.composer            { grid-column: 2;      grid-row: 4; }
.presence-panel      { grid-column: 3;      grid-row: 2 / -1; }
```

**Intermediate breakpoint** (1024-1279px): agent sidebar and presence panel collapse by default. Toggle buttons in the header expand them as overlay panels (absolute positioned, `--z-dropdown` z-index, `--shadow-lg`).

```css
@media (min-width: 1024px) and (max-width: 1279px) {
  .chatroom {
    grid-template-columns: 1fr;
  }
  .agent-sidebar,
  .presence-panel {
    position: absolute;
    z-index: var(--z-dropdown);
    box-shadow: var(--shadow-lg);
  }
  .agent-sidebar  { left: 0;  width: 220px; top: 48px; bottom: 0; }
  .presence-panel { right: 0; width: 200px; top: 48px; bottom: 0; }
}
```

### 3.2 Mobile Layout

**Breakpoint**: < 1024px (single column, panels as drawers)

```
┌────────────────────────────────────┐
│ [<] [Agents] #general    [People] │
├────────────────────────────────────┤
│                                    │
│  ┌──────────────────────────────┐  │
│  │ Alice Chen         10:23 AM  │  │
│  │ Can you analyze this?        │  │
│  └──────────────────────────────┘  │
│                                    │
│  ┌──────────────────────────────┐  │
│  │ |  GPT-4o Agent    10:24 AM  │  │
│  │ |  Here are the findings:    │  │
│  │ |  1. Revenue up 15%         │  │
│  │ |  ...                       │  │
│  └──────────────────────────────┘  │
│                                    │
│  ┌──────────────────────────────┐  │
│  │ [A] Claude          Streaming│  │
│  │ Complementing with... _      │  │
│  └──────────────────────────────┘  │
│                                    │
│  Bob is typing...                  │
├────────────────────────────────────┤
│ [+] [Type a message...      ] [>] │
└────────────────────────────────────┘
```

**Mobile grid definition**:

```css
@media (max-width: 1023px) {
  .chatroom {
    grid-template-columns: 1fr;
    grid-template-rows: 48px 1fr auto auto;
  }
  .agent-sidebar, .presence-panel { display: none; }
}
```

**Drawer panels** (mobile):

| Button | Position | Component | Content |
|--------|----------|-----------|---------|
| `[Agents]` (`CpuChipIcon`) | Header left | `SDrawer` from left | Agent sidebar content |
| `[People]` (`UsersIcon`) | Header right | `SDrawer` from right | Presence panel content |

Drawers close on: outside tap, swipe in direction of origin, route change, or pressing the toggle button again.

**Header on mobile**:

```
┌────────────────────────────────────────────┐
│ [<] [Agents] #general  [People] [...]      │
└────────────────────────────────────────────┘
```

The `[...]` button (`EllipsisVerticalIcon`) opens an `SDropdown` with: Search, Settings, Export. These replace the individual icon buttons used on desktop.

### 3.3 Message Bubbles

All messages are left-aligned for consistent reading flow across multi-participant conversations.

#### User Message

```
┌──────────────────────────────────────────────────────────┐
│ [A] Alice Chen                               10:23 AM   │
│ ──────────────────────────────────────────────────────── │
│ Can you analyze this dataset and provide                 │
│ insights on the revenue trends?                          │
│                                                          │
│ [PaperClipIcon] quarterly-data.csv (2.4 MB)             │
│                                              [edited]    │
└──────────────────────────────────────────────────────────┘
                 [Edit]  [Delete]  [Copy]   <-- hover row
```

**Visual spec**:
- Background: `--color-bg` (white)
- Border: 1px `--color-border`
- Border-radius: `--radius-md`
- Padding: 12px 16px
- Margin-bottom: 8px
- Max-width: 75% of feed width
- Min-width: 200px

**Metadata row** (top):
- Avatar: `SAvatar` 28px, user initials, positioned left
- Sender name: 13px 600 weight `--color-fg`, 8px gap after avatar
- Timestamp: 12px 400 weight `--color-muted`, right-aligned (`margin-left: auto`)
- Gap between name and timestamp filled by flex

**Content area**:
- Rendered HTML via `renderMarkdown()` + DOMPurify
- Body text: 14px 400 weight `--color-fg`, line-height 1.5
- Code blocks: `--font-mono`, 13px, `--color-surface` background, `--radius-sm` corners, padding 12px
- Inline code: `--font-mono`, 13px, `--color-surface` background, padding 2px 4px, `--radius-sm`
- Links: `--color-accent`, underline on hover
- Images: max-width 100%, `--radius-sm` corners
- Tables: bordered with `--color-border`, header row `--color-surface` background

**Attachments row** (bottom, if present):
- `PaperClipIcon` 16px `--color-muted`
- Filename as link (`--color-accent`), file size in parentheses (`--color-muted`, 12px)
- Quarantined: `ShieldExclamationIcon` + strikethrough name + "(quarantined)" in `--color-warning`
- Expired: `ClockIcon` + strikethrough name + "(expired)" in `--color-muted`

**Edit indicator**: "edited" text, 11px, `--color-muted`, italic, bottom-right

**Hover actions row**: appears 4px below the bubble on hover, right-aligned, 28px height
- `PencilSquareIcon` "Edit" — ghost button, `--color-accent`, 12px
- `TrashIcon` "Delete" — ghost button, `--color-danger`, 12px
- `ClipboardDocumentIcon` "Copy" — ghost button, `--color-muted`, 12px
- Touch: actions visible on long-press (300ms) via context menu
- Buttons: 32x28px minimum touch target, 8px gap between

**Edit permission logic**:
- Edit visible: author within 5 minutes of `created_at`, or user has admin/owner role
- Delete visible: author (own message), or user has admin/owner role

#### Agent Message

```
┌──────────────────────────────────────────────────────────┐
│ [A] GPT-4o Agent                             10:24 AM   │
│ ──────────────────────────────────────────────────────── │
│ Based on the quarterly data, here are the key            │
│ findings:                                                │
│                                                          │
│ 1. Revenue increased by 15% quarter-over-quarter         │
│ 2. User engagement metrics show a 23% improvement        │
│                                                          │
│ ```python                                                │
│ import pandas as pd                                      │
│ df = pd.read_csv('quarterly.csv')                        │
│ summary = df.describe()                                  │
│ ```                                                      │
└──────────────────────────────────────────────────────────┘
```

**Visual spec** (differences from user message):
- Left border: 3px solid `--color-accent`
- Background: `--color-surface`
- Avatar: `SAvatar` 28px with `CpuChipIcon` overlay or agent initial, `--color-accent` ring
- Sender name: 13px 600 weight `--color-accent`
- Max-width: 85% of feed width (agents produce longer content)
- No hover edit/delete actions (agent messages are system-managed)
- Copy action available on hover

#### System Message

System messages are centered, compact, and visually muted.

```
             ─── Alice joined the chatroom ───
```

**Visual spec**:
- No bubble, no border
- Centered text, max-width 60%
- Font: 12px 400 weight `--color-muted`, italic
- Horizontal rules (`SDivider`) on either side of text, `--color-border`
- Margin: 12px 0
- No avatar, no timestamp (timestamp available on hover via `STooltip`)

#### Inline Edit Mode

When a user clicks "Edit" on their own message:

```
┌──────────────────────────────────────────────────────────┐
│ [A] Alice Chen                               10:23 AM   │
│ ──────────────────────────────────────────────────────── │
│ ┌────────────────────────────────────────────────────┐   │
│ │ Can you analyze this dataset and provide           │   │
│ │ insights on the revenue trends?                    │   │
│ │                                                    │   │
│ └────────────────────────────────────────────────────┘   │
│                                     [Save]  [Cancel]     │
└──────────────────────────────────────────────────────────┘
```

- The rendered content is replaced by a `STextarea` with the raw markdown
- `STextarea`: auto-growing, min-height 64px, max-height 200px
- Save button: `SButton` size sm variant primary
- Cancel button: `SButton` size sm variant secondary
- Escape key cancels, Ctrl+Enter saves
- While editing, the bubble border changes to 1px `--color-accent`

### 3.4 Agent Streaming

Agent streaming renders token-by-token output from bound agents via `agent.token` WebSocket events.

#### State Machine

```
Idle ──[agent.thinking]──> Thinking ──[agent.token]──> Streaming ──[agent.finished]──> Idle
                                         │                              │
                                         └──[agent.finished{error}]─────┘──> Error
```

#### Thinking State

When `agent.thinking` fires and no tokens have arrived yet:

```
┌──────────────────────────────────────────────────────────┐
│ [A] Claude 3.5                                           │
│ ──────────────────────────────────────────────────────── │
│ Thinking...                                              │
└──────────────────────────────────────────────────────────┘
```

- Same visual frame as agent message (left accent border, surface bg)
- "Thinking..." text: 14px `--color-muted`, italic
- Animated three-dot pulse after text: CSS keyframe, `opacity: 0.3 -> 1 -> 0.3`, 1.4s loop, dots staggered by 0.2s

```css
@keyframes thinking-dot {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}
.thinking-dot:nth-child(1) { animation-delay: 0s; }
.thinking-dot:nth-child(2) { animation-delay: 0.2s; }
.thinking-dot:nth-child(3) { animation-delay: 0.4s; }
```

- Client-side watchdog: if no `agent.token` or `agent.finished` arrives within 120 seconds, the composable sets `agentError[roomId] = 'timeout'` and the view surfaces a toast.

#### Streaming State

As `agent.token` events arrive, content accumulates and renders progressively:

```
┌──────────────────────────────────────────────────────────┐
│ [A] Claude 3.5                              Streaming    │
│ ──────────────────────────────────────────────────────── │
│ Based on my analysis, the key factors are:               │
│                                                          │
│ 1. Market positioning has improved due to_               │
└──────────────────────────────────────────────────────────┘
```

- Status label: "Streaming" replaces timestamp, 12px `--color-accent`, italic
- Blinking cursor: `_` block character at end of text, CSS animation `opacity: 1 -> 0`, 1s interval, `steps(1)`
- Rendering: memoized per agent — only re-renders when accumulated text changes; the `_streamCache` map avoids calling `renderMarkdown()` on every token at high frequency
- Progressive markdown: partial markdown is tolerated (unclosed fences, incomplete lists) because `renderMarkdown()` handles truncated input gracefully
- The streaming bubble is a transient element, not a persisted message — it lives outside the TanStack Query cache

```css
@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.streaming-cursor {
  animation: blink-cursor 1s steps(1) infinite;
  color: var(--color-accent);
  font-weight: 600;
}
```

#### Completion Transition

When `agent.finished` fires:
1. The corresponding `message.created` event adds the final message to TanStack Query cache
2. The streaming bubble for that agent is removed (`clearAgentStream`)
3. The real message bubble appears in its place with final rendered content
4. No visible flash — the transition is seamless because streaming content matches final content

#### Error State

When agent finishes with an error or the watchdog triggers timeout:
- Toast notification: `useToast().error()` with localized message
- Agent status in sidebar changes to `--color-danger` text
- The incomplete streaming bubble remains visible but the cursor stops blinking
- Error auto-clears from store after the view consumes it

### 3.5 Typing Indicators

**Position**: between the message feed bottom and the composer, spanning the center column.

```
┌──────────────────────────────────────────────────────────┐
│ Alice is typing...                                       │
└──────────────────────────────────────────────────────────┘
```

**Visual spec**:
- Height: 24px (fixed, always reserved to prevent layout shift)
- Padding: 0 16px
- Font: 13px 400 weight `--color-muted`, italic
- Visibility: hidden (transparent) when no one is typing; fade-in 150ms on show

**Text format** (localized via `$t()`):
- 1 user: "Alice is typing..."
- 2 users: "Alice and Bob are typing..."
- 3+ users: "3 people are typing..."
- Current user is excluded from the typing list
- User IDs resolve to display names via session context; fallback to truncated ID (first 8 chars)

**Animated dots**: three dots after "typing" text, same pulse animation as thinking indicator but at 12px size.

**Client-side emit logic**:
- `typing.start` sent on first keystroke in composer
- 3-second debounce timer resets on each keystroke
- `typing.stop` sent when timer expires
- Timer cleared on unmount

### 3.6 Presence Panel

**Position**: right column (200px), full height from below header to bottom.

```
┌────────────────────┐
│ ONLINE (3)         │
│ ────────────────── │
│ [A] Alice Chen     │
│     (you)          │
│ [A] Bob Smith      │
│ [A] Eve Johnson    │
│                    │
│ ────────────────── │
│ AGENT STATUS       │
│ ────────────────── │
│                    │
│ [C] GPT-4o         │
│     Idle           │
│                    │
│ [C] Claude 3.5     │
│     Thinking...    │
│                    │
└────────────────────┘
```

**Visual spec**:
- Background: `--color-surface`
- Border-left: 1px `--color-border`
- Padding: 16px
- Overflow-y: auto

**Section: Online Users**:
- Section header: "ONLINE" + count in parentheses, 11px 600 weight uppercase `--color-muted`, letter-spacing 0.05em
- Each user: `SAvatar` 24px + display name, 14px 400 weight `--color-fg`
- Current user tagged with "(you)" in 12px `--color-muted`
- User items: height 36px, hover `--color-sidebar-hover` background
- Green dot indicator: 8px circle `--color-success` on avatar corner (bottom-right)
- Users sorted alphabetically, current user first

**Section: Agent Status**:
- Section header: "AGENT STATUS", same style as above
- Each agent: `SAvatar` 24px (with `CpuChipIcon` or agent initial) + agent name + status text
- Status values:

| Status | Text | Color | Animation |
|--------|------|-------|-----------|
| Idle | "Idle" | `--color-muted` | None |
| Thinking | "Thinking..." | `--color-accent` | Three-dot pulse |
| Streaming | "Streaming" | `--color-accent` | Subtle pulse on text |
| Error | "Error" | `--color-danger` | None |

- Agent items: height 44px (taller to accommodate status line)
- Agent thinking animation: `CpuChipIcon` gets a subtle rotate animation (360deg / 2s linear infinite) when thinking/streaming

### 3.7 Composer

**File**: `src/slices/conversation/components/ChatroomComposer.vue`

**Position**: bottom of center column, full width of message feed area.

```
┌──────────────────────────────────────────────────────────────┐
│ [+] ┌──────────────────────────────────────────────┐  [>]   │
│     │ Type a message...                            │        │
│     │                                              │        │
│     └──────────────────────────────────────────────┘        │
│ ┌────────────────────────────────────────────────────────┐   │
│ │ [DocIcon] report.pdf        ████████░░  78%       [x]  │   │
│ │ [ImgIcon] screenshot.png    Ready                 [x]  │   │
│ └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**Visual spec**:
- Background: `--color-bg`
- Border-top: 1px `--color-border`
- Padding: 12px 16px
- Layout: flex row, align-items end, gap 8px

**Attachment button** (`[+]`):
- `PlusIcon` 20px in 36x36px ghost button
- Click opens native file picker (no type restriction)
- Accepts any file up to 1 GB
- Multiple selection allowed
- Alternatively: drag-and-drop on the entire composer area

**Textarea**:
- `STextarea` variant borderless, flex 1
- Placeholder: "Type a message..." (via `$t()`)
- Min-height: 36px (single line)
- Max-height: 192px (approximately 8 lines, then scrolls internally)
- Auto-grows with content
- Font: 14px 400 weight `--color-fg`
- Focus: `--focus-ring` on the entire composer border, not just the textarea

**Send button** (`[>]`):
- `PaperAirplaneIcon` 20px in 36x36px button
- Variant: primary when message or uploads are ready; ghost and disabled when both empty
- Disabled state: `opacity: 0.4`, `cursor: not-allowed`

**Keyboard shortcuts**:

| Key | Action |
|-----|--------|
| `Enter` | Send message (if content or pending uploads) |
| `Shift+Enter` | Insert newline |
| `Escape` | Clear draft (if in normal mode); cancel edit (if editing) |

**Pending uploads list**:
- Displayed between textarea and bottom border
- Each item: file type icon (derived from MIME) + filename + `SProgressBar` (if uploading) or "Ready" text + remove button (`XMarkIcon`)
- File type icons: `DocumentIcon` (default), `PhotoIcon` (image/*), `FilmIcon` (video/*), `MusicalNoteIcon` (audio/*)
- Upload progress: `SProgressBar` height 4px, `--color-accent` fill
- Files > 32 MB: automatic tus protocol (16 MB chunks); < 32 MB: single-shot POST
- Remove button: `XMarkIcon` 16px ghost button, cancels in-flight upload if active
- Max visible items: 4, then scrolls with overflow-y auto, max-height 120px

**Drag-and-drop zone**:
- When files are dragged over the composer, the entire composer gets a dashed border (`2px dashed --color-accent`) and a translucent overlay with `ArrowUpTrayIcon` 48px and "Drop files here" text
- Drop zone covers the full composer area
- Visual feedback: `--color-accent` at 0.1 opacity background

**Disabled state** (WebSocket disconnected):
- Textarea is readonly, placeholder changes to "Reconnecting..."
- Send button disabled
- Attachment button disabled
- Subtle `--color-danger-tint` background tint on the entire composer

### 3.8 Search

Search is activated by clicking the search button in the header or pressing `Ctrl+K` / `Cmd+K`. It renders as a panel that slides down from the header.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Header                                                              │
├──────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────┐  [Close]     │
│  │ [MagnifyingGlassIcon] Search messages...           │              │
│  └────────────────────────────────────────────────────┘              │
│                                                                      │
│  3 results for "revenue analysis"                                    │
│  ────────────────────────────────────────────────────────────────    │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Alice Chen · Dec 15, 10:23 AM                                  │  │
│  │ Can you analyze this dataset and provide insights on the       │  │
│  │ **revenue** trends?                                            │  │
│  └────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ GPT-4o Agent · Dec 15, 10:24 AM                                │  │
│  │ Based on the quarterly data, **revenue** increased by 15%      │  │
│  └────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Claude 3.5 · Dec 15, 10:25 AM                                  │  │
│  │ The **revenue analysis** shows a strong upward trend...         │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  Message Feed (dimmed with overlay)                                  │
└──────────────────────────────────────────────────────────────────────┘
```

**Visual spec**:
- Panel: slides down from below header, absolute positioned, `--z-dropdown` z-index
- Background: `--color-bg`, border-bottom 1px `--color-border`, `--shadow-md`
- Padding: 16px
- Max-height: 50vh, overflow-y auto
- Animation: slide down 200ms ease (`--transition-normal`)
- Message feed behind panel: dimmed with `--overlay-backdrop` at 0.2 opacity

**Search input**:
- `SSearchInput` component, full width
- Auto-focus on open
- Debounced search: 300ms after last keystroke
- API: `GET /api/chatrooms/{cid}/search?q=...`
- Clear button (`XMarkIcon`) inside input

**Result count**: "N results for 'query'" — 13px `--color-muted`

**Result items**:
- Clickable cards with subtle hover (`--color-surface` background)
- Sender name + timestamp on first line: 12px `--color-muted`
- Snippet on second line: 14px `--color-fg`, search terms highlighted with `<mark>` tag (yellow background `--color-warning-tint`, `--radius-sm` padding)
- Sanitized via `sanitizeSnippet()` from `renderMarkdown.ts`
- Click action: closes search panel, scrolls message feed to the matching message, highlights the message bubble briefly (200ms `--color-accent` at 0.1 opacity flash)

**Close actions**:
- Close button (`XMarkIcon`) in panel header
- `Escape` key
- Clicking outside the panel (on the dimmed overlay)

**States**:
- Empty query: panel visible with just the input, no results section
- Searching: `SLoadingSpinner` below input
- No results: "No messages match your search" in `--color-muted`, 14px
- Error: `SAlert` variant danger inline

**Mobile**: same panel but full-width, max-height 60vh

### 3.9 Export

Export generates an offline archive of the chatroom's message history. The flow uses an async backend job.

**Trigger**: "Export" button in header (`ArrowDownTrayIcon`). Opens an `SModal`.

```
┌─────────────────────────────────────────────────┐
│  Export Chatroom                         [X]     │
│  ─────────────────────────────────────────────── │
│                                                  │
│  Format                                          │
│  (o) Markdown    ( ) JSON    ( ) PDF             │
│                                                  │
│  Date Range                                      │
│  [SSelect: All Messages          v]              │
│    Options: All Messages                         │
│             Last 7 Days                          │
│             Last 30 Days                         │
│             Custom Range...                      │
│                                                  │
│  [Custom date inputs if selected]                │
│                                                  │
│  ─────────────────────────────────────────────── │
│                           [Cancel]   [Export]     │
└─────────────────────────────────────────────────┘
```

**Form fields**:

| Field | Component | Options |
|-------|-----------|---------|
| Format | Radio group (`SFormField`) | Markdown (default), JSON, PDF |
| Date Range | `SSelect` | All Messages, Last 7 Days, Last 30 Days, Custom Range |
| Custom Start | `SInput type="date"` | Only visible when "Custom Range" selected |
| Custom End | `SInput type="date"` | Only visible when "Custom Range" selected |

**Export flow**:

1. User fills form and clicks "Export" (`SButton` primary)
2. `POST /api/chatrooms/{cid}/export` with format and date range
3. Modal transitions to progress state:

```
┌─────────────────────────────────────────────────┐
│  Export Chatroom                         [X]     │
│  ─────────────────────────────────────────────── │
│                                                  │
│  [DocumentArrowDownIcon 48px]                    │
│                                                  │
│  Exporting chatroom history...                   │
│  SProgressBar (indeterminate)                    │
│                                                  │
│  Status: Running                                 │
│                                                  │
└─────────────────────────────────────────────────┘
```

4. `useChatroomExport` polls the export job status every 3 seconds
5. On completion:

```
┌─────────────────────────────────────────────────┐
│  Export Chatroom                         [X]     │
│  ─────────────────────────────────────────────── │
│                                                  │
│  [CheckCircleIcon 48px, --color-success]         │
│                                                  │
│  Export ready!                                   │
│                                                  │
│  [Download]                                      │
│                                                  │
│  Link expires in 24 hours.                       │
│                                                  │
└─────────────────────────────────────────────────┘
```

6. On failure:

```
┌─────────────────────────────────────────────────┐
│  Export Chatroom                         [X]     │
│  ─────────────────────────────────────────────── │
│                                                  │
│  [ExclamationCircleIcon 48px, --color-danger]    │
│                                                  │
│  Export failed.                                  │
│  Unable to generate the archive.                 │
│                                                  │
│                         [Cancel]   [Retry]        │
│                                                  │
└─────────────────────────────────────────────────┘
```

**Download button**: `SButton` primary with `ArrowDownTrayIcon`, opens the signed URL in a new tab. URL has 24-hour TTL — the expiry notice is shown in 12px `--color-muted` below the button.

### 3.10 Load-Earlier Pagination

Messages are fetched with cursor-based pagination (`GET /api/chatrooms/{cid}/messages?before=<cursor>&limit=50`). The initial load fetches the most recent 50 messages.

**Load-earlier button**:

```
┌──────────────────────────────────────────────────────────────┐
│               [ArrowUpIcon] Load earlier messages             │
└──────────────────────────────────────────────────────────────┘
```

- Position: top of message feed, visible when `hasOlderMessages` is true
- Style: `SButton` variant secondary, size sm, centered, full-width within a max-width of 240px
- Icon: `ArrowUpIcon` 16px before text
- Disabled while loading

**Loading state**: button text changes to "Loading..." with `SLoadingSpinner` replacing the icon.

**Scroll preservation**: after new (older) messages are prepended, the scroll position is adjusted so the previously-topmost visible message remains in the same viewport position. Implementation uses `scrollTop` delta calculation before and after DOM update.

**Auto-trigger**: when the user scrolls to within 100px of the top of the feed and `hasOlderMessages` is true, `loadEarlier()` triggers automatically (scroll-based pagination). The button remains as a fallback for users who prefer explicit loading.

**Page size**: 50 messages per fetch. Cursor is the `id` of the oldest loaded message.

### 3.11 Scroll Behavior

The message feed must balance two needs: auto-scrolling for live conversation and preserving position when reviewing history.

**Auto-scroll rules**:

| Condition | Behavior |
|-----------|----------|
| User is at bottom (within 80px of scrollHeight) | New messages auto-scroll feed to bottom |
| User has scrolled up (> 80px from bottom) | Feed does NOT auto-scroll; new messages pill appears |
| User sends a message | Always scrolls to bottom |
| Agent streaming tokens arrive | Auto-scrolls only if user was at bottom when streaming started |

**New Messages Pill**:

When new messages arrive while the user is scrolled up:

```
                    ┌──────────────────────────┐
                    │ [ChevronDownIcon] New messages │
                    └──────────────────────────┘
```

- Position: fixed at center-bottom of message feed, 16px above the typing indicator
- Style: `--color-accent` background, white text, `--radius-full` (pill shape), padding 6px 16px, `--shadow-md`
- Animation: fade-in + slide-up 150ms on appear
- `ChevronDownIcon` 16px before text
- Badge: count of unseen messages if > 1 ("3 new messages")
- Click: smooth-scrolls to bottom (300ms duration), pill disappears
- Auto-dismiss: when user scrolls to bottom manually

**Initial load scroll**: on first mount, scrolls to bottom immediately (no animation).

**Route restoration**: when returning to a chatroom via back navigation (Vue Router `keep-alive`), scroll position is restored to where the user left off.

### 3.12 Approval Cards

Approval cards are rendered inline in the message feed when a workflow step requests human approval. They come from `approval.requested` WebSocket events and are tracked in the orchestration store.

#### Pending Approval

```
┌──────────────────────────────────────────────────────────────┐
│ [ShieldCheckIcon]  Approval Required                         │
│ ──────────────────────────────────────────────────────────── │
│ Workflow "Data Pipeline" requests approval for:              │
│ Step 3: "Delete stale records older than 90 days"            │
│                                                              │
│                                     [Reject]    [Approve]    │
└──────────────────────────────────────────────────────────────┘
```

**Visual spec (pending)**:
- Background: `--color-warning-tint`
- Border: 1px `--color-warning`
- Border-left: 3px `--color-warning`
- Border-radius: `--radius-md`
- Padding: 12px 16px
- Margin: 12px 0 (vertical gap around the card in the feed)
- Icon: `ShieldCheckIcon` 20px `--color-warning-on`
- Title: "Approval Required" — 14px 600 weight `--color-warning-on`
- Description: 14px 400 weight `--color-fg`
- Approve button: `SButton` size sm variant primary ("Approve")
- Reject button: `SButton` size sm variant danger-secondary ("Reject")
- Reject action: opens `SConfirmDialog` with optional rejection reason textarea

#### Resolved Approval

```
┌──────────────────────────────────────────────────────────────┐
│ [CheckCircleIcon]  Approved by Alice · Dec 15, 10:30 AM     │
│ Step 3: "Delete stale records older than 90 days"            │
└──────────────────────────────────────────────────────────────┘
```

**Visual spec (approved)**:
- Background: `--color-success-tint`
- Border-left: 3px `--color-success`
- Icon: `CheckCircleIcon` 20px `--color-success-on`
- Title: "Approved by {name}" + timestamp — 14px 500 weight `--color-success-on`
- No action buttons

**Visual spec (rejected)**:
- Background: `--color-danger-tint`
- Border-left: 3px `--color-danger`
- Icon: `XCircleIcon` 20px `--color-danger-on`
- Title: "Rejected by {name}" + timestamp — 14px 500 weight `--color-danger-on`
- Optional rejection reason below title in 13px `--color-muted` italic

**Position logic**: approval cards are placed in the message feed at the chronological position where the approval was requested, interleaved with regular messages.

### 3.13 Empty State

When a chatroom has no messages and no active streaming:

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│                                                              │
│                  [ChatBubbleLeftRightIcon 48px]               │
│                                                              │
│                   No messages yet                            │
│                                                              │
│         Start the conversation by typing a message           │
│         below, or wait for a bound agent to respond.         │
│                                                              │
│                  Bound agents: GPT-4o, Claude 3.5            │
│                                                              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Component**: `SEmptyState`

- Icon: `ChatBubbleLeftRightIcon` 48px `--color-muted` at 0.5 opacity
- Title: "No messages yet" — 18px 600 weight `--color-fg`
- Description: "Start the conversation by typing a message below, or wait for a bound agent to respond." — 14px 400 weight `--color-muted`
- Agent list (if agents are bound): "Bound agents: Agent1, Agent2" — 13px `--color-accent`
- No action button (the composer is always visible below)
- Vertically and horizontally centered in the message feed area

### 3.14 Chatroom Header

**File**: `src/slices/conversation/components/ChatroomHeader.vue`

```
┌──────────────────────────────────────────────────────────────────────┐
│ [<] [ChatBubbleIcon] #general      [Live]     [Search] [Gear] [>|] │
└──────────────────────────────────────────────────────────────────────┘
```

**Visual spec**:
- Height: 48px
- Background: `--color-bg`
- Border-bottom: 1px `--color-border`
- Padding: 0 16px
- Display: flex, align-items center, gap 12px

**Elements**:

| Element | Icon | Behavior | Notes |
|---------|------|----------|-------|
| Back | `ArrowLeftIcon` 20px | Navigates to chatroom list | Ghost button 36x36px |
| Room icon | `ChatBubbleLeftRightIcon` 20px | Decorative | `--color-accent` |
| Room name | None | Text, truncated with ellipsis | 16px 600 weight, max-width 300px |
| Connection pill | None | Live status indicator | See below |
| Search | `MagnifyingGlassIcon` 20px | Opens search panel | Ghost button 36x36px |
| Settings | `Cog6ToothIcon` 20px | Navigates to settings | Ghost button 36x36px |
| Export | `ArrowDownTrayIcon` 20px | Opens export modal | Ghost button 36x36px |

**Connection status pill**:

| State | Label | Color | Icon |
|-------|-------|-------|------|
| Connected | "Live" | `--color-success` text, `--color-success-tint` bg | `SignalIcon` 12px |
| Reconnecting | "Reconnecting" | `--color-warning` text, `--color-warning-tint` bg | `ArrowPathIcon` 12px (spin animation) |
| Disconnected | "Offline" | `--color-danger` text, `--color-danger-tint` bg | `SignalSlashIcon` 12px |

Pill style: `SBadge`-like, `--radius-full`, padding 2px 10px, 12px font.

---

## 4. ChatroomSettingsView

**File**: `src/slices/conversation/views/ChatroomSettingsView.vue`

**Route**: `/chatrooms/:chatroomId/settings` (`conversation.chatroom.settings`)

**Layout**: AppShell, sidebar visible, content padding 24px.

### 4.1 Wireframe

```
┌──────────────────────────────────────────────────────────────────────┐
│  SBreadcrumb: Chatrooms > #general > Settings                        │
├──────────────────────────────────────────────────────────────────────┤
│  SPageHeader                                                         │
│  Chatroom Settings                                                   │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌── General ────────────────────────────────────────────────────┐   │
│  │ SCard                                                         │   │
│  │                                                               │   │
│  │ Name                                                          │   │
│  │ ┌──────────────────────────────────────┐                      │   │
│  │ │ #general                             │                      │   │
│  │ └──────────────────────────────────────┘                      │   │
│  │                                                               │   │
│  │                                          [Save Changes]       │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌── Access Control ─────────────────────────────────────────────┐   │
│  │ SCard                                                         │   │
│  │                                                               │   │
│  │ Allow organization members              [SToggle]             │   │
│  │ Any member of the organization can join.                      │   │
│  │                                                               │   │
│  │ Allow project members                   [SToggle]             │   │
│  │ Any member of this project can join.                          │   │
│  │                                                               │   │
│  │ Restrict to project owners              [SToggle]             │   │
│  │ Only project owners can access. Overrides member flags.       │   │
│  │                                                               │   │
│  │ Allow guest links                       [SToggle]             │   │
│  │ Anyone with the link can join as a guest.                     │   │
│  │                                                               │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌── Guest Link ─────────────────────────────────────────────────┐   │
│  │ SCard (visible only when allow_guest_links is on)             │   │
│  │                                                               │   │
│  │ Share this permanent link to invite guests:                   │   │
│  │ ┌──────────────────────────────────────────────────┐ [Copy]   │   │
│  │ │ https://smap.example.com/g/{id}/{token}          │          │   │
│  │ └──────────────────────────────────────────────────┘          │   │
│  │                                                               │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌── Bound Agents ───────────────────────────────────────────────┐   │
│  │ SCard                                                         │   │
│  │                                                               │   │
│  │ ┌──────────────────────────────────────────────────────────┐  │   │
│  │ │ [SSelect: Choose an agent...  v]        [+ Bind Agent]   │  │   │
│  │ └──────────────────────────────────────────────────────────┘  │   │
│  │                                                               │   │
│  │ STable                                                        │   │
│  │ ┌──────────────────────────────────────────────────────────┐  │   │
│  │ │ Agent        │ Wake-up Config │ DLQ Status │  Actions    │  │   │
│  │ ├──────────────┼────────────────┼────────────┼─────────────┤  │   │
│  │ │ GPT-4o       │ @mention       │ 0 items    │ [Edit] [X]  │  │   │
│  │ │ Claude 3.5   │ always         │ 2 items    │ [Edit] [X]  │  │   │
│  │ └──────────────┴────────────────┴────────────┴─────────────┘  │   │
│  │                                                               │   │
│  │ Orphaned bindings (agent deleted):                            │   │
│  │  abc12345 · Unknown agent                         [Remove]    │   │
│  │                                                               │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌── Danger Zone ────────────────────────────────────────────────┐   │
│  │ SCard (border: 1px --color-danger)                            │   │
│  │                                                               │   │
│  │ Compact History                          [Compact]            │   │
│  │ Merge old messages to reduce storage.                         │   │
│  │ This action cannot be undone.                                 │   │
│  │                                                               │   │
│  │ SDivider                                                      │   │
│  │                                                               │   │
│  │ Delete Chatroom                          [Delete Chatroom]    │   │
│  │ Permanently remove this chatroom and                          │   │
│  │ all messages. This cannot be undone.                          │   │
│  │                                                               │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 Section Details

#### General

- Name field: `SFormField` + `SInput`, required, maxLength 200
- Save button: `SButton` variant primary, disabled when name is unchanged or empty
- Validation error: inline under the input via `SFormField` error slot

#### Access Control

- Each flag rendered as `SFormField` + `SToggle` with label and description
- When "Restrict to project owners" is toggled on, the "Allow org members" and "Allow project members" toggles become visually dimmed (opacity 0.5, pointer-events none) — per R13.04 auto-correct behavior
- Changes save immediately on toggle (optimistic update via `PATCH /api/chatrooms/{cid}`)
- On error: toggle reverts, `useToast().error()` with failure message

#### Guest Link

- Visible only when `allow_guest_links` is true
- Read-only `SInput` with the full guest URL
- Copy button: `SButton` size sm variant secondary with `ClipboardDocumentIcon`
- On copy success: toast "Link copied to clipboard"
- Guest link format: `https://{host}/g/{chatroomId}/{guestToken}`

#### Bound Agents

- Agent selector: `SSelect` with available (unbound) project agents + `SButton` "Bind Agent"
- Bound agents table columns:

| Column | Content |
|--------|---------|
| Agent | Agent name + `SAvatar` |
| Wake-up Config | Current wake-up trigger (editable) |
| DLQ Status | Dead-letter queue item count, clickable to expand |
| Actions | "Edit" (opens `WakeupConfigEditor` inline), "Remove" (danger, unbinds agent) |

- `WakeupConfigEditor` (from workflow slice): inline expandable panel below the agent row
- `DlqViewer` (from workflow slice): inline expandable panel showing failed messages with retry actions
- Orphaned bindings: agents that were deleted from the project but still bound — shown as truncated ID + "Unknown agent" label + remove button
- Remove action: `SConfirmDialog` "Unbind this agent? It will no longer respond in this chatroom."

#### Danger Zone

- Card: `--color-bg` background, border 1px `--color-danger`
- Compact button: `SButton` variant danger-secondary, triggers `POST /api/chatrooms/{cid}/compact`
- Compact confirmation: `SConfirmDialog` "Compact chatroom history? Older messages will be merged. This cannot be undone."
- Delete button: `SButton` variant danger, triggers `DELETE /api/chatrooms/{cid}`
- Delete confirmation: `SConfirmDialog` with chatroom name typed for confirmation (same pattern as org delete)

### 4.3 States

| State | Display |
|-------|---------|
| Loading | `SSkeleton` for each card (4 skeleton blocks) |
| Load error | `SAlert` danger with retry button, replacing all cards |
| Save error | `SAlert` danger inline below the relevant card |
| Saving | Button shows `SLoadingSpinner` + "Saving..." |

### 4.4 Responsive

- **>= 768px**: cards at max-width 640px, centered
- **< 768px**: cards full width, padding 16px

---

## 5. GuestLandingView

**File**: `src/slices/conversation/views/GuestLandingView.vue`

**Route**: `/g/:chatroomId/:guestToken` (`conversation.guest`)

**Layout**: AuthLayout (centered card, no sidebar, no top bar).

### 5.1 Wireframe

```
┌─────────────────────────────────────────────┐
│                                             │
│                   SMAP                      │
│                                             │
│         ┌─────────────────────────┐         │
│         │ Join #general           │         │
│         │                         │         │
│         │ You have been invited   │         │
│         │ to join this chatroom   │         │
│         │ as a guest.             │         │
│         │                         │         │
│         │ Display Name            │         │
│         │ ┌─────────────────────┐ │         │
│         │ │                     │ │         │
│         │ └─────────────────────┘ │         │
│         │                         │         │
│         │ [Enter Chatroom]        │         │
│         │                         │         │
│         └─────────────────────────┘         │
│                                             │
└─────────────────────────────────────────────┘
```

### 5.2 Flow

1. Guest opens the permanent link `/g/{chatroomId}/{guestToken}`
2. View validates the token via `enrollGuest(chatroomId, token)` on mount
3. On success: `history.replaceState` strips the token from the URL, then redirects to `/chatrooms/{chatroomId}` with the guest session
4. On failure: error state is displayed

### 5.3 Enrollment Card

**Visual spec**:
- Card: same as AuthLayout card — max-width 420px, `--color-bg`, `--shadow-md`, `--radius-lg`, padding 32px
- Title: "Join #chatroomName" — 20px 600 weight `--color-fg`
- Description: "You have been invited to join this chatroom as a guest." — 14px 400 weight `--color-muted`
- Display name field: `SFormField` + `SInput`, required, placeholder "Your name", maxLength 100
- Submit button: `SButton` variant primary, full width, "Enter Chatroom"
- Enter key submits the form

### 5.4 States

| State | Display |
|-------|---------|
| Loading (enrolling) | `SLoadingSpinner` centered in card with "Joining chatroom..." text |
| Success | Brief success message, then immediate redirect (user rarely sees this) |
| Invalid token | `SAlert` variant danger: "This link is no longer valid. The chatroom may have been deleted or guest access may have been disabled." No retry — the link is permanently invalid. |
| Network error | `SAlert` variant danger: "Could not connect. Please check your connection and try again." + retry button |
| Already enrolled | Auto-redirects to chatroom (transparent re-entry) |

### 5.5 Security

- The guest token is stripped from the browser URL immediately after successful enrollment via `history.replaceState` — it must never remain in browser history
- Guest sessions have limited permissions: no chatroom settings, no export, no agent binding, no admin actions
- The guest landing page does not require prior authentication (meta: `requiresAuth: true` triggers the standard auth flow which handles guest tokens specially)

---

## 6. Files Summary

### New Components

| File | Description |
|------|-------------|
| `src/slices/conversation/components/ChatroomHeader.vue` | Room header: back, name, status pill, action buttons |
| `src/slices/conversation/components/ChatroomAgentSidebar.vue` | Left panel: bound agents list with live status |
| `src/slices/conversation/components/ChatroomMessageBubble.vue` | Message rendering: user, agent, system variants |
| `src/slices/conversation/components/ChatroomStreamingBubble.vue` | Live agent streaming with cursor animation |
| `src/slices/conversation/components/ChatroomTypingIndicator.vue` | Typing status display with animated dots |
| `src/slices/conversation/components/ChatroomSearchPanel.vue` | Slide-down search with FTS results |
| `src/slices/conversation/components/ChatroomExportModal.vue` | Export config modal with job progress |
| `src/slices/conversation/components/ChatroomApprovalCard.vue` | Inline approval request/resolved card |
| `src/slices/conversation/components/ChatroomNewMessagesPill.vue` | Scroll-to-bottom pill with unread count |
| `src/slices/conversation/components/ChatroomLoadEarlier.vue` | Load-earlier button with auto-trigger on scroll |

### Existing Components to Restyle

| File | Changes |
|------|---------|
| `src/slices/conversation/components/ChatroomComposer.vue` | Restyle: `STextarea`, `SButton`, `SFileUpload`, `SProgressBar`, drag-drop zone |
| `src/slices/conversation/components/ChatroomPresence.vue` | Restyle: `SAvatar`, status indicators, section headers |

### Views to Restyle

| File | Changes |
|------|---------|
| `src/slices/conversation/views/WorkspaceListView.vue` | Restyle: `SPageHeader`, `SCard` grid, `SModal`, `SEmptyState`, `SSearchInput` |
| `src/slices/conversation/views/ChatroomListView.vue` | Restyle: `SBreadcrumb`, `SPageHeader`, `STable`, `SBadge`, `SModal`, `SEmptyState` |
| `src/slices/conversation/views/ChatroomView.vue` | Rearchitect: 3-column grid, extract sub-components, integrate new components |
| `src/slices/conversation/views/ChatroomSettingsView.vue` | Restyle: `SCard` sections, `SFormField`, `SToggle`, `STable`, `SConfirmDialog` |
| `src/slices/conversation/views/GuestLandingView.vue` | Restyle: AuthLayout card, `SFormField`, `SInput`, `SButton`, `SAlert` |

### Supporting Files

| File | Description |
|------|-------------|
| `src/slices/conversation/locales/en.json` | Add keys for new component labels, placeholders, status text |
| `src/slices/conversation/locales/zh-TW.json` | Corresponding Traditional Chinese translations |
| `src/slices/conversation/composables/useChatroomScroll.ts` | New: scroll position tracking, auto-scroll logic, new-messages detection |
| `src/slices/conversation/composables/useChatroomAttachments.ts` | New: file upload orchestration (single-shot + tus), progress tracking |

### Component Dependency Map

```
ChatroomView.vue
  ├── ChatroomHeader.vue
  │     └── SBadge (connection pill), SButton (actions), SDropdown (mobile overflow)
  ├── ChatroomAgentSidebar.vue
  │     └── SAvatar, SDivider
  ├── ChatroomMessageBubble.vue (v-for messages)
  │     └── SAvatar, SButton (hover actions), STextarea (edit mode), STooltip
  ├── ChatroomStreamingBubble.vue (v-for active streams)
  │     └── SAvatar
  ├── ChatroomApprovalCard.vue (v-for live approvals)
  │     └── SButton, SConfirmDialog (reject)
  ├── ChatroomTypingIndicator.vue
  ├── ChatroomComposer.vue
  │     └── STextarea, SButton, SFileUpload, SProgressBar
  ├── ChatroomPresence.vue
  │     └── SAvatar, SDivider
  ├── ChatroomSearchPanel.vue (conditional)
  │     └── SSearchInput, SLoadingSpinner, SAlert
  ├── ChatroomExportModal.vue (conditional)
  │     └── SModal, SFormField, SSelect, SButton, SProgressBar, SAlert
  ├── ChatroomNewMessagesPill.vue (conditional)
  │     └── SBadge
  └── ChatroomLoadEarlier.vue (conditional)
        └── SButton, SLoadingSpinner

ChatroomSettingsView.vue
  ├── SPageHeader, SBreadcrumb, SCard, SDivider
  ├── SFormField, SInput, SToggle, SSelect, SButton, STable
  ├── SConfirmDialog, SAlert
  ├── WakeupConfigEditor (from workflow slice)
  └── DlqViewer (from workflow slice)
```

### Keyboard Shortcuts

| Context | Key | Action |
|---------|-----|--------|
| ChatroomView | `Ctrl+K` / `Cmd+K` | Open search panel |
| ChatroomView | `Escape` | Close search panel / close drawer / cancel edit |
| Composer | `Enter` | Send message |
| Composer | `Shift+Enter` | Insert newline |
| Composer | `Escape` | Clear draft |
| Edit mode | `Ctrl+Enter` | Save edit |
| Edit mode | `Escape` | Cancel edit |
| Export modal | `Escape` | Close modal |

### WebSocket Events (Reference)

| Event | Direction | Handler |
|-------|-----------|---------|
| `message.created` | Server -> Client | Add to TanStack Query cache, trigger auto-scroll |
| `message.updated` | Server -> Client | Refresh message in cache |
| `message.deleted` | Server -> Client | Remove message from cache |
| `agent.thinking` | Server -> Client | Set thinking flag in store, show thinking bubble |
| `agent.token` | Server -> Client | Append to stream buffer, render streaming bubble |
| `agent.finished` | Server -> Client | Clear stream/thinking, real message takes over |
| `presence.joined` | Server -> Client | Add user to presence set |
| `presence.left` | Server -> Client | Remove user from presence set |
| `approval.requested` | Server -> Client | Add approval card to orchestration store |
| `approval.resolved` | Server -> Client | Update approval card status |
| `typing.start` | Client -> Server | Sent on first keystroke (debounced) |
| `typing.stop` | Client -> Server | Sent 3s after last keystroke |
| `workflow.state_changed` | Server -> Client | Update workflow status indicators |
