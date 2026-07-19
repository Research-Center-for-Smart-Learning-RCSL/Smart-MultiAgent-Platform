---
type: bugfix
status: approved
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

- [ ] AC-1: The headless budget regressions fail before the fix and pass after.
- [ ] AC-2: Every production knowledge assembly receives a finite `KnowledgeBudget`; no
  uncapped `budget=None` path remains.
- [ ] AC-3: Headless fixed-context accounting includes base prompt, skills, notifications,
  serialized tools, input, response reserve, and the same mode ceiling/safety margin as room.
- [ ] AC-4: Combined knowledge is bounded with Concept Map > Knowledge Map > File RAG
  precedence, and zero-grant sources are not queried.
- [ ] AC-5: Oversized initial A2A/approval payloads never reach the provider; authorized
  knowledge starvation is audited and drained notifications are requeued.
- [ ] AC-6: The approval/non-member room gate, tenant scoping, block order, and normal room
  budgeting remain unchanged.
- [ ] AC-7: Focused tests, backend lint, format, and type checks pass.

## 11. SRS Delta

None. This restores [R9.10] and [R11.19].

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- FU-1: Re-budget or cap tool-result growth across later tool rounds.
- FU-2: Define an explicit maximum A2A payload size at the orchestration boundary.
