---
type: bugfix
status: implemented
created: 2026-07-17
requirements: [R11.02, R11.08]
---

# Agentless rooms suppress Concept Map automatic triggers

## 1. Summary

This dossier remediates F-1 from
`docs/audits/2026-07-17-rag-graphrag-remediation-verification/findings.md`.
Messages in rooms with no bound Agent currently skip Concept Map trigger evaluation, so
chatroom-owned and enabled workspace-owned maps neither count messages nor advance their
silence clocks (`backend/app/api/v1/messages.py:291-305`; `backend/contexts/knowledge/application/graphrag_triggers.py:103-131`).

- **Goal:** make room identity, not a non-empty Agent list, the trigger-coverage authority.
- **Non-goals:** change retrieval ACLs, enable disabled wide-owner maps, alter manual builds,
  or change trigger thresholds/job-id semantics.

## 2. Observed vs Expected

- **Observed:** `_dispatch_graphrag_builds` returns for an empty binding list and the
  application evaluator independently returns for empty `agent_ids`
  (`backend/app/api/v1/messages.py:301-305`;
  `backend/contexts/knowledge/application/graphrag_triggers.py:111-123`). The agent-reply
  path repeats the same premise (`backend/contexts/agents/application/runtime/turn_engine.py:1713-1716`).
- **Expected:** [R11.02] counts all messages in a covered room and supports both message and
  silence triggers (`REQUIREMENTS.md:447-450`); [R11.08] scopes chatroom and workspace
  owners independently of Agent presence (`REQUIREMENTS.md:483-484`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Patch empty-list guards or remove Agent lists from the contract? | Make `chatroom_id` authoritative and derive group membership inside the repository selector. | Only the group union arm needs Agent membership; the chatroom/workspace arms already do not (`backend/contexts/knowledge/infrastructure/graphrag_repositories.py:337-373`). This also stops conflating a binding-fetch failure with a valid empty room. |
| Q-2 | Should agentless activity start silence timers? | Yes, for the chatroom map and enabled workspace map. | A never-touched clock deliberately never fires (`backend/contexts/knowledge/application/graphrag_triggers.py:155-191`), so recording the message is required by [R11.02]. |

## 4. Reproduction

1. Create a chatroom-owned Concept Map and an enabled workspace-owned Concept Map with
   `every_n_messages=1` or `silence_minutes`; typed-owner creation requires no Agent
   (`backend/app/api/v1/graphrag.py:255-279`).
2. Leave the room with no bound Agents and persist a user message.
3. Observe that dispatch returns at `backend/app/api/v1/messages.py:301-302`; no counter or
   silence timestamp is touched.
4. Wait past `silence_minutes`; the sweep receives no activity timestamp and does not fire
   (`backend/app/workers/tasks/graphrag.py:441-466`).

## 5. Root Cause Analysis

The original Agent-owned model survived as three empty-binding guards in the API,
application evaluator, and agent-reply path. The current selector is a room-coverage query:
its chatroom and workspace arms do not depend on Agent ids, while only the group arm does
(`backend/contexts/knowledge/infrastructure/graphrag_repositories.py:299-384`). Treating
`agent_ids` as a precondition therefore prevents the correct selector from running. The
earliest corrective point is the selector contract: derive room bindings there and always
evaluate a persisted message by `chatroom_id`.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** all agentless rooms with chatroom-owned or enabled workspace-owned maps,
  for both message-count and silence triggers. Group maps correctly remain absent without a
  bound live member.
- **Confirmed sibling:** the agent-reply dispatcher suppresses evaluation when its current
  binding lookup is empty (`backend/contexts/agents/application/runtime/turn_engine.py:1713-1716`).
- **Cleared:** the owner selector can return agentless chatroom/workspace layers
  (`backend/contexts/knowledge/infrastructure/graphrag_repositories.py:337-373`), and the
  build worker scopes deltas by typed owner rather than Agent presence
  (`backend/app/workers/tasks/graphrag.py:206-244`).
- **Existing debt:** trigger and retrieval coverage remain separate queries; the prior
  dossier already records their drift risk
  (`docs/tasks/2026-07-14-concept-map-message-trigger-coverage/spec.md:209-215`).

## 7. Fix Design

1. Change the trigger repository port/implementation to accept `chatroom_id` only. Join the
   room's live Agent bindings inside the group arm; keep the chatroom and enabled-workspace
   arms independent.
2. Remove the empty-binding exits from user-message, application-evaluator, and agent-reply
   dispatch. A committed message always reaches room-scoped evaluation.
3. Keep owner/project predicates, `concept_map_enabled`, soft-delete filters, layer dedupe,
   counter/clock behavior, stable job ids, and best-effort post-commit dispatch unchanged.
4. Keep layer responsibilities intact: route/runtime dispatch, application counters/clocks,
   and infrastructure owner-resolution SQL.

Reuse the existing union/dedupe query
(`backend/contexts/knowledge/infrastructure/graphrag_repositories.py:330-384`), counter and
clock adapters (`backend/contexts/knowledge/application/graphrag_triggers.py:53-100`), and
stable job id (`backend/contexts/knowledge/application/graphrag_triggers.py:26-43`).

### Security Considerations

The query must remain scoped to the supplied room and its project; enabled-wide-owner gates
must not be weakened. This change authorizes no read and exposes no new graph data. Trigger
thresholds and stable Arq ids remain the resource-amplification controls.

## 8. Regression Test Plan

1. In `backend/tests/unit/test_message_wakeup_dispatch.py`, pass an empty binding set and
   assert the facade is called and returned triggers are enqueued.
2. In `backend/tests/unit/test_graphrag_triggers.py`, verify an agentless chatroom/workspace
   selection increments `every_n_messages` and touches declared silence clocks.
3. In `backend/tests/wiring/test_graphrag_owner_resolution.py`, verify a real agentless room
   returns its chatroom map plus enabled workspace map exactly once, excluding group and
   disabled workspace maps.
4. Add an agent-reply race characterization that an empty current binding set does not skip
   room-level trigger evaluation.

Tests 1 and 2 fail against the guards at `backend/app/api/v1/messages.py:301-302` and
`backend/contexts/knowledge/application/graphrag_triggers.py:111-113`.

## 9. Risks and Rollback

Previously inert rooms will begin producing the intended background builds. Query cost stays
bounded to one room-coverage query per persisted message. Rollback is code-only; no schema or
data migration is required, but rollback restores the defect.

## 10. Acceptance Criteria

- [x] AC-1: The regression tests in section 8 fail before the fix and pass after.
  Verified: both failed for the documented reason before the fix — the facade was never
  called (`messages.py:301` guard) and the evaluator returned `[]`
  (`graphrag_triggers.py:113` guard).
- [x] AC-2: `every_n_messages=1` fires for chatroom and enabled workspace maps in an
  agentless room; no group or disabled workspace map fires.
  Verified: `test_agentless_room_evaluates_chatroom_and_workspace_maps` (unit) and
  `test_message_trigger_configs_cover_agentless_room` (wiring, real Postgres — the
  enabled group map is excluded because no member is bound to the room).
- [x] AC-3: Agentless activity advances eligible silence clocks and produces exactly one
  threshold build per idle cycle using the existing stable job id.
  Verified: the unit test asserts the silence-declaring map's clock is touched and the
  every_n-only map's is not; the `_BUILDABLE_STATES` gate plus freshness gate plus stable
  job id are untouched (`graphrag_triggers.py:136-152,157-201`).
- [x] AC-4: User-message and agent-reply paths never suppress room-level Concept Map
  evaluation solely because the binding set is empty; binding-fetch failure remains
  distinguishable from an empty set.
  Verified structurally rather than by guard-patching: `agent_ids` is gone from the
  facade, so no caller can suppress. `messages.py:220-221` still distinguishes
  fetch-failure (`None`) from empty (`[]`) for the wake-up path, and the Concept Map
  path no longer consumes either. Agent-reply covered by
  `tests/unit/test_agent_reply_graphrag_dispatch.py`.
- [x] AC-5: Existing non-empty group coverage, project/room scoping, enablement, soft-delete
  filtering, layer dedupe, and manual builds are unchanged.
  Verified: the group arm's `chatroom_agents` join is logically identical to the
  caller-supplied `list_bound_agents` set it replaces (see D-1); all four pre-existing
  message-trigger wiring tests pass unmodified in assertion.
- [x] AC-6: Focused unit and wiring tests, backend lint, format, and type checks pass.
  Verified: `pytest -q -m "not wiring"` 4947 passed; wiring tier on a fresh DB 42 passed.
  `ruff check` + `ruff format --check` clean; `mypy .` clean over 764 files. Remaining
  failures in both tiers are pre-existing and unrelated — see FU-3/FU-4/FU-5.

## 11. SRS Delta

None. This restores [R11.02] and [R11.08].

## 12. Deviation Log

- **D-1: "live Agent bindings" (§7.1) resolved as "current binding rows", without an
  `agents.deleted_at` filter.** The spec's wording was ambiguous once the join moved into
  the repository. `ChatroomAgentRepository.list`
  (`backend/contexts/conversation/infrastructure/repositories/chatroom_repo.py:266-281`)
  does not filter soft-deleted agents, so the caller previously passed their bindings in;
  reproducing that exactly is what makes AC-5's "group coverage unchanged" provably true
  and keeps this a pure bugfix. Adding the filter would have narrowed group coverage (a
  shared group whose only room-bound member is soft-deleted would stop firing) — a
  behavior change outside this dossier's non-goals. Decided with the user before
  implementation; recorded as FU-2 rather than actioned.
- **D-2: `agent_ids` removed from the facade and both dispatchers, not just guarded.**
  §7.2 said to remove the empty-binding exits. Removing the parameter outright was
  necessary to satisfy AC-4 structurally: `messages.py:220-221` distinguishes a failed
  binding fetch (`None`) from a genuinely empty room (`[]`), and the old
  `if not bound_agent_ids: return` conflated them. With no binding argument, the Concept
  Map path cannot conflate what it never receives. Consistent with Q-1's intent.
- **D-3: existing wiring fixtures now bind their agents to the room.** The four
  pre-existing `list_message_trigger_configs_for_room` tests passed `agent_ids` without
  ever creating a `chatroom_agents` row — reachable only because the selector trusted the
  caller's list. Now that it derives bindings itself, the fixtures must reflect what
  production always did. Assertions are unchanged; only the setup gained
  `ChatroomAgentRepository.add`. This makes the tests more faithful, not weaker.

## 13. Follow-ups

- FU-1: Unify retrieval and trigger owner-coverage construction if a later refactor can do
  so without coupling runtime ranking to trigger enumeration.
- FU-2: Decide whether a soft-deleted Agent's surviving room binding should count as
  Concept Map coverage. Today it does, in both the trigger selector and
  `ChatroomAgentRepository.list` — see D-1. The narrow case that differs: a shared,
  multi-member group whose only room-bound member is soft-deleted while the group's map
  survives via other live members. Fixing it belongs with the binding-liveness contract,
  not this dossier.
- FU-3: `tests/wiring/test_graphrag_owner_resolution.py:582-660`
  (`test_list_silence_trigger_configs_scopes_by_owner`) asserts an exact list from the
  *global* silence feed, so any `silence_minutes` config left in the shared test DB by a
  prior run fails it. Confirmed pre-existing: it fails alone against a dirty DB and passes
  on a fresh one, with no test from this task running. Scope the assertion to the
  configs the test created.
- FU-4: `alembic upgrade head` cannot run against an empty database — revision id
  `0032_audit_retention_delete_grant` is 33 characters and `alembic_version.version_num`
  is `VARCHAR(32)`, so migration 0032 dies with `StringDataRightTruncation`. Existing
  databases predate the long id and are unaffected, which is why CI has not caught it;
  any genuinely fresh environment is blocked. Found while rebuilding the local test DB.
- FU-5: Two wiring tests fail on a fresh DB independently of this change —
  `test_account_self_delete.py::test_self_delete_cascades_projects_and_memberships` and
  `test_graphrag_owner_resolution.py::test_recreate_after_soft_delete_does_not_collide_on_owner_index`
  (the latter inserts `owner_kind='chatroom'` with all owner columns null, violating
  `ck_graphrag_configs_owner`). Both are create/delete-path issues; this diff touches no
  INSERT and no owner columns.
- FU-6: `evaluate_graphrag_message_triggers` instantiates the concrete
  `GraphRagConfigRepository` from the application layer
  (`backend/contexts/knowledge/application/graphrag_triggers.py:14`) instead of depending
  on the `GraphRagConfigRepositoryPort` that already exists
  (`backend/contexts/knowledge/application/graphrag_ports.py:253`) and that the builder,
  reconciler, and retriever already use. Pre-existing; this change shrank the coupling's
  surface but did not remove it.
