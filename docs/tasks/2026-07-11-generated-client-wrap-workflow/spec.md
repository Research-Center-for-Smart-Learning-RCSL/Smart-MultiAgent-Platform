---
type: refactor
status: approved
created: 2026-07-11
requirements: [R24.13, R22.11]
---

# Wrap the `workflow` slice's api layer over the generated client

## 1. Summary

Next increment of the [R24.13] slice-wrap program: convert the `workflow` slice's api
module (`frontend/src/slices/workflow/api/index.ts` — **20 named-export functions**, 189
lines) to call the generated `@shared/api-client` services instead of the bare
`@shared/transport` `http` singleton. Unlike `agents`/`keys`, this module **already
unwraps `.data`** in every function (each returns a bare body), so this is the
**conversation pattern**: signature-preserving drop-in with **zero call-site changes**.
All 20 functions have an exact URL+verb match across four generated services — there is
**no dead code** to delete (contrast the agents increment's 7 removed methods).

## 2. Motivation

- **[R24.13] convergence.** One instrumented axios singleton (`shared/transport/axios.ts`)
  owns bearer auth, 401-refresh, and problem+json→typed-error mapping; the generated
  `request` core calls into it, so wrapped methods inherit all of it. This slice hand-codes
  ~20 request/response shapes against `http` (`workflow/api/index.ts:3` imports `http`),
  duplicating URL/verb/param knowledge that `pnpm run gen:api` already owns and
  `check:openapi-drift` guards. `agent-groups`, `conversation`, `keys`, `agents` are done;
  `workflow` is the largest remaining `http` module (189 lines).
- **Not "messy" — divergent-source-of-truth.** The concrete debt is that
  `workflow/api/index.ts:20-216` re-encodes endpoints (`/workflows/{id}/runs`,
  `/orchestration/...`, the `/agents/{id}` wakeup reach-in) that the generated
  `WorkflowsService`/`WorkflowRunsService`/`OrchestrationService`/`AgentsService` already
  express, so a backend contract change silently desyncs the hand-typed copy.

## 3. Non-goals

- **No externally observable behavior change.** Same endpoints, verbs, bodies, query
  params, `If-Match` preconditions. No approval, instruction, DLQ, or wakeup payload is
  reshaped, logged, or dropped.
- **No call-site changes.** The api already returns bare bodies; consumers
  (`WorkflowListView`, `WorkflowRunView`, `WorkflowRunsListView`, `WorkflowBackstageView`,
  `AgentOrchestrationView`, `DlqViewer`, `InstructChainView`, `SubagentTree`,
  `useWorkflowRunSocket`, `useWorkflowLint`) are untouched.
- **No slice-type rebase.** The hand-rolled domain types stay (Q-2): `Workflow`,
  `WorkflowRun`, `WorkflowStep`, `WorkflowDefinition`, `ValidationResult`, `LintIssue`, and
  the orchestration DTOs (`Approval`, `ApprovalWithVotes`, `Instruction`, `AgentInstance`,
  `DlqEntry`) re-exported from `@shared/types/workflow` (`types/index.ts:147-155`). They
  back the Vue-Flow editor, the run-state machine, and the socket event union
  (`WorkflowRunEvent`, `types/index.ts:130-141`).
- **No `gen:api` rerun.** Frontend-only edit; the contract is unchanged.
- **No orchestration model-typing.** The generated `OrchestrationService` methods return
  `Record<string, any>` (the backend orchestration routes aren't modelled as Pydantic
  `*Out` in the OpenAPI). Tightening those server-side is out of scope → FU.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled, carried) enum widening? | Backend enum sweep already done; wrap now. | Generated `RunState` (`RunState.ts:5`) and `StepState` (`StepState.ts:5`) are byte-identical string unions to the slice's (`types/index.ts:27,29-36`) — no drift, no backend change pulled in. |
| Q-2 | (settled, carried) Keep hand-rolled types or alias generated models? | Keep hand-rolled; bridge/cast divergences at the api boundary. | Types are consumed slice-wide (editor, sockets, run machine) and the orchestration DTOs live in `@shared/types`, not the generated client; the generated returns are loosely typed (`Record<string,any>`) so aliasing them would *lose* type precision. |
| Q-3 | (settled, carried) How to convert safely at this scale? | Rewrite over the generated services; `pnpm typecheck` + `pnpm test` verify; conversation-pattern means no consumer sweep. | Proven across four prior slices. |
| Q-4 | The generated orchestration/run/validate methods return `Record<string,any>` / `Record<string,string>`, not precise models — cast or bridge? | **Cast** to the hand-rolled type at the wrapper return (`as ApprovalWithVotes`, `as WorkflowRun[]`, `as { run_id: string }`, …). | The old code already made the identical unchecked assertion via `http.get<T>()` (`index.ts:21`, etc.) — the cast merely relocates it, introducing **no new** unsafety. A default-supplying `to<Type>` bridge is only warranted when the backend always populates an optional field (none here; the loose typing is codegen imprecision, not missing data). |

## 5. Current vs Target Structure

Frontend layer direction is unchanged (`slices/workflow/api` → `shared/api-client`, both
legal). Each function body changes from `http.<verb><T>(url, …)` (already `.data`-unwrapped)
to `<Service>.<method>({ …options })` (already a bare body). Function names, signatures,
and return types are **identical** — the module's public surface does not move.

### 5A. Function → generated method mapping (verified, all 20 matched)

| # | Function | Service.method | Notes |
|---|---|---|---|
| 1 | `listWorkflows` | `WorkflowsService.listWorkflows…Get` | `{ wid }`; return cast (definition drift, §5B-1) |
| 2 | `createWorkflow` | `WorkflowsService.createWorkflow…Post` | `{ wid, requestBody }`; cast |
| 3 | `patchWorkflow` | `WorkflowsService.patchWorkflow…Patch` | `{ workflowId, ifMatch: String(version), requestBody }`; cast |
| 4 | `deleteWorkflow` | `WorkflowsService.deleteWorkflow…Delete` | `{ workflowId }`; `void` |
| 5 | `validateWorkflow` | `WorkflowsService.validateWorkflow…Post` | body `{ definition }` (`ValidateIn`); return cast (§5B-3) |
| 6 | `triggerRun` | `WorkflowsService.triggerRun…Post` | body `{ trigger_payload }` (`RunTriggerIn`); cast `{ run_id }` |
| 7 | `dryRun` | `WorkflowsService.dryRun…Post` | body `{ trigger_payload }`; cast `{ run_id }` |
| 8 | `listRuns` | `WorkflowsService.listRuns…Get` | `{ workflowId, limit=50, offset=0, includeArchive=false }`; cast (§5B-2) |
| 9 | `getRun` | `WorkflowRunsService.getRun…Get` | `RunOut` → `WorkflowRun` assignable, **no cast** |
| 10 | `cancelRun` | `WorkflowRunsService.cancelRun…Post` | cast `{ status }` |
| 11 | `listSteps` | `WorkflowRunsService.listSteps…Get` | `StepOut[]` → `WorkflowStep[]` assignable, **no cast** |
| 12 | `getApproval` | `OrchestrationService.getApproval…Get` | `Record<string,any>` → cast `ApprovalWithVotes` |
| 13 | `listApprovalsForRun` | `OrchestrationService.listApprovalsForRun…Get` | cast `Approval[]` |
| 14 | `getInstruction` | `OrchestrationService.getInstruction…Get` | cast `Instruction` |
| 15 | `listInstructionsForChain` | `OrchestrationService.listInstructionsForChain…Get` | cast `Instruction[]` |
| 16 | `listRunSubagents` | `OrchestrationService.listRunSubagents…Get` | **param is `workflowRunId`**, not `runId`; cast `AgentInstance[]` |
| 17 | `listDlq` | `OrchestrationService.getAgentDlq…Get` | cast `DlqEntry[]` |
| 18 | `getAgentWakeupConfig` | `AgentsService.readAgent…Get` | hand-built reshape `{ wakeupConfig, version }` stays |
| 19 | `patchAgentWakeupConfig` | `AgentsService.patchAgent…Patch` | `{ agentId, ifMatch: String(version), requestBody: { wakeup_config } }`; read `.version` |

The full method names + `path:line` citations live in the mapping artifact (Explore
output attached to this task), not inline, to keep the dossier maintainable.

### 5B. Return casts (all behavior-identical to the current `http.get<T>` assertion)

1. **`WorkflowOut.definition` is `Record<string,any>`** (`WorkflowOut.ts:7`) vs slice
   `Workflow.definition: WorkflowDefinition` (required `entry_node_id`/`nodes`/`edges`,
   `types/index.ts:62-80`). Affects functions 1, 2, 3 — cast the returned body to `Workflow`.
2. **`listRuns` returns `Array<RunOut | ArchivedRunOut>`** (`ArchivedRunOut.trigger_type`
   and `.workflow_id` are `string | null`, `ArchivedRunOut.ts:21-22`) vs `WorkflowRun[]`
   (non-null). Cast to `WorkflowRun[]` — identical to the current unchecked
   `http.get<WorkflowRun[]>` (`index.ts:94`).
3. **`ValidateOut.errors`/`warnings` are `Array<Record<string,any>>`** (`ValidateOut.ts:6,8`)
   vs `ValidationResult` `LintIssue[]` (`types/index.ts:124-128`). Cast to `ValidationResult`.
4. **triggerRun/dryRun/cancelRun** return `Record<string,string>` vs the inline
   `{ run_id }`/`{ status }` shapes. Cast.
5. **Orchestration 12-17** all return `Record<string,any>`/`Array<Record<string,any>>`;
   cast each to its hand-rolled target (direct `as ApprovalWithVotes` etc. — the concrete
   interface overlaps `Record<string,any>`, no `as unknown` needed).

**No cast (directly assignable):** getRun (`RunOut`→`WorkflowRun`), listSteps
(`StepOut[]`→`WorkflowStep[]`). **No bridge (hand-built reshape stays):**
getAgentWakeupConfig reads `AgentOut.wakeup_config` (required `Record<string,any>`,
`AgentOut.ts:27`) — the current `?? {}` default becomes redundant but is kept defensively;
patchAgentWakeupConfig reads `AgentOut.version` off the response.

### 5C. Consumer sweep

**None.** The api already returned bare bodies, so no consumer reads `.data` off these
functions (verified: the 11 importers call the functions directly, e.g.
`WorkflowListView`, `useWorkflowRunSocket`). This is the defining property of the
conversation pattern.

## 6. Characterization Test Plan

The api module currently has **no `api/__tests__`** directory. Per the agents/keys
precedent, add `workflow/api/__tests__/index.spec.ts` — request-level MSW characterization
that pins, **before** the rewrite is trusted:

- **verb + path + params** for a representative function per service group: a Workflow CRUD
  read/write (`listWorkflows`, `createWorkflow`), `patchWorkflow` asserting the `If-Match`
  header carries `String(version)`, `listRuns` asserting `limit/offset/include_archive`
  query params, a run action (`triggerRun` body `{ trigger_payload }`, `cancelRun`), an
  orchestration read per shape (`getApproval`, `listDlq`), and both wakeup-config functions.
- **the two reshapes**: `getAgentWakeupConfig` returns `{ wakeupConfig, version }` and
  defaults `wakeup_config` absent → `{}`; `patchAgentWakeupConfig` sends
  `{ wakeup_config }` with `If-Match` and returns the bumped `version`.
- **the `{ run_id }` / `{ status }` casts** resolve the bare body.

Existing view/socket tests (`__tests__/WorkflowListView.test.ts`,
`__tests__/WorkflowRunView.test.ts`, `composables/__tests__/*`) already exercise the
consumers; they must pass **unmodified** (the signature-preserving guarantee). Any that
module-mock the api returning a bare body already match the new shape (no `{ data }`
envelope to update, unlike the agents socket mocks).

## 7. Migration Steps

1. Rewrite `workflow/api/index.ts` over the four generated services
   (`WorkflowsService`, `WorkflowRunsService`, `OrchestrationService`, `AgentsService`);
   drop the `http` import; keep every function name/signature/return type. Apply the §5B
   casts at each return; keep the two wakeup reshapes.
2. Add `workflow/api/__tests__/index.spec.ts` per §6.
3. `pnpm typecheck` → must be green with no consumer edits (proves the conversation
   pattern held). If any consumer error appears, that is a spec violation — stop and report.
4. `pnpm test` → new spec passes; all existing workflow tests pass unmodified.
5. `pnpm lint` (changed files) + `pnpm build`. No `gen:api`.

## 8. Risks and Rollback

- **Loose orchestration return typing.** The casts to hand-rolled DTOs (§5B-5) are
  unchecked — but identical to the pre-existing `http.get<T>()` assertions, so this
  refactor adds no new unsafety. Mitigated by the characterization spec feeding realistic
  bodies. Genuine model-typing is FU-2.
- **`listRunSubagents` param rename.** The generated method's path param is `workflowRunId`,
  not `runId` (§5A-16) — a wiring detail the rewrite must get right; `pnpm typecheck` and
  the spec catch a wrong key.
- **`If-Match` on two functions.** `patchWorkflow` and `patchAgentWakeupConfig` must pass
  `ifMatch: String(version)`; a missing precondition would 428 at runtime — pinned by the
  spec.
- Rollback is `git revert` of the implementation commit; the module is self-contained.

## 9. Acceptance Criteria

- [ ] AC-1: no externally observable behavior change — every existing workflow test
      (views, components, composables) passes **unmodified**; `pnpm typecheck` is green with
      **zero** consumer edits (proves the signature-preserving conversation pattern).
- [ ] AC-2: the motivating violation from §2 is gone — `workflow/api/index.ts` no longer
      imports `@shared/transport` `http`; all 20 functions call a `@shared/api-client`
      service; each resolves the bare body typed as its slice type.
- [ ] AC-3: `If-Match` preserved — `patchWorkflow` and `patchAgentWakeupConfig` send
      `If-Match: String(version)`, asserted by the characterization spec.
- [ ] AC-4: the two wakeup-config reshapes are behavior-identical — `getAgentWakeupConfig`
      returns `{ wakeupConfig, version }` (defaulting absent `wakeup_config` to `{}`),
      `patchAgentWakeupConfig` posts `{ wakeup_config }` and returns the bumped version;
      pinned by the spec.
- [ ] AC-5: request bodies/params unchanged — the spec asserts verb/path/body/params for a
      representative read/write per service group (Workflow CRUD, runs, orchestration,
      wakeup), including `listRuns` query params and `triggerRun` `{ trigger_payload }`.
- [ ] AC-6: `pnpm test`, `pnpm lint` (changed files), and `pnpm build` are green; no
      `gen:api` rerun (contract unchanged).

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- FU-1: remaining slice wraps (`tenancy`, `identity`, `admin`, `prompt-studio`) — the last
  `http`-based api layers after this increment.
- FU-2: model the backend orchestration routes (approvals, instructions, subagents, DLQ) as
  Pydantic `*Out` schemas so `OrchestrationService` returns typed models instead of
  `Record<string,any>`, letting the §5B-5 casts be deleted.
- FU-3: the alive-only `OrchestrationService.listSubagentChildren…` method
  (`/orchestration/instances/{id}/children`) is generated but unconsumed (the slice uses
  the run-scoped subagents endpoint instead, `api/index.ts:170-171`) — confirm it is
  intentionally frontend-dead or wire it up.
