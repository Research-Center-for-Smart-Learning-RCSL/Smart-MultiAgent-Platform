---
type: bugfix
status: implemented
created: 2026-07-17
requirements: [R9.10, R11.19]
---

# Headless turns bypass the cross-source knowledge token budget

## 1. Summary

This dossier remediates F-5 from
`docs/audits/2026-07-17-rag-graphrag-remediation-verification/findings.md`.
`run_input_turn` invokes shared knowledge assembly without a budget, joins every returned
block, and dispatches without a context-limit preflight
(`backend/contexts/agents/application/runtime/turn_engine.py:628-661`). A2A and approval
turns can therefore exceed the provider context despite normal room-turn budgeting.

- **Goal:** apply the same finite fixed-context accounting, knowledge precedence, starvation
  behavior, and initial-dispatch guard to every headless turn.
- **Non-goals:** cap later tool-round growth, constrain A2A envelope schema in this task, or
  change provider token estimators.

## 2. Observed vs Expected

- **Observed:** the headless call omits `budget`
  (`backend/contexts/agents/application/runtime/turn_engine.py:628-630`); `budget=None`
  deliberately queries all providers uncapped
  (`backend/contexts/agents/application/runtime/turn_engine.py:2209-2253`). Headless then
  dispatches directly (`backend/contexts/agents/application/runtime/turn_engine.py:642-661`).
- **Expected:** [R9.10] measures the next request, not room history alone
  (`REQUIREMENTS.md:370-375`), and [R11.19] bounds combined File RAG, Knowledge Map, and
  Concept Map context with narrow-scope precedence (`REQUIREMENTS.md:504`). The prior budget
  dossier records headless omission as a known deviation/follow-up
  (`docs/tasks/2026-07-14-knowledge-context-token-budget/spec.md:294-304,335-338`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Duplicate room arithmetic or extract shared planning? | Extract/reuse a shared initial-request budget planner and make the knowledge budget mandatory. | Budgeting currently lives in a room-local closure, which allowed headless assembly to drift (`backend/contexts/agents/application/runtime/turn_engine.py:1245-1290`). |
| Q-2 | What happens when authorized knowledge has zero budget? | Mirror room behavior: audit/return `knowledge_starved`, requeue drained notifications, and do not call the provider. | Silently dropping all configured knowledge makes the answer untrustworthy; room turns already handle this loudly (`backend/contexts/agents/application/runtime/turn_engine.py:1405-1447`). |
| Q-3 | How should fixed-only overflow behave? | Return a stable pre-dispatch overflow result/audit; never issue a guaranteed-invalid provider call. | Headless has no room UI where a provider context error can be surfaced reliably. |

## 4. Reproduction

1. Configure an Agent with File RAG and Knowledge Map, a large prompt/skill index/tool schema,
   and a compact cap below the combined payload.
2. Invoke it through A2A with a broad input; A2A is a production headless caller
   (`backend/contexts/orchestration/application/a2a_handler.py:166-199`).
3. Observe each knowledge provider receives no token budget and the assembled request is sent
   without a context-limit calculation.
4. The provider rejects or times out on the oversized request.

Approvals share the path (`backend/app/workers/tasks/approvals.py:83-94`).

## 5. Root Cause Analysis

Room request measurement and rendering are coupled inside a local `_assemble_request`
closure, while headless turns independently reconstruct their system prompt. Headless also
queries knowledge before resolving skills, notifications, and tools, so the fixed context
needed to compute remaining capacity is unknown. The optional `KnowledgeBudget` parameter
made that architectural omission silently valid. The root fix is a shared, mandatory
budget-planning boundary used before either path queries knowledge.

## 6. Blast Radius and Sibling Suspects

- **Blast radius:** A2A CALL/INSTRUCT and approval turns for knowledge-enabled Agents;
  unconstrained A2A payloads aggravate the failure
  (`backend/contexts/orchestration/domain/models.py:40-53,76-88`).
- **Cleared:** normal room turns compute finite budgets and a compact pre-dispatch guard
  (`backend/contexts/agents/application/runtime/turn_engine.py:1205-1291,1366-1447`).
- **Confirmed separate debt:** tool outputs can grow later rounds without re-budgeting
  (`backend/contexts/agents/application/runtime/turn_engine.py:1996-2058`); this remains a
  follow-up.
- **Cleared security boundary:** the optional room is membership-checked before headless
  Concept Map resolution (`backend/contexts/agents/application/runtime/turn_engine.py:656-660`).
  Scope note: A2A never passes `chatroom_id`
  (`backend/contexts/orchestration/application/a2a_handler.py:183-189`), so only the approvals
  worker (`backend/app/workers/tasks/approvals.py:90-94`) reaches that gate. The gate is intact,
  but it does not cover the A2A path; do not treat it as a general headless guard.

## 7. Fix Design

1. Resolve skills, pending context, built-in tools, registry, and serialized tool specs before
   knowledge. Preserve rendered order: base, knowledge, skills, notify.
2. Resolve provider context limit and ceiling exactly as room turns: compact cap/default in
   compact mode and provider hard limit in general mode.
3. Measure base/dynamic system blocks, serialized tools, input, and response reserve. Reuse
   `knowledge_budget` and `KnowledgeBudget`
   (`backend/contexts/agents/application/context.py:113-150`), the existing safety margin,
   and graph-source cap.
4. Make `_assemble_agent_knowledge` require a finite budget; remove the uncapped production
   branch. Allocate Concept Map, then Knowledge Map, then File RAG, returning unused grants
   exactly as room turns do.
5. Reuse/extract `_SystemBlocks` and one request planner so measure/render ordering cannot
   drift between room and headless paths.
6. Mirror room starvation and notification-requeue semantics. Source detection must use the
   ACL-filtered `knowledge_chatroom_id`.
7. Re-estimate system, messages, tool specs, and reserve before initial dispatch. Fixed-only
   overflow returns a stable skipped/error result and audit without logging prompt contents.

### Security Considerations

This is an LLM resource-exhaustion surface. Count attacker-influenceable A2A input, skill
index, notification context, and tool schemas server-side. Preserve the room membership gate
and all project/config retrieval filters. Do not log raw prompt, retrieved text, tool schema,
or notification bodies.

## 8. Regression Test Plan

1. Extend `backend/tests/unit/test_a2a_turn_dispatch.py:227-318` with compact and general
   cases that assert finite source grants, precedence, and bounded initial payload.
2. Add authorized-source/zero-budget coverage: `knowledge_starved`, audit fields, notification
   requeue, and no provider call.
3. Add fixed-only overflow coverage and a no-source zero-budget characterization.
4. Retain the non-member room Concept Map security regression
   (`backend/tests/unit/test_a2a_turn_dispatch.py:291-318`).
5. Extend shared arithmetic coverage in
   `backend/tests/unit/test_turn_context_budget.py:71-143` if planning is extracted.

## 9. Risks and Rollback

Some headless calls that previously reached the provider and failed will now truncate or skip
deterministically. Resolving tools before knowledge changes timing, not rendered order. Keep
the existing safety margin plus pre-dispatch guard because estimation is approximate. No
migration is required; code rollback restores the overflow risk.

## 10. Acceptance Criteria

- [x] AC-1: The headless budget regressions fail before the fix and pass after. Verified red
  first: `None == 700` (no grant issued) and `completed` where `skipped` is required.
- [x] AC-2: Every production knowledge assembly receives a finite `KnowledgeBudget`; no
  uncapped `budget=None` path remains. `budget` is now a required parameter.
- [x] AC-3: Headless fixed-context accounting includes base prompt, skills, notifications,
  serialized tools, input, response reserve, and the same mode ceiling/safety margin as room.
- [x] AC-4: Combined knowledge is bounded with Concept Map > Knowledge Map > File RAG
  precedence, and zero-grant sources are not queried.
- [x] AC-5: Oversized initial A2A/approval payloads never reach the provider; authorized
  knowledge starvation is audited and drained notifications are requeued.
- [x] AC-6: The approval/non-member room gate, tenant scoping, and block order are unchanged.
  Room budgeting is unchanged except for the ceiling clamp recorded as D-3.
- [x] AC-7: Focused tests, backend lint, format, and type checks pass. Full unit tier green
  (5434 passed); the wiring/integration tiers could not run — see D-4.

## 11. SRS Delta

None. This restores [R9.10] and [R11.19].

## 12. Deviation Log

- **D-1: Starvation semantics are a subset of the room path's, not a mirror.** §7.6 and Q-2 say
  "mirror room behavior". Three of the room path's six starvation steps are room-only and have
  no headless counterpart: the WS `emit_agent_finished_error`, the observer `observation.failed`
  event, and `_compact_forced_rooms.discard`. Headless does the other three (audit, commit,
  requeue) and returns `skipped`. The room verdict is also deferred past a recompaction pass;
  headless decides immediately, because it has no history to shed and therefore no second pass
  that could change the answer.
- **D-2: The overflow guard covers both context modes, diverging from the room path.** The room
  path guards only in `compact` mode and deliberately lets `general` mode surface the provider's
  own context error to the room UI (`turn_engine.py:1849-1851`). Headless has no UI to surface it
  to and no history to recompact, so both modes stop pre-dispatch with
  `reason="context_overflow"`. This follows Q-3 but is a real behavioral divergence between the
  two paths, not a mirror.
- **D-3: `_request_ceiling` clamps the ceiling to the provider limit, which changes room
  behavior in one edge case.** AC-6 asked that room budgeting stay unchanged. The quality audit
  found that `context_token_cap` is bounded at the DB by `MAX_CONTEXT_TOKEN_CAP`, which is the
  *widest* provider window (gemini's 1M, `contexts/agents/domain/models.py:91`) rather than the
  agent's own — so a claude (200k) or openai (128k) agent can legally carry a cap above what its
  provider accepts. Unclamped, the new headless overflow guard would then hard-skip *every* turn
  for such an agent, which is worse than the uncapped behavior being replaced. The clamp lives in
  the shared helper, so the room path also stops granting knowledge above the provider window.
  For every agent whose cap is within its provider's window (the normal case) nothing changes.
  A later review asked whether the clamp could newly starve *room* turns by flooring a budget
  that used to be positive. It cannot, and the reason is the room path's existing
  recompact-then-judge ordering: for a clamped agent `ceiling == context_limit` exactly, so a
  floored budget implies `payload >= context_limit`, which trips the recompaction branch
  (`turn_engine.py:1914`) before starvation is judged, and `starved` is recomputed against the
  shed history. The residual case — a non-history fixed context (prompt, skills, tools) that
  alone exceeds the provider window — previously built a request the provider was certain to
  reject, so a clean `knowledge_starved` skip is an improvement rather than a regression.
- **D-4: Behavioral verification against a running stack was not performed.** The compose stack
  (Postgres/Redis/Vault) is unavailable in this environment; the wiring tier fails with
  `socket.gaierror` on a clean checkout too. `tests/wiring/test_wiring.py::test_a2a_call_round_trip`
  covers exactly this path and could not be exercised. Verification rests on the unit tier.
- **D-5: `_context_limit_for` was extracted and the two pre-existing copies collapsed onto it.**
  Not in the Fix Design, which only said headless should resolve the limit "exactly as room turns".
  Agreed with the user before implementation rather than duplicating the lookup a third time.
- **D-6: the overflow verdict is split in two, and the provider bound is judged before
  retrieval.** A post-implementation review found that a fixed context too large for the provider
  floors the knowledge budget exactly as a tight cap does, so the starvation branch claimed it
  first and audited `knowledge_starved` — pointing the operator at a cap or knowledge setting
  that cannot fix an oversized input. `run_input_turn` now decides `fixed_context + reserve >
  context_limit` before the starvation check and before `_assemble_agent_knowledge`, so the
  provider-level verdict is reported as `context_overflow` and no retrieval I/O is paid for a
  request that cannot be sent. The post-assembly guard stays as a backstop for knowledge
  overshooting its grant. Both overflow audits carry `bound: "provider"` to keep the two
  distinguishable if a second bound is ever added.
- **D-7: the compact-mode cap is deliberately NOT enforced as a bound on the headless payload.**
  The same review proposed measuring the pre-dispatch guard against `ceiling` rather than
  `context_limit`, so a compact-mode agent could not dispatch above its cap. This was
  implemented, rejected on evidence, and reverted: [R9.10] defines `context_token_cap` as the
  threshold at which compaction runs, not as a bound on the request, and the room path guards
  against the provider limit for the same reason. Enforcing it here would skip every headless
  turn for any agent whose prompt alone exceeds its compaction trigger — a 5k-token prompt under
  an 8k cap never dispatches. `test_run_input_turn_dispatches_above_a_cap_the_prompt_alone_overruns`
  pins the rejection so it is not re-proposed.

## 13. Follow-ups

- FU-1: Re-budget or cap tool-result growth across later tool rounds. The security audit
  confirmed this is now the direct residual of the vulnerability fixed here: the pre-dispatch
  guard bounds only the first request, while `_stream_with_tools` appends up to `MAX_TOOL_ROUNDS`
  rounds of tool results with no aggregate cap and no re-check against `context_limit`.
- FU-2: Define an explicit maximum A2A payload size at the orchestration boundary.
  `A2AEnvelope.payload` is `dict[str, Any]` with no length validation
  (`contexts/orchestration/domain/models.py:51`), so an unbounded string is accepted into the
  Redis stream and worker memory before any guard can fire.
- FU-3: `_context_limit_for` falls back to `128_000` for an unrecognised `model_hint`. Behavior
  is unchanged by this task, but a provider with a smaller real window would pass the overflow
  guard and then be rejected. Now has one place to fix instead of three.
- FU-4: `turn_engine.py` is ~2650 lines mixing room orchestration, headless orchestration,
  knowledge assembly, system-block modelling, and token estimation. Pre-existing.
- FU-5: an approver whose turn is refused pre-dispatch casts no vote, and there is no signal to
  tell the gate so — it falls to its timeout port, which is the exact failure
  `drive_approver_turn` was built to eliminate (`app/workers/tasks/approvals.py:1-10`). The
  worker now logs the refusal at warning level with the reason so the cause is findable when it
  happens rather than at the timeout, but the real fix is an abstain/unavailable signal to
  `ApprovalService` so the gate can settle immediately instead of waiting.
