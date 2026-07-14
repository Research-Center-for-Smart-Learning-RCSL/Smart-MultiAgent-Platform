---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.02, R11.08]
---

# F-3: Every-N Concept Map triggers resolve deletion candidates instead of room coverage

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-3).

## 1. Summary

On each chat message, Concept Map (GraphRAG) `every_n_messages` trigger evaluation resolves
the set of configs to consider by calling `GraphRagConfigRepository.list_for_agents` — a
repository method built for **Agent deletion**. That selector only matches
`owner_kind="agent_group"` configs and, by design for deletion, excludes a shared group
whose owning group still has any other live member. As a result: (1) chatroom-owned and
workspace-owned Concept Maps configured with `every_n_messages` never increment their
message counter and never build; and (2) a shared A+B agent-group map is omitted for a
room containing A whenever B remains a live member elsewhere. The message-driven build
feature is effectively inert for every typed-owner mode except single-member agent groups.

## 2. Observed vs Expected

- **Observed**: `evaluate_graphrag_message_triggers`
  (`backend/contexts/knowledge/application/graphrag_triggers.py:65-97`) receives only
  `agent_ids` and selects configs via
  `GraphRagConfigRepository(db).list_for_agents(unique_agent_ids)` (`:75`). Because the
  counter `increment` happens inside the per-config loop (`:83`), a config never returned
  is never advanced. `list_for_agents`
  (`backend/contexts/knowledge/infrastructure/graphrag_repositories.py:237-297`) joins on
  `owner_agent_group_id` (`:259-261`) — structurally excluding `chatroom`/`workspace`
  owners — and applies `NOT EXISTS(other_live_member)` (`:262-274,284`), which returns a
  group config only when deleting `agent_ids` would empty the group. The dispatcher holds
  `chatroom_id` but drops it before evaluation, using it only in a log line
  (`backend/app/api/v1/messages.py:300-329`, evaluator call `:313`, log `:325`).
- **Expected**: message-count triggers fire for **every Concept Map whose typed owner
  covers the current room** — the same coverage the retrieval path resolves via
  `list_layers_for_turn` (`graphrag_repositories.py:299-372`): the chatroom-owned config
  for the room, `agent_group`-owned configs whose live members include a bound agent
  (when `concept_map_enabled`), and the `workspace`-owned config for the room's workspace
  (when `concept_map_enabled`). Intent: [R11.02] (message-count trigger), [R11.08] (typed
  owner coverage).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Package with F-1/F-2? | Separate dossier | Different batch (trigger correctness, not a release-blocking security fix). |
| Q-2 | Fire auto-builds for maps whose retrieval is disabled? | Mirror retrieval coverage | Resolve trigger-eligible configs exactly like `list_layers_for_turn`: chatroom-owned always; `agent_group`/`workspace` only when `concept_map_enabled`. Don't build a graph no turn will read; keeps trigger and retrieval semantics identical and prevents wasted builds. |

## 4. Reproduction

1. **Chatroom/workspace owner**: create a Concept Map with `owner_kind="chatroom"` (or
   `"workspace"`) and `trigger_config.every_n_messages=1`. Post messages in the room. The
   Redis counter `graphrag:msg_count:{config_id}` never increments and no build is queued,
   because `list_for_agents` never returns the config.
2. **Shared group**: create an `agent_group`-owned map for a group with live members A and
   B, `every_n_messages=1`. In a room where A is bound, post messages. The map is omitted
   because B is still a live member elsewhere, so `NOT EXISTS(other_live_member)` is false.

## 5. Root Cause Analysis

The root cause is the **wrong selector**: the trigger path reuses the deletion-cascade
enumerator `list_for_agents` (`graphrag_repositories.py:237-297`) as if it meant "configs
covering these agents' room." Two properties of that selector, correct for deletion, are
wrong for triggers:
- Join anchored on `owner_agent_group_id` (`:259-261`) -> excludes `chatroom`/`workspace`.
- `NOT EXISTS(other_live_member)` (`:262-274`) -> excludes shared groups with survivors.

The dispatcher already possesses the missing scope (`chatroom_id`, `bound_agent_ids` at
`messages.py:229-232`) but discards `chatroom_id` before the evaluator
(`facade.evaluate_graphrag_message_triggers` takes only `agent_ids`,
`backend/contexts/knowledge/interfaces/facade.py:192-202`). The correct query shape exists
(`list_layers_for_turn`, `graphrag_repositories.py:299-372`) but is keyed on a single
`agent_id` and enable-gates all layers including — for the trigger path we must not
enable-gate the chatroom layer (retrieval doesn't either, `:325-331`).

## 6. Blast Radius and Sibling Suspects

- **Blast radius**: all `chatroom`- and `workspace`-owned Concept Maps using
  `every_n_messages`, plus every multi-member `agent_group` map — i.e. automatic
  message-count builds are broken for all typed-owner modes except single-member groups.
  `silence_minutes` triggers are a separate defect (F-4) and out of scope.
- **Sibling suspects:**
  - **Knowledge-Map triggers** (`knowmap_triggers.py`): different mechanism (corpus
    revision, F-12), not owner-coverage — CLEARED for F-3.
  - **Retrieval coverage** (`list_layers_for_turn`): CLEARED — already resolves owners
    correctly; it is the model to mirror, not a defect.
  - **`list_for_owner`** (`graphrag_repositories.py:205-235`): correct for owner-delete
    cascade; not misused elsewhere.

## 7. Fix Design

Resolve trigger-eligible configs by **room coverage**, not deletion candidacy.

- **New repository selector** on `GraphRagConfigRepository`, e.g.
  `list_message_trigger_configs_for_room(*, chatroom_id, agent_ids)` returning every
  covering config once, mirroring `list_layers_for_turn`'s owner logic
  (`graphrag_repositories.py:299-372`) but for the trigger path:
  - `chatroom` owner where `owner_chatroom_id == chatroom_id` — always included (retrieval
    does not enable-gate this layer, `:325-331`).
  - `agent_group` owner joined via `agent_group_members.agent_id IN agent_ids` with
    `concept_map_enabled IS TRUE` — includes shared groups with survivors (drop the
    `NOT EXISTS(other_live_member)` deletion predicate entirely).
  - `workspace` owner where `owner_workspace_id == (room's workspace)` with
    `concept_map_enabled IS TRUE` — reuse the `room_workspace` scalar subquery pattern
    (`:320-324`).
  - Dedupe (a config reachable via two agents appears once) — reuse the Python dedupe at
    `:363-372`.
- **Thread `chatroom_id` through**: add it to the facade method
  (`facade.py:192-202`), the application function `evaluate_graphrag_message_triggers`
  (`graphrag_triggers.py:65-70`), and the dispatcher call
  (`messages.py:313`, which already has `chatroom_id` in scope at `:300-304`). Replace the
  `list_for_agents` call (`graphrag_triggers.py:75`) with the new selector. `every_n`
  parse/increment/fire logic (`:79-95`) is unchanged — only the config set changes.

**Gating decision (Q-2):** the new selector's enable-gating exactly mirrors retrieval
coverage, so a map builds on message count iff a turn in that room could retrieve it.

**Reuse inventory:**
- `list_layers_for_turn` (`graphrag_repositories.py:299-372`) — copy its owner-layer logic
  and `room_workspace` subquery (`:320-324`); drop only the single-`agent_id` keying and
  the chatroom-layer enable exemption already matches.
- `_OWNER_COLUMN` / `_LAYER_RANK` (`graphrag_repositories.py:119-123,125-126`) — owner
  column map and precedence, reuse for dedupe ordering.
- Redis counter helpers `GraphRagMessageCounter.increment` (`graphrag_triggers.py:56-62`)
  and job-id dedupe (`:85-95`) — unchanged.

**Patterns to follow (SoC):** owner resolution stays in the repository/infrastructure
layer; the application trigger function orchestrates; the API dispatcher only supplies the
room + bound agents. Do not resolve owners in `messages.py`.

**Data repair:** none — no persisted state is wrong; counters simply resume incrementing
for the now-covered configs on the next message.

## 8. Regression Test Plan

Failing-first tests (fail against current code, pass after):

1. **Chatroom owner increments/builds** — with a `chatroom`-owned config,
   `every_n_messages=1`, evaluating a room message queues a build. Fails today
   (`list_for_agents` never returns it).
2. **Workspace owner increments/builds** — same for a `workspace`-owned, enabled config.
   Fails today.
3. **Shared group not excluded** — an `agent_group`-owned map for a group with live members
   A and B fires in a room where A is bound while B stays live. Fails today (deletion
   predicate excludes it).
4. **Disabled wide map skipped (Q-2)** — an `agent_group`/`workspace` map with
   `concept_map_enabled=false` does NOT fire; a chatroom-owned map fires regardless of any
   enable flag. Guards the gating decision.
5. **Single-member group parity** — the previously-working single-member-group case still
   fires (no regression).

## 9. Risks and Rollback

- **Risk**: broadening the trigger set increases background build volume (previously most
  configs never fired). Mirroring retrieval coverage bounds it to configs a turn could
  read; existing per-config `every_n` and job-id dedupe throttle further.
- **Risk**: the new query must dedupe correctly or a config counts twice per message.
  Covered by test 5 + the reused dedupe.
- **Rollback**: revert the commits; the new selector is additive and `list_for_agents`
  remains for its deletion callers, so rollback is clean with no migration.

## 10. Acceptance Criteria

- [x] AC-1: The five regression tests in §8 fail before the fix and pass after. Realized as
  evaluator-level unit tests (`test_graphrag_triggers.py`, run: red→green) plus
  selector-level wiring tests (`test_graphrag_owner_resolution.py`, written; not run locally
  — see D-1).
- [x] AC-2: `every_n_messages` triggers fire for `chatroom`- and `workspace`-owned Concept
  Maps covering the room (workspace gated on `concept_map_enabled`). Unit:
  `test_chatroom_and_workspace_owned_configs_fire`; selector:
  `test_message_trigger_configs_for_room_cover_all_typed_owners`.
- [x] AC-3: Shared multi-member `agent_group` maps fire when any bound agent is a live
  member, regardless of other surviving members. Selector drops the
  `NOT EXISTS(other_live_member)` predicate; wiring
  `test_message_trigger_configs_for_room_cover_all_typed_owners` asserts inclusion with a
  live sibling member.
- [x] AC-4: `agent_group`/`workspace` maps with `concept_map_enabled=false` do not fire;
  chatroom-owned maps fire independent of enable flags — matching retrieval coverage. Wiring
  `test_message_trigger_configs_gate_wide_owners_on_enable`.
- [x] AC-5: `list_for_agents` remains unchanged and still serves the agent-delete cascade.
  Method untouched; existing wiring tests (`test_list_for_agents_*`) still cover it.
- [x] AC-6: Trigger-eligible configs match `list_layers_for_turn` coverage for the same
  room/agents (parity check between build triggering and retrieval). Asserted in the wiring
  selector test as the union of `list_layers_for_turn` over the room's bound agents.

## 11. SRS Delta

None — restores [R11.02]/[R11.08] typed-owner message-trigger coverage.

## 12. Deviation Log

- **D-1 (test execution environment):** the build environment has no
  Postgres/Redis/Neo4j/Qdrant, so the `wiring`/`integration` tiers cannot run locally. The
  §8 selector tests were written and reviewed against the proven
  `test_list_layers_for_turn_orders_and_gates_layers` pattern but executed only in the
  `backend-wiring` CI job, not on this machine. The evaluator-level unit tests
  (`test_graphrag_triggers.py`, `test_message_wakeup_dispatch.py`) were run red→green
  locally. No behavioral deviation from the approved design.
- **D-2 (single-member group gating):** per Q-2, the new selector enable-gates
  `agent_group` coverage on `concept_map_enabled`. A single-member group map created with
  the default `concept_map_enabled=false` therefore no longer fires message-count builds
  until enabled — a deliberate consequence of mirroring retrieval coverage (the old
  `list_for_agents` path ignored the flag). Documented, not a defect.

## 13. Follow-ups

- **FU-1 (F-4, separate)**: `silence_minutes` triggers have no evaluator at all; the
  message-count fix does not address them.
- **FU-2**: consider whether `list_layers_for_turn` and the new trigger selector should
  share a single owner-coverage core to prevent future drift between "what builds" and
  "what is retrieved".
