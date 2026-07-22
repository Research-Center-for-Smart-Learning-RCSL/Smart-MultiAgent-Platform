---
type: audit
status: reviewed
created: 2026-07-22
requirements: [R14.08, R14.10]
---

# Audit: Workflow run cancellation adversarial verification

## 1. Scope

- **Area**: Workflow run termination, node execution, parked-branch resume, and workflow-originated synchronous A2A cancellation.
- **Intent sources**: `[R14.08]` and `[R14.10]` in `REQUIREMENTS.md`; `docs/workflow.schema.md:162`; `docs/tasks/2026-07-22-workflow-run-cancellation/spec.md`.
- **Depth**: Thorough. State/lifecycle, concurrency, error-path/event-flow, and client-trace lenses were independently investigated and then refuted or confirmed against the implementation.

## 2. Coverage

Read in full: workflow run engine and repositories; workflow service and API cancellation entry point; workflow workers for steps, watchdog, signals, and resumes; A2A service, rendezvous, handler, and agent-invocation executor; the workflow run socket composable; relevant unit tests, requirements, schema, and task dossier.

Not covered: live PostgreSQL/Redis fault injection or browser rendering. The configured integration database host (`postgres:5432`) was unavailable in this environment, so findings are traced from the transaction and message paths rather than reproduced against a running stack.

## 3. Findings

## F-1: Node liveness check has a terminal-state TOCTOU window

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/run_engine.py:568-624`, `backend/contexts/workflow/infrastructure/repositories.py:227-233`
- **Failure scenario**: A parallel branch reads the run as `RUNNING` at its node boundary. Before it inserts the next step and invokes its executor, a sibling commits a terminal state. The first branch still creates the step and can issue a provider call because neither step creation nor executor entry atomically predicates on the live run state.
- **Blast radius**: The stated node-boundary guarantee is violated under the exact cross-worker interleaving this task addresses; a user can still pay for one node that had not begun when the run became terminal.
- **Intent source**: `docs/tasks/2026-07-22-workflow-run-cancellation/spec.md` AC-4 and AC-5; `docs/workflow.schema.md:162`.

## F-2: Losing terminal transitions still publish their own terminal outcome

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/infrastructure/repositories.py:301-327`, `backend/contexts/workflow/application/run_engine.py:414-436`, `:446-467`, `:696-720`, `:839-866`
- **Failure scenario**: Two branches concurrently observe a running run. One commits `SUCCEEDED`; the other fails a node, receives `False` from the conditional state update, but still bulk-cancels steps, increments the failed metric, emits an audit event, and publishes a failed run event. The database remains succeeded while clients receive a later failed terminal event.
- **Blast radius**: Run status, audit history, metrics, and real-time UI can disagree. A losing branch can also rewrite another completed step to cancelled.
- **Intent source**: `[R14.08]`; `docs/workflow.schema.md:162`.

## F-3: Concurrent parked branches can both resume after one state transition loses

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/run_engine.py:345-405`, `backend/contexts/workflow/infrastructure/repositories.py:301-327`, `backend/contexts/workflow/application/run_engine.py:670-678`
- **Failure scenario**: Two distinct parallel branches are parked and their separate claim holders both read `WAITING`. The first wins `WAITING -> RUNNING`; the second ignores its failed conditional update, seals its parked step, and advances its edges anyway.
- **Blast radius**: Duplicate downstream execution and side effects after parallel parked branches resume. The documented claim-retry rule cannot help because the losing resume is incorrectly reported as successful.
- **Intent source**: `[R14.08]`; `backend/contexts/workflow/application/run_engine.py:334-344`.

## F-4: A transient Redis failure permanently drops the in-flight A2A cancellation signal

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/run_engine.py:506-514`, `:879-890`, `backend/contexts/orchestration/infrastructure/a2a_rendezvous.py:84-96`
- **Failure scenario**: The run transition commits, then `cancel_workflow_calls` fails transiently. The engine has already cleared its in-memory cancellation set and catches/logs the exception without retry, outbox, or re-enqueue. A live multi-round turn therefore receives no cancellation marker and can continue its remaining tool rounds.
- **Blast radius**: The Q-2 cancellation guarantee fails precisely during a recoverable Redis outage, permitting additional provider spend after a durable terminal run state.
- **Intent source**: `docs/tasks/2026-07-22-workflow-run-cancellation/spec.md` Q-2 and C6.

## F-5: Bulk step cancellation leaves the live backstage trace stale

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/infrastructure/repositories.py:440-449`, `backend/contexts/workflow/application/run_engine.py:446-467`, `frontend/src/slices/workflow/composables/useWorkflowRunSocket.ts:58-72`
- **Failure scenario**: A user cancels a run with a parked sibling. `cancel_pending_for_run` changes the sibling step to cancelled but emits no step event. The socket receives only a run-terminal event, for which the client refreshes the run header but not `wfKeys.steps(runId)`; the trace stays pending/running until reconnect or manual refetch.
- **Blast radius**: Admins and project owners see a stale execution trace after cancellation even though the database state is terminal.
- **Intent source**: `[R14.10]`; workflow event contract in `REQUIREMENTS.md:845`.

## 4. Refuted Candidates

- The 900-second workflow-call index TTL does not expire during a validated `agent_invocation`: `docs/workflow.schema.json:248-260` caps the node timeout at 600 seconds.
- A post-commit Arq replay cannot bypass the current node liveness check: `run_step` rebuilds context and `_execute_node` reads terminal state before its normal path.
- A registration that races with run cancellation can still enqueue a CALL message, but the terminal marker is either observed during registration or written before the callee's existing cancellation check; no provider round starts from that interleaving. This does not survive as an independent spend defect.
- `_mark_run_failed_isolated` sends the A2A cancellation signal only after its independent transaction exits successfully.

## 5. Hand-off

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | Untriaged | — |
| F-2 | Untriaged | — |
| F-3 | Untriaged | — |
| F-4 | Untriaged | — |
| F-5 | Untriaged | — |

## 5.1 Review decision

The user approved repair of every confirmed finding. F-1 through F-5 are all
assigned to `docs/tasks/2026-07-22-workflow-run-cancellation/`; the historical
handoff table above is retained unchanged from the audit capture.

## 6. Out-of-scope Observations

None.
