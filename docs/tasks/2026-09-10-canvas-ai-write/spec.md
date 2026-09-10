---
type: feature
status: in-progress
created: 2026-09-10
requirements: [R13.42, R13.44, R13.47]
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Phase 3 -- AI Writes to Canvas

## 1. Summary

Allow AI agents bound to a chatroom to create, update, and delete canvas objects through
a set of built-in runtime tools (`canvas_create_object`, `canvas_update_object`,
`canvas_delete_object`), gated by a new `may_write_canvas` grant on the chatroom
binding. Agent-created objects carry the agent's identity (`created_by_agent_id`) and
are visually distinguished in the UI. The write path uses the existing REST-layer
`CanvasFacade` operations; if Phase 2 CRDT is active, a server-side adapter pushes the
resulting object into the Yjs document so live editors see the change in real time.

This is Phase 3 of a three-phase feature. Phase 1 (snapshot-based) is implemented.
Phase 2 (CRDT sync, `2026-09-10-canvas-crdt-sync`) is approved but not required for
Phase 3 -- AI writes work through the REST object CRUD regardless of whether CRDT is
deployed.

## 2. Goals and Non-goals

**Goals**

- New `may_write_canvas` grant on `ChatroomAgent`, independent of `may_read_canvas`.
  A room creator grants or revokes it per agent binding.
- Three built-in runtime tools: `canvas_create_object`, `canvas_update_object`,
  `canvas_delete_object`, following the grant-sourced pattern used by
  `start_activity`/`end_activity` (`builtin_tools.py:958-970`) and `read_drafts`
  (`builtin_tools.py:985-989`).
- Agent-created canvas objects carry `created_by_agent_id` (new FK column on
  `canvas_objects`) and are visually distinct in the frontend (agent avatar or color
  badge on the object).
- Canvas events from agent writes use the same event types as user writes
  (`canvas.object_created`, `canvas.object_updated`, `canvas.object_deleted`), with an
  additional `agent_id` field in the payload.
- Audit events for every agent canvas write, identifying the agent and the acting user
  (the user whose turn triggered the agent).
- UI settings panel exposes the `may_write_canvas` toggle alongside `may_read_canvas`
  in `AgentCanvasAccess.vue`.

**Non-goals**

- Agent reading canvas content through tools (already handled by `CanvasContextProvider`
  and the `may_read_canvas` grant in Phase 1).
- Agent-initiated canvas creation or deletion (canvas lifecycle stays user-controlled).
- Agent image uploads to canvas (image upload requires a file, which agents do not
  produce in this phase).
- Natural-language canvas commands (the tools accept structured object definitions, not
  prose descriptions).
- Agent awareness/cursors on the canvas (agents are not WebSocket participants).

## 3. Clarifications

- **Q-1**: REST or CRDT write path? **REST first, CRDT adapter later.** AI writes go
  through `CanvasFacade.create_object()` / `update_object()` / `delete_object()`. If
  Phase 2 CRDT is deployed, a post-write hook in `CanvasService` pushes the mutation into
  the in-memory Yjs document (via `CrdtRelay.apply_mutation()`) so live CRDT editors see
  it immediately. Without Phase 2, changes flow through the existing WS event
  notification path.

- **Q-2**: Separate grant or reuse `may_read_canvas`? **New `may_write_canvas` grant.**
  Read and write are independent permissions. An agent can read the canvas digest without
  being able to modify it, and vice versa (though write-without-read is an unusual
  configuration, it is not prohibited).

- **Q-3**: Tool scope? **Full CRUD.** Three tools: `canvas_create_object` (create one
  object), `canvas_update_object` (update one object by ID), `canvas_delete_object`
  (delete one object by ID). A `batch_operate` tool is deferred -- single-object tools
  are simpler for the model to use and easier to audit.

- **Q-4**: Should agents update or delete objects they did not create? **Yes.** The
  `may_write_canvas` grant authorizes full CRUD on all objects in the canvas, not just
  agent-owned objects. This matches the user model (any user who can send messages can
  edit any object).

- **Q-5**: Tool input format? **Structured object definitions.** The `canvas_create_object`
  tool accepts `kind`, `position_x`, `position_y`, `width`, `height`, `content`, `style`
  as a JSON schema. This is the same shape as `CanvasObjectIn` in the REST API. The model
  decides what to place and where; the platform does not interpret natural language.

## 4. Requirements

### 4.1 Existing requirements touched

- **[R13.44]** (extended): Canvas write access for AI agents is gated by a separate
  `may_write_canvas` grant, independent of the chatroom-level send permission.

- **[R13.47]** (unchanged): Agent canvas reading remains gated by `may_read_canvas` and
  `expose_to_agents`. The write grant does not imply read access.

### 4.2 New requirements (SRS Delta)

- **[R13.56]** A room creator may grant an agent bound to the chatroom the ability to
  create, update, and delete canvas objects, via a `may_write_canvas` grant on the
  chatroom binding. The grant is independent of `may_read_canvas` and records the
  granting user. Revoking the grant does not delete objects the agent previously created.

- **[R13.57]** An agent with `may_write_canvas` exercises the grant through three
  built-in runtime tools: `canvas_create_object`, `canvas_update_object`, and
  `canvas_delete_object`. Each tool accepts a structured JSON input matching the canvas
  object schema. The tools are available only during agent turns in rooms where the grant
  is set; they are absent from the tool list otherwise. Each tool invocation emits an
  audit event identifying the agent and the canvas operation.

- **[R13.58]** A canvas object created by an agent carries `created_by_agent_id` (the
  agent's identity) alongside the existing `created_by_user_id` (the user whose turn
  triggered the write). The frontend visually distinguishes agent-created objects from
  user-created ones.

## 5. Acceptance Criteria

- [ ] AC-1: An agent with `may_write_canvas` can create a canvas object via
  `canvas_create_object`. The object appears on the canvas for all connected users.
- [ ] AC-2: An agent with `may_write_canvas` can update an existing canvas object via
  `canvas_update_object` (change position, content, style).
- [ ] AC-3: An agent with `may_write_canvas` can delete a canvas object via
  `canvas_delete_object`.
- [ ] AC-4: The tools are absent from the tool list for agents without the grant.
  Verified by checking tool registration output.
- [ ] AC-5: Agent-created objects carry `created_by_agent_id` and are visually
  distinguished in the UI (agent avatar or color badge).
- [ ] AC-6: Canvas writes by agents emit audit events with `agent_id` and `action`
  fields (`canvas.agent_object_created`, `canvas.agent_object_updated`,
  `canvas.agent_object_deleted`).
- [ ] AC-7: The `may_write_canvas` toggle appears in the chatroom settings alongside
  `may_read_canvas`. Granting and revoking works correctly.
- [ ] AC-8: The canvas event payload for agent writes includes an `agent_id` field so
  the frontend can render the agent badge.
- [ ] AC-9: Guest rate limiting ([R13.46]) does not apply to agent writes (agents are
  not guests). Agent writes are bounded by the per-turn tool-round cap instead.

## 6. Detailed Changes

### 6.1 Backend -- Migration

New Alembic migration:
- Add `created_by_agent_id` column to `canvas_objects` table (UUID FK to `agents.id`,
  nullable).
- Add `may_write_canvas` column to `chatroom_agents` table (Boolean, default `False`).

### 6.2 Backend -- Domain model changes

`contexts/conversation/domain/models.py`:
- Add `may_write_canvas: bool = False` to `ChatroomAgent` dataclass.
- Add `CanvasWriteGrant` dataclass (same pattern as `CanvasReadGrant` at line 119).

`contexts/canvas/domain/models.py`:
- Add `created_by_agent_id: uuid.UUID | None = None` to `CanvasObject` dataclass.

### 6.3 Backend -- Repository changes

`contexts/conversation/infrastructure/repositories/chatroom_repo.py`:
- Add `may_write_canvas` to column mapping.
- Add `set_canvas_write_grant()` and `canvas_write_grant()` methods (same pattern as
  `set_canvas_read_grant` / `canvas_read_grant`).

`contexts/canvas/infrastructure/repositories/canvas_repo.py`:
- Map `created_by_agent_id` in `_row_to_object()`.
- Accept `created_by_agent_id` in `create_object()` values.

### 6.4 Backend -- Canvas write tools

New file: `contexts/agents/application/runtime/canvas_tools.py`

- `CanvasWriteContext` dataclass: `chatroom_id`, `canvas_id`, `agent_id`,
  `actor_user_id`, `actor_ip`, `request_id`, `db`.
- `build_canvas_write_tools(db, agent, context) -> list[Tool]`: returns three `Tool`
  objects:
  - `canvas_create_object`: input schema matches `CanvasObjectIn` fields. Invokes
    `CanvasFacade.create_object()` with `created_by_agent_id=agent.id`.
  - `canvas_update_object`: input `{object_id, ...patch fields}`. Invokes
    `CanvasFacade.update_object()`.
  - `canvas_delete_object`: input `{object_id}`. Invokes
    `CanvasFacade.delete_object()`.
  - Each emits an audit event before returning `ToolResult`.

### 6.5 Backend -- Turn engine integration

`contexts/agents/application/runtime/turn_engine.py`:
- Add `canvas_write: CanvasWriteContext | None = None` parameter to
  `build_agent_tools()`.
- Resolve the grant in the turn setup (alongside `_canvas_context`): check
  `ChatroomAgentRepository.canvas_write_grant()`. If granted, build a
  `CanvasWriteContext` and pass to `build_agent_tools()`.

`contexts/agents/application/runtime/builtin_tools.py`:
- Add `canvas_write` parameter to `build_agent_tools()`.
- Add dispatch block after `draft_access` (line 989):
  ```python
  if canvas_write is not None:
      from contexts.agents.application.runtime.canvas_tools import build_canvas_write_tools
      out.extend(build_canvas_write_tools(db, agent=agent, context=canvas_write))
  ```

### 6.6 Backend -- Service and facade

`contexts/canvas/application/canvas_service.py`:
- `create_object()`: accept `created_by_agent_id` parameter, pass through to repo.

`contexts/canvas/interfaces/facade.py`:
- `create_object()`: accept and forward `created_by_agent_id`.

`contexts/conversation/application/chatroom_service.py`:
- Add `set_agent_canvas_write_grant()` (same pattern as `set_agent_canvas_grant`).

`contexts/conversation/interfaces/facade.py`:
- Add `set_agent_canvas_write_grant()`.

### 6.7 Backend -- REST endpoint

`app/api/v1/chatrooms.py`:
- Add `AgentCanvasWriteAccessIn` model and `patch_chatroom_agent_canvas_write_access`
  endpoint.
- Add `may_write_canvas` to `AgentRef` response model.

### 6.8 Frontend -- Agent canvas access UI

`slices/conversation/components/AgentCanvasAccess.vue`:
- Add `may_write_canvas` toggle alongside the existing `may_read_canvas` toggle.

`slices/conversation/api/index.ts`:
- Add `setChatroomAgentCanvasWriteAccess()` API function.
- Add `may_write_canvas` to `BoundAgentRef`.

`slices/conversation/composables/useChatroomBindings.ts`:
- Add `may_write_canvas` to `BoundAgent` and `boundCanvasWriteGrants`.

### 6.9 Frontend -- Visual distinction for agent objects

`slices/canvas/components/CanvasPanel.vue`:
- Agent-created objects display a small agent avatar badge (or colored border) when
  `created_by_agent_id` is set. The exact UI treatment follows the agent avatar pattern
  used in `ChatroomMessageBubble.vue` for agent messages.

### 6.10 Frontend -- i18n

Add keys to `slices/canvas/locales/en.json` and `zh-TW.json`:
- `canvas.agentCreated`, `canvas.mayWriteCanvas`, `canvas.grantWrite`,
  `canvas.revokeWrite`.

## 7. Existing Debt and Patterns

### Patterns to follow

- **Grant-sourced tool**: Follow `draft_access` pattern at `builtin_tools.py:985-989` --
  a context object passed into `build_agent_tools()`, lazily imported tool builder.
- **Grant column**: Follow `may_read_canvas` pattern at
  `conversation/domain/models.py:119` and `chatroom_repo.py:739-780`.
- **Tool shape**: Follow `activity_tools.py:178` -- `Tool` dataclass with `name`,
  `description`, `input_schema`, and `invoke` async callable.
- **Agent identity on objects**: Follow `created_by_user_id` / `created_by_guest_id`
  pattern on `canvas_objects` table.

### Reuse inventory

| What | Where | Use |
|------|-------|-----|
| `build_agent_tools()` | `builtin_tools.py:865` | Tool registration |
| `Tool` dataclass | `builtin_tools.py` | Tool definition |
| `CanvasFacade` | `canvas/interfaces/facade.py:18` | Write operations |
| `ChatroomAgentRepository` | `conversation/infrastructure/repositories/` | Grant check |
| `audit.emit()` | `shared_kernel/audit.py` | Audit trail |
| `AgentCanvasAccess.vue` | `conversation/components/` | UI toggle (extend) |

## 8. Security Considerations

- **Grant isolation**: `may_write_canvas` is per-binding, per-room. An agent granted
  write in Room A has no write access in Room B. Revoking the grant immediately removes
  the tools from subsequent turns.
- **Tool availability**: The tools are assembled at the start of each turn. A revoked
  grant means the tools are absent, not that they check at invocation time -- there is no
  TOCTOU gap within a single turn.
- **Audit trail**: Every agent canvas write emits an audit event with `agent_id`,
  `actor_user_id` (the human whose conversation triggered the agent), and the operation
  details. This provides accountability for agent actions.
- **No prompt injection via canvas**: Agent tools accept structured JSON, not
  natural-language canvas descriptions. The platform does not interpret user-authored
  canvas content as agent instructions (the digest is read-only context, not a tool
  input).
- **Object ownership**: `created_by_agent_id` is set server-side by the tool invocation,
  never by the agent's input. An agent cannot claim a different creator identity.

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Agent floods canvas with objects | Medium | Medium | Per-turn tool-round cap limits total tool calls; future: per-turn object-creation cap |
| Agent creates objects with malicious content | Low | Medium | `content` field is text, rendered through the same sanitization as user content |
| Phase 2 CRDT adapter complexity | Medium | Low | Adapter is optional; without it, REST path + WS events still work |

## 10. Test Plan

### Unit tests

- `test_canvas_write_tools.py`: Tool construction with/without grant, input validation,
  successful create/update/delete, audit event emission.
- `test_canvas_write_grant.py`: Grant set/revoke lifecycle, independence from
  `may_read_canvas`.

### Integration tests (pytest.mark.db)

- `test_canvas_agent_write_e2e.py`: Full turn with canvas write tool invocation, verify
  object created in DB with correct `created_by_agent_id`.

### E2E tests (Playwright)

- Toggle `may_write_canvas` in settings, trigger an agent turn that uses the tool, verify
  the object appears on the canvas with agent badge.

## 11. Open Questions

None -- all questions resolved in Clarifications section.

## 12. Deviation Log

(Populated during implementation.)

## 13. Follow-ups

- FU-1: `canvas_batch_operate` tool for agents (create multiple objects in one call).
- FU-2: Agent image placement tool (agent provides an image URL, platform downloads and
  places it).
- FU-3: Phase 2 CRDT adapter for agent writes (push mutations into Yjs doc).
