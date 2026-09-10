---
type: feature
status: draft
created: 2026-09-10
requirements: []
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Phase 3 -- AI Writes to Canvas

## 1. Summary

Allow AI agents with a `may_write_canvas` grant to add objects to the canvas via a
`write_to_canvas` built-in runtime tool. Agent-created objects are visually
distinguished (agent avatar/color) and emit the same `canvas.object_created` events as
user writes.

## 2. Goals and Non-goals

**Goals**

- New `may_write_canvas` grant on `ChatroomAgent`, independent of `may_read_canvas`.
- `write_to_canvas` built-in tool available during agent turns when the grant is set.
- Agent-created canvas objects carry the agent's identity and are visually distinct.
- Canvas events from agent writes are indistinguishable in shape from user writes
  (same event types, same WS channel).

**Non-goals**

- Agent-initiated canvas deletion or bulk operations (write-only in Phase 3).
- Agent editing existing objects placed by users.
- Agent reading canvas content through the tool (already handled by
  `CanvasContextProvider` in Phase 1).

## 3. Acceptance Criteria

- [ ] AC-1: An agent with `may_write_canvas` grant can add objects to the canvas via a
  `write_to_canvas` built-in tool. (from parent AC-18)
- [ ] AC-2: Canvas writes by agents are visually distinguished (agent avatar/color) and
  emit `canvas.object_created` events like user writes. (from parent AC-19)
- [ ] AC-3: The `write_to_canvas` tool is not available to agents without the grant.
- [ ] AC-4: Agent canvas writes appear in the audit trail with the agent's identity.
- [ ] AC-5: The UI settings panel exposes the `may_write_canvas` toggle alongside
  `may_read_canvas`.

## 4. Key Technical Decisions

- Tool follows the `present_observation` / `read_drafts` pattern: grant-sourced,
  registered in `BUILTIN_TOOL_NAMES`, built by `build_agent_tools`.
- `created_by_agent_id` column or use existing `created_by_user_id` with a sentinel.

## 5. Detailed Changes

_To be filled during `/spec` analysis._

## 6. Test Plan

_To be filled during `/spec` analysis._

## 7. Open Questions

- OQ-1: Should `write_to_canvas` accept structured object definitions (position, size,
  kind) or natural-language descriptions that the platform interprets?
- OQ-2: Should agents be able to update or delete their own previously placed objects?
