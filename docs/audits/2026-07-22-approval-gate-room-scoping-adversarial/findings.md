---
type: audit
status: closed
created: 2026-07-22
requirements: [R15.10]
---

# Audit: approval-gate room-scoping adversarial verification

## 1. Scope

- **Area**: The approval-gate room-resolution flow from workflow trigger payload through executor,
  approval creation, Redis claim, publishes, notifications, timeout jobs, approver turns, and all
  executable `RunContext` construction paths.
- **Intent sources**: `[R15.10]` in `REQUIREMENTS.md`, the approved room-scoping dossier at
  `docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md`, especially AC-2 through AC-5, and the
  room-scoped knowledge guard in `backend/contexts/agents/application/runtime/turn_engine.py:741-753`.
- **Depth**: Thorough. Three independent lenses ran: lifecycle/resume, boundary inputs and tenant
  isolation, and asynchronous sinks/concurrency. Each candidate received a separate refutation pass.

## 2. Coverage

Read in full: `backend/contexts/workflow/application/executors/approval_gate.py`,
`backend/contexts/workflow/application/run_engine.py`,
`backend/contexts/orchestration/application/approval_service.py`,
`backend/app/workers/tasks/approvals.py`, `backend/contexts/conversation/interfaces/facade.py`,
the chatroom and workspace repositories, and the approval-gate unit tests. Read relevant trigger
payload validation and API wiring in `backend/shared_kernel/validation.py` and
`backend/app/api/v1/workflows.py`.

Not covered: browser/UI rendering, database execution of the read-only detection query, and the
broader approval-resume reliability dossier. The audit did not judge structural quality or security
severity beyond recording functional isolation behavior.

## 3. Findings

## F-1: Falsy supplied chatroom IDs silently create headless approval gates

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/executors/approval_gate.py:68-70` resolves a
  supplied value with `or` and performs UUID, scope, and audit checks only inside `if raw_room`.
  `backend/app/api/v1/workflows.py:180-181` accepts a `BoundedPayload`, whose contract at
  `backend/shared_kernel/validation.py:96-98` is an arbitrary bounded JSON object. The executor then
  calls `create_approval_gate(..., chatroom_id=None)` at `approval_gate.py:107-110`, writes a claim at
  `:120-125`, emits an event at `:127-135`, and parks at `:139-144`.
- **Failure scenario**: A caller triggers a workflow with
  `{"trigger_payload":{"chatroom_id":""}}` (equally `false`, `0`, `[]`, or `{}`). The value is
  present but falsy, so no resolver or rejection audit runs; the executor creates and parks a
  room-less gate. An in-memory reproduction produced `running True None` for the empty-string case.
- **Blast radius**: Every trigger source and persisted continuation carrying a falsy supplied value;
  operators receive neither a failure nor `approval.gate_room_rejected`, while a human-facing gate is
  silently absent.
- **Intent source**: AC-3 in
  `docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md` requires every malformed supplied room
  to fail loudly and never degrade to a headless gate. AC-5 reserves headless behavior for no room
  supplied anywhere.

## F-2: A room can be soft-deleted after scope validation but before its approval event is published

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `ConversationFacade.resolve_chatroom_scope` performs independent live-room and
  live-workspace reads at `backend/contexts/conversation/interfaces/facade.py:86-92`; their repository
  predicates are at `backend/contexts/conversation/infrastructure/repositories/chatroom_repo.py:91-101`
  and `workspace_repo.py:46-56`. After that await, the executor calls `create_approval_gate` at
  `backend/contexts/workflow/application/executors/approval_gate.py:107-110`, which publishes to the
  room without a liveness re-check at
  `backend/contexts/orchestration/application/approval_service.py:102-115`.
- **Failure scenario**: A live in-project room passes scope resolution. A separate transaction
  soft-deletes the room before `create_gate` publishes. The approval request is then emitted to a
  logically deleted room and the room value is still copied to notes and jobs.
- **Blast radius**: A narrow concurrent delete/create window can produce a stale same-project event.
  The audit did not find a path that changes a validated room into another project's room.
- **Intent source**: AC-3 in
  `docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md` explicitly includes deleted rooms in the
  fail-closed contract. A two-session interleaving test is required to confirm the production timing.

## 4. Refuted Candidates

- **Cross-project values still reach a sink**: refuted. Validation precedes approval creation, claim
  registration, every publish, notification, and job enqueue (`approval_gate.py:68-110`); the
  cross-project and config-path unit tests pin the absence of all of those effects.
- **Continuation, retry, or resume loses project identity**: refuted. `start_run`,
  `_prepare_continuation`, `retry_node`, and `resume_at_port` set or re-read `project_id` before an
  executor can run (`run_engine.py:179-186,263-270,306-313,353-360`). The watchdog context is
  inspection-only.
- **Truthy malformed values bypass scope checks**: refuted. `uuid.UUID` rejects them and the executor
  records `approval.gate_room_rejected`; `test_approval_gate_rejects_malformed_room_instead_of_ignoring_it`
  covers the string case.
- **A rejected gate can enqueue a timeout or approver turn before failure**: refuted. Those effects
  begin only in `ApprovalService.create_gate` after executor validation returns successfully
  (`approval_service.py:76-134,151-189`).

## 5. Hand-off

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix | `docs/tasks/2026-07-22-approval-gate-room-scoping/` |
| F-2 | fix | `docs/tasks/2026-07-22-approval-gate-room-scoping/` |

## 6. Out-of-scope Observations

None.
