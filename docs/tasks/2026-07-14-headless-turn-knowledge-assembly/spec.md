---
type: bugfix
status: in-progress
created: 2026-07-14
requirements: [R10.09, R11.14]
---

# F-15: Headless Agent invocations omit automatic File RAG and all Knowledge Maps

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-15).

## 1. Summary

When an Agent runs inside a chatroom, its turn automatically queries all three knowledge
sources — File RAG, its attached Knowledge Map, and the room's Concept Maps — and prepends the
results as system blocks (`backend/contexts/agents/application/runtime/turn_engine.py:941-950`).
When the *same* Agent is invoked **headless** through `run_input_turn`
(`turn_engine.py:404-462`), none of that runs: the method assembles only the base prompt,
notifications, and tools before streaming. Headless invocations come from A2A
(`backend/contexts/orchestration/application/a2a_handler.py:182-188`) and the approval worker
(`backend/app/workers/tasks/approvals.py:88`). So an Agent whose only answer lives in its
attached Knowledge Map or File RAG corpus answers an A2A `CALL`/`INSTRUCT` or drives an approval
turn **without that knowledge**, contradicting `[R11.14]` ("at agent invocation, an attached
Knowledge Map is queried...") and `[R10.09]`. File RAG and Knowledge Maps are per-Agent bindings
and fully reproducible headless; Concept Maps are room-scoped and only apply when a valid room
context is supplied — which A2A never has and the approval worker does (Q-2).

## 2. Observed vs Expected

- **Observed** — `run_input_turn` (`turn_engine.py:404-412` signature) receives only `agent_id`
  (+ caller/workflow bookkeeping); it has no `chatroom_id`. Before streaming it builds the base
  prompt (`_resolve_prompt`, `:433`), notifications/approval tools (`_pending_context_and_tools`,
  `:435`), and tools (`:436, :441-447`), joins `system_parts` (`:439`), and dispatches
  `_stream_with_tools(..., chatroom_id=None, room=None, ...)` (`:449-459`). It never calls
  `_rag_context`, `_knowmap_context`, or `_graphrag_context`. The room path `_run_locked`
  (`:833-843` signature, mandatory `chatroom_id`) does call all three inline
  (`:941-950`): File RAG `_rag_context(agent, queries)` (`:942`), Concept Maps
  `_graphrag_context(agent, chatroom_id, queries)` (`:945`), Knowledge Maps
  `_knowmap_context(agent, queries)` (`:948`).
- **Expected** — `[R11.14]`: "At agent invocation, an attached Knowledge Map is queried as an
  Axis-1 system block ... beside file-RAG, independent of any Concept Map." `[R10.09]`: retrieved
  RAG chunks are inserted as system-role messages before the turn. Neither restricts this to
  room turns; "agent invocation" includes headless A2A/approval invocations. A headless turn must
  therefore assemble File RAG (`agent.rag_config_id`) and the attached Knowledge Map
  (`agent.knowmap_config_id`) — both per-Agent, needing no room. Concept Maps
  (`[R11.09]`/`[R11.06]`) are strictly room-scoped (`_graphrag_context` requires `chatroom_id`,
  `:1720-1742`, resolving layers via `resolve_graphrag_layers(agent_id, chatroom_id)`,
  `:1733-1735`) and are included only when a valid room context is supplied.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which knowledge sources should headless turns assemble? | File RAG + attached Knowledge Map always; Concept Maps only when a valid `chatroom_id` is supplied. | File RAG and Knowledge Maps are per-Agent bindings on the loaded `agent` (`agent.rag_config_id`, `agent.knowmap_config_id`) and need no room — omitting them is the `[R11.14]`/`[R10.09]` violation. Concept Maps are room-derived and cannot be resolved without a chatroom. |
| Q-2 | Concept Maps for the callers: A2A (no room) and the approval worker (carries a `chatroom_id`)? | **Thread the approval worker's room** into `run_input_turn` so approver turns also get their room's Concept Maps; A2A passes no room and gets File RAG + Knowledge Maps only. | User decision. The approval task already carries an authoritative `chatroom_id` (`approvals.py:35`, currently log-only) identifying the room the vote is threaded back to; that is a legitimate room context for the approver's Concept Map resolution. A2A envelopes have no room at all (`a2a_handler.py:166-188`), so Concept Maps can never apply there. `chatroom_id` stays Optional, so Concept Maps are conditional even for approvals. |
| Q-3 | Extract a shared helper or duplicate the room block? | Extract one shared helper and call it from both `_run_locked` and `run_input_turn`. | The two paths drifting apart is exactly how this defect arose. A single `_assemble_agent_knowledge(agent, queries, *, chatroom_id)` used by both prevents re-divergence; the room path's observable output (same three blocks, same order) must be preserved (characterization, §8.6). |

## 4. Reproduction

**A2A (File RAG + Knowledge Map gap):**
1. Agent A in project P is attached to Knowledge Map M (`agent.knowmap_config_id = M`) that
   contains the sole answer to a question; A has no `file_search` tool enabled.
2. Another Agent sends A an A2A `CALL`/`INSTRUCT` with that question
   (`a2a_handler.py:_run_turn_with_db`, `:166-188`).
3. `run_input_turn` streams A's reply. **Today:** M is never queried (no `_knowmap_context`
   call), no Knowledge Map tool exists, so A answers without the knowledge. **After the fix:** M
   (and A's File RAG, if bound) is queried and prepended as a `type:"graphrag"`/`type:"rag"`
   system block before streaming.

**Approval (adds Concept Maps):**
1. Approver Agent B drives an approval turn parked in chatroom R
   (`approvals.py:drive_approver_turn`, `:31-37`, `:88`).
2. **Today:** `run_input_turn` gets neither B's File RAG/Knowledge Map nor R's Concept Maps.
   **After the fix:** B's File RAG + Knowledge Map are assembled, and — because the task carries
   R's `chatroom_id` — R's Concept Maps are resolved and included.

## 5. Root Cause Analysis

The causal chain:

1. `run_input_turn` (`turn_engine.py:404-462`) omits the knowledge-assembly block that
   `_run_locked` runs inline at `:941-950`. **This is the root cause** — knowledge assembly was
   coded only into the room path, so every headless caller silently loses it.
2. `run_input_turn` has no `chatroom_id` parameter (`:404-412`), so even the room-independent
   File RAG / Knowledge Map queries — which need none — were never wired in, and the room-scoped
   Concept Map query had no room to run against.
3. The knowledge assembly being **inline** in `_run_locked` rather than a shared helper meant
   adding a second entry point (`run_input_turn`) required duplicating it, which never happened —
   an aggravating structural factor.

Correcting (1) via (3) — extract the assembly into a helper and call it from both paths, giving
`run_input_turn` an optional `chatroom_id` — restores `[R11.14]`/`[R10.09]` for headless turns
without duplicating logic.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every A2A `CALL`/`INSTRUCT`, workflow-driven, and approval-Agent turn:
  results diverge from room behavior and silently ignore designer-attached File RAG and Knowledge
  Maps. Approval turns additionally lacked their room's Concept Maps.
- **Sibling suspects:**
  - **File RAG provider** `_rag_context` → `RagContextProvider.query(rag_config_id, query_texts, agent_id)`
    (`turn_engine.py:1712-1718`; `backend/contexts/knowledge/application/rag_context_provider.py:62-158`):
    per-Agent, no room — CONFIRMED reproducible headless.
  - **Knowledge Map provider** `_knowmap_context` →
    `KnowledgeMapContextProvider.query(knowmap_config_id, query_texts, querying_agent_id)`
    (`turn_engine.py:1744-1755`): per-Agent (`agent.knowmap_config_id`), no room — CONFIRMED
    reproducible headless.
  - **Concept Map provider** `_graphrag_context` (`turn_engine.py:1720-1742`): requires
    `chatroom_id` and resolves layers via `resolve_graphrag_layers(agent_id, chatroom_id)`
    (`:1733-1735`) — CONFIRMED room-scoped; must be gated on a real `chatroom_id`.
  - **Query builder** `_knowledge_queries(history, *, input_text)` (`turn_engine.py:1794`):
    degrades cleanly on empty history (`:1796` uses `input_text` as current), so
    `run_input_turn` (no history) can call it with `history=[]` — CLEARED.
  - **A2A caller** `a2a_handler.py:166-188`: no `chatroom_id` available in the envelope — passes
    `None`, Concept Maps off — CONFIRMED.
  - **Approval caller** `approvals.py:31-37`: carries an Optional `chatroom_id` (`:35`) — thread
    it through — CONFIRMED (Q-2).
  - **Combined knowledge budget (F-16)** — RELATED, separate. Headless assembly inherits the same
    unbudgeted join (`turn_engine.py:439`, room mirror `:1032`) that F-16 addresses; not fixed
    here (FU-1).

## 7. Fix Design

1. **Extract a shared assembly helper.** Add
   `TurnEngine._assemble_agent_knowledge(agent, queries, *, chatroom_id: uuid.UUID | None) -> list[str]`
   that returns the knowledge system blocks in the room path's current order: File RAG
   (`_rag_context`), then Concept Maps (`_graphrag_context`) **only when `chatroom_id is not
   None`**, then Knowledge Map (`_knowmap_context`). Preserve the exact append order and the
   empty-block handling the room path uses today (`turn_engine.py:942-950`).
2. **Refactor the room path to use it.** Replace the inline block in `_run_locked`
   (`turn_engine.py:941-950`) with a call to the helper passing the room's `chatroom_id`. Its
   emitted blocks and order must be byte-for-byte what it produces today (characterization, §8.6).
3. **Wire headless — matching the room-path block order.** Add
   `chatroom_id: uuid.UUID | None = None` to `run_input_turn` (`turn_engine.py:404-412`). The
   room path appends the three knowledge blocks (`:944/947/950`) **before** the notify/pending
   block (`:978`); the headless path currently computes `notify_block` at `:435` and appends it
   at `:437-438`. To preserve that order, build
   `queries = self._knowledge_queries([], input_text=input_text)` and extend `system_parts` with
   `_assemble_agent_knowledge(agent, queries, chatroom_id=chatroom_id)` **after `base_system` is
   seeded (`:434`) but before the `if notify_block:` append (`:437`)** — i.e., knowledge blocks
   precede the notify block, as in the room path. (Headless has no history/summary block, so
   knowledge follows the base prompt directly.) When `chatroom_id is None`, the helper skips
   `_graphrag_context` entirely (no Concept Map resolution, no room lookup).
4. **Callers.** A2A (`a2a_handler.py:182-188`) calls `run_input_turn` with `chatroom_id` left at
   its `None` default. The approval worker (`approvals.py:88`) parses its carried
   `chatroom_id: str | None` (`:35`) to a UUID and passes it through (pass `None` when absent);
   Concept Maps then resolve for the approver in that room.

**Security considerations** (this fix touches the agent knowledge/prompt surface and, via Q-2, a
room trust boundary):
- Threading the approval room to enable Concept Maps (Q-2) is a **new trust-boundary crossing** —
  an approver Agent now reads a room's conversation-derived Concept Map that it previously never
  saw headless. `/build` should run the `check-security` lens on this specific
  approver -> Concept Map flow even though the audit did not pre-tag F-15 as security-required.
- The approval room threading must use the **server-side** `chatroom_id` from the parked approval
  payload (`approvals.py:35`), never a user-supplied value, so an approver cannot be steered to a
  foreign room's Concept Maps. Concept Map resolution reuses the existing
  `resolve_graphrag_layers(agent_id, chatroom_id)` (`turn_engine.py:1733-1735`) — the same path a
  normal room turn uses — so this grants the approver no access wider than a normal room turn for
  that same agent + room would, and returns an empty block when the approver's layers resolve to
  nothing.
- Concept Map channel ACL hardening (F-2 handshake room-ACL, F-25 mid-socket re-auth) is separate
  and out of scope; this fix must not widen Concept Map access beyond `resolve_graphrag_layers`'s
  current layer rules. If F-2's fix tightens that resolution, this path inherits it for free.
- File RAG pinned-key scope (F-1) is orthogonal and unchanged; headless File RAG uses the same
  `RagContextProvider` and will inherit F-1's guard once it lands.

**Reuse inventory (do not re-invent):**
- `_rag_context` / `_knowmap_context` / `_graphrag_context` / `_knowledge_queries`
  (`turn_engine.py:1712, 1744, 1720, 1794`) — the per-provider methods already exist and are
  reused verbatim by the helper; only the *orchestration* is new.
- The room path's block-append idiom (`turn_engine.py:942-950`) — copy its ordering and
  empty-check semantics into the helper.

**Patterns to follow (SoC):** all changes stay in the agents runtime (`turn_engine.py`) and the
two thin caller edits; no new coupling to the knowledge context beyond the existing provider
methods. No new tool is added (a Knowledge Map "tool" is explicitly not part of this fix — the
finding notes none exists; inline assembly matches the room path).

**Data repair:** none — this is a runtime behavior fix; no persisted data is affected.

## 8. Regression Test Plan

Extend the existing `run_input_turn`/A2A tests in
`backend/tests/unit/test_a2a_turn_dispatch.py` (already has
`test_run_input_turn_headless_completed`, `:81`) and the approver-turn tests in
`backend/tests/unit/test_approval_gate_fixes.py` (`drive_approver_turn`, `:102-149`); guard the
room-path characterization in `backend/tests/unit/test_agent_trigger_wiring.py`. Failing-first
backend unit tests against `TurnEngine` (fake providers / spies on `_rag_context`,
`_knowmap_context`, `_graphrag_context`):

1. **Headless queries Knowledge Map (primary red-first)** — `run_input_turn` for an agent with
   `knowmap_config_id` set assembles the Knowledge Map block into `system_text`. Fails today (no
   knowledge assembly).
2. **Headless queries File RAG** — `run_input_turn` for an agent with `rag_config_id` set
   assembles the File RAG block. Fails today.
3. **Headless without room skips Concept Maps** — `run_input_turn` with `chatroom_id=None` never
   calls `_graphrag_context` / `resolve_graphrag_layers`. (Locks the room-scoping invariant.)
4. **Headless with room includes Concept Maps** — `run_input_turn` with a `chatroom_id` calls
   `_graphrag_context` with exactly that id and includes the block. Fails today.
5. **Caller wiring** — A2A `_run_turn_with_db` invokes `run_input_turn` with `chatroom_id=None`
   (File RAG + Knowledge Map on, Concept Maps off); the approval worker passes its carried room
   through (Concept Maps on when present, off when the carried id is `None`).
6. **Room path unchanged (characterization)** — `_run_locked` still emits the same three blocks
   in the same order after the helper extraction (assert against the pre-refactor block sequence).

## 9. Risks and Rollback

- **Latency / cost on headless turns** — headless turns now issue provider embedding/retrieval
  calls they previously skipped. This is the intended `[R11.14]` behavior (parity with room
  turns); the per-provider internal caps (`rag_context_provider.py:143-144`,
  `_MAX_KNOWLEDGE_QUERIES` `turn_engine.py:1811`) bound the work as they do for room turns.
- **Unbudgeted system-block growth (F-16)** — the headless join (`turn_engine.py:439`) has no
  combined knowledge budget, same as the room path. Not regressed here; folded into F-16 (FU-1).
- **Characterization risk on the room path** — the helper extraction must not change room-turn
  output. Mitigated by test §8.6 asserting identical block content and order.
- **Approval room misuse** — mitigated by the security note in §7 (server-side room id only).
- **Rollback** — revert the dossier's commits. The new optional parameter and the extracted
  helper are additive/behavior-preserving for the room path; no schema change. Rollback returns
  headless turns to their prior (knowledge-less) behavior.

## 10. Acceptance Criteria

- [x] AC-1: Tests §8.1, §8.2, §8.4 fail before the fix and pass after. (Verified red-first, then
  green: `test_run_input_turn_assembles_knowledge_map`, `..._assembles_file_rag`,
  `..._with_room_includes_concept_maps`.)
- [x] AC-2: A headless `run_input_turn` assembles File RAG (when `agent.rag_config_id` is set) and
  the attached Knowledge Map (when `agent.knowmap_config_id` is set) as system blocks before
  streaming.
- [x] AC-3: A headless turn resolves Concept Maps if and only if a valid `chatroom_id` is supplied;
  with `chatroom_id=None`, `_graphrag_context`/`resolve_graphrag_layers` is never invoked.
  (`test_run_input_turn_without_room_skips_concept_maps`.)
- [x] AC-4: A2A invocations pass no room (Concept Maps off); the approval worker threads its
  carried authoritative `chatroom_id` through so approver turns include their room's Concept Maps.
  (`test_run_turn_with_db_passes_parent_agent_id` asserts no room; `test_drive_approver_turn_*`.)
- [x] AC-5: The room-turn path (`_run_locked`) produces identical knowledge blocks (same content,
  same order) after the helper extraction (§8.6). See D-1 for the characterization test placement.
- [x] AC-6: unit `pytest` green (1676 passed); `ruff check`/`format --check` clean on the diff;
  `mypy` introduces no new errors (16 pre-existing baseline errors in untouched modules — FU-3).
  The `tests/wiring/` tier is compose-backed and not runnable without the live stack (env-blocked,
  not a regression).
- [ ] AC-7: `/check-security` lens for the new approver -> Concept Map flow (server-side room id
  only; no access wider than a normal room turn for that agent + room). Deferred to the consolidated
  `/check-security` pass run after F-16 and F-14 land (all three share `turn_engine.py` and the
  knowledge/key surfaces); a focused self-audit against §7's three security requirements passed.

## 11. SRS Delta

None — this restores documented behavior. `[R11.14]` ("at agent invocation, an attached Knowledge
Map is queried ... beside file-RAG") and `[R10.09]` already cover headless invocation; the
approval-room Concept Map inclusion is consistent with `[R11.09]`'s "every Concept Map covering the
agent in the current room" applied to the approval's authoritative room.

## 12. Deviation Log

- **D-1**: The §8.6 room-path characterization was placed as a direct unit test of the extracted
  `_assemble_agent_knowledge` helper (`test_a2a_turn_dispatch.py::
  test_assemble_agent_knowledge_order_and_empty_handling`) rather than in `test_agent_trigger_wiring.py`.
  Reason: `_run_locked` has no unit-level harness — `test_agent_trigger_wiring.py` fakes the entire
  `TurnEngine.run_turn` and never exercises the inline assembly — whereas the helper's returned block
  sequence *is* the room path's knowledge-block output (the room path now just `system_parts.extend`s
  it). Testing the helper's order + empty-handling is the faithful, feasible characterization of the
  preserved behavior.
- **D-2**: `_assemble_agent_knowledge` returns `tuple[list[str], RagContext | None]`, not the spec's
  `list[str]` (§7.1). Reason: the room path consumes `rag_ctx.sources` after assembly to persist RAG
  citations (`reply_meta['rag_sources']`, `turn_engine.py:1122-1124`, [R10.09]); returning only the
  blocks would silently drop citation persistence. The `RagContext` is surfaced as the second tuple
  element and ignored by the headless caller. Behavior for both paths is otherwise unchanged.

## 13. Follow-ups

- **FU-1 (F-16)**: the headless path inherits the no-combined-knowledge-budget behavior; when
  F-16's token-aware allocator lands, apply it to the shared assembly helper so both room and
  headless turns are budgeted uniformly.
- **FU-2 (turn-entry coverage — verified)**: a full sweep of `turn_engine.py` confirms
  `run_input_turn` is the *only* headless provider-streaming entry, and its only callers are the
  A2A handler and the approval worker (`a2a_handler.py:182`, `approvals.py:88`); workflow-driven
  turns arrive via the A2A handler, and observer/wakeup turns run through `_run_locked` (which
  already assembles knowledge). So this one fix covers every knowledge-omitting path — no
  separate workflow entry is left unpatched. Recorded as a follow-up only to re-verify if a new
  headless turn entry is added later.
- **FU-3 (pre-existing mypy baseline)**: `mypy .` on `backend/` reports 16 errors in 8 modules
  untouched by this task (e.g. `contexts/tenancy/infrastructure/repositories.py:497`,
  `contexts/conversation/infrastructure/presence.py`, `contexts/workflow/application/workflow_service.py:352`).
  Confirmed pre-existing by stashing this task's edits and re-running `mypy` (same 16). Out of scope
  here; recorded so the repo-wide type baseline can be cleaned separately.
