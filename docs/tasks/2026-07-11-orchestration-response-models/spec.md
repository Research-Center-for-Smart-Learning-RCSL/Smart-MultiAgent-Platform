---
type: refactor
status: draft
created: 2026-07-11
requirements: [R24.13]
---

# Type the orchestration read API with Pydantic response models

## 1. Summary

The `/api/orchestration/*` read routes (`backend/app/api/v1/orchestration.py`) return bare
`dict[str, Any]` / `list[dict[str, Any]]`, so their generated client methods resolve
`Record<string, any>` and the `workflow` slice's api layer papers over that with six
`as SliceType` casts — assertions that fabricate structure and give **zero** typecheck
protection against backend contract drift (the exact regression the generated client was
adopted to prevent, [R24.13]). This refactor gives the seven orchestration routes typed
Pydantic `response_model`s (six models: `ApprovalOut`, `ApprovalVoteOut`,
`ApprovalWithVotesOut`, `InstructionOut`, `AgentInstanceOut`, `DlqEntryOut`), mirroring the
existing `_*_out` helper shapes exactly, then regenerates the client and drops the six
casts. It is behavior-preserving **except one deliberate, tested fix**: the DLQ
`attempt_count` becomes a number on the wire (see Q-1). Descends from the code-review of
the workflow slice-wrap (finding #2 / workflow FU-2).

## 2. Motivation

- **Loose contract, unchecked casts.** Every `OrchestrationService` method is typed
  `CancelablePromise<Record<string, any>>` because the routes annotate
  `-> dict[str, Any]` (`orchestration.py:140,163,188,210,236,257,282`). The workflow api
  therefore casts the body: `as ApprovalWithVotes` (`workflow/api/index.ts:129`),
  `as Approval[]` (:137), `as Instruction` (:147), `as Instruction[]` (:155),
  `as AgentInstance[]` (:170), `as DlqEntry[]` (:178). A backend field rename compiles
  clean and every consumer silently reads `undefined`. This is a check-quality *altitude*
  finding: the fix belongs server-side (a real `response_model`), not as six per-call
  assertions.
- **A latent data-shape bug the loose typing hides.** `read_dlq`
  (`contexts/orchestration/infrastructure/a2a_streams.py:207-208`) coerces every Redis
  stream field to `str` (`{str(k): str(v) for k, v in fields.items()}`), so the DLQ route
  emits `attempt_count` as the string `"3"`, while the frontend types it `number`
  (`workflow/types/index.ts:193`) and renders it (`DlqViewer.vue:114`). The `as DlqEntry[]`
  cast masks the mismatch. A typed `DlqEntryOut` surfaces and fixes it (Q-1).

## 3. Non-goals

- **No behavior change beyond Q-1.** Same routes, verbs, AuthZ, JSON field names and
  values — the only observable change is DLQ `attempt_count` `"3"` → `3`. No other field's
  type or presence changes; the response_models mirror the `_*_out` dicts exactly.
- **No AuthZ change.** The resolve-project-then-`_assert_project_member` flow
  (`orchestration.py:46-59`, per-route project scoping) is untouched — only the return
  typing changes.
- **No frontend type rebase.** The slice keeps its hand-rolled `Approval`/`Instruction`/
  `AgentInstance`/`DlqEntry` types (Q-2, consistent with the whole [R24.13] program); only
  the six now-redundant casts drop.
- **Not the other workflow casts.** `as Workflow[]`/`as WorkflowRun[]`/`as ValidationResult`/
  `as { run_id }`/`as { status }` (`workflow/api/index.ts:37-117`) come from
  `WorkflowsService`/`WorkflowRunsService` returning loosely-typed models — a separate
  contract-typing follow-up (FU-1), out of scope here.
- **No mutation surface.** These are read routes only; orchestration mutations flow through
  the workflow engine and are untouched.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | DLQ `attempt_count` is a string on the wire but `number` in the frontend — reconcile how? | **Coerce to `int`** on the wire (`DlqEntryOut.attempt_count: int`). | Pydantic coerces `"3"`→`3` on serialization, making the wire truthful to the existing frontend `number` type; a count is semantically a number. One observable change, covered by a regression test. Rejected: keep `str` + retype the frontend — loses numeric semantics and still touches the frontend. |
| Q-2 | Keep hand-rolled frontend workflow types or re-export the generated `*Out`? | **Keep hand-rolled; drop the redundant casts** where the generated `*Out` is structurally assignable. | Consistent with the Q-2 decision across every prior slice (agents/keys/conversation/workflow). Re-exporting would churn every consumer importing the slice types for no safety gain. |
| Q-3 | Also type the unconsumed `list_subagent_children` route? | **Yes** — give it `AgentInstanceOut` (it reuses `_instance_out`). | Trivial (same model as `list_run_subagents`), and leaves the orchestration read surface with no `dict[str, Any]` holdouts. |

## 5. Current vs Target Structure

Backend layer direction is unchanged: `app/api/v1/orchestration.py` (presentation) defines
the response models inline — the same pattern `rag.py:107` (`RagDocumentOut`) and the
workflow routes already use — and continues to call the orchestration facades/services.

### 5A. The six response models (mirror the `_*_out` helpers exactly)

Defined in `orchestration.py`, matching the domain models
(`contexts/orchestration/domain/models.py`) and the current helper output byte-for-byte:

| Model | Fields (all from the `_*_out` dict) | Enum/nullable notes |
|---|---|---|
| `ApprovalOut` | id, workflow_run_id, mode, leader_agent_id, approver_agent_ids: `list[str]`, timeout_seconds: `int`, state, started_at, ended_at: `str \| None` | `mode: ApprovalMode` (single/majority/consensus), `state: ApprovalState` (pending/approved/rejected/timeout_leader) — domain enums (`models.py:242,248`); all id/date fields serialized `str` by `_approval_out` (:71) |
| `ApprovalVoteOut` | approval_id, voter_agent_id, vote: `bool`, rationale: `str \| None`, cast_at | `vote` is `bool` (`models.py:272`) |
| `ApprovalWithVotesOut` | `ApprovalOut` fields + `votes: list[ApprovalVoteOut]` | `get_approval` returns votes; `list_for_run` omits them → **votes optional** on the wire (Q: model `votes: list[ApprovalVoteOut] \| None` on a single `ApprovalWithVotesOut`, or two models). Decide at build: `_approval_out` adds `votes` only when passed (`:83`). Simplest faithful mapping: `ApprovalOut` (no votes) for the list route, `ApprovalWithVotesOut(ApprovalOut)` (votes required) for the single route — matches `_approval_out`'s two call shapes exactly. |
| `InstructionOut` | id, chain_id, path: `list[str]`, depth: `int`, issuer_agent_id, target_agent_id, payload: `dict[str, Any]`, state, issued_at, resolved_at: `str \| None` | `state: InstructionState` (`models.py:302`) |
| `AgentInstanceOut` | id, agent_id, parent_id: `str \| None`, chatroom_id: `str \| None`, run_context: `dict[str, Any]`, task_description: `str \| None`, state: `str`, spawned_at, destroyed_at: `str \| None` | `state` is a free `str` (`models.py:343`) — not an enum |
| `DlqEntryOut` | stream_entry_id, stream_id, envelope: `str`, **attempt_count: `int`** (Q-1), last_error, moved_at | Pydantic coerces `read_dlq`'s `"3"`→`3`; extra keys none (read_dlq emits exactly these six) |

Implementation choice (build): follow `rag.py`'s `_to_document_out` pattern — have the
`_*_out` helpers construct and return model **instances**, and annotate each route
`-> XOut`. Alternatively keep helpers returning dicts and add `response_model=` to the
decorators; either yields the same OpenAPI. The route→model map:

| Route (`orchestration.py`) | Model |
|---|---|
| `get_approval` (:135) | `ApprovalWithVotesOut` |
| `list_approvals_for_run` (:157) | `list[ApprovalOut]` |
| `get_instruction` (:183) | `InstructionOut` |
| `list_instructions_for_chain` (:204) | `list[InstructionOut]` |
| `list_run_subagents` (:230) | `list[AgentInstanceOut]` |
| `list_subagent_children` (:251) | `list[AgentInstanceOut]` (Q-3) |
| `get_agent_dlq` (:277) | `list[DlqEntryOut]` |

### 5B. Regenerate the contract

`make openapi-types` equivalent (verified this session): `python -m scripts.export_openapi`
→ `backend/openapi.json`, then `pnpm run gen:api`. Expected bounded diff: **+9 generated
files** (`ApprovalOut`, `ApprovalVoteOut`, `ApprovalWithVotesOut`, `InstructionOut`,
`AgentInstanceOut`, `DlqEntryOut` + the enums `ApprovalMode`, `ApprovalState`,
`InstructionState` — none exist yet, confirmed) and `OrchestrationService.ts` return-type
changes from `Record<string, any>` to the models. On Windows write `openapi.json` as
UTF-8/LF via a direct Python write (the PowerShell `>` redirect emits UTF-16).

### 5C. Frontend: drop the six casts

In `workflow/api/index.ts`, remove `as ApprovalWithVotes` (:129), `as Approval[]` (:137),
`as Instruction` (:147), `as Instruction[]` (:155), `as AgentInstance[]` (:170),
`as DlqEntry[]` (:178) where the generated `*Out` is now structurally assignable to the
hand-rolled slice type. If any is *not* directly assignable, `pnpm typecheck` will report
the precise field gap — that gap is a real contract divergence to reconcile (bridge or fix
the model), not to re-cast around. The hand-rolled types (`@shared/types/workflow.ts`
Approval/ApprovalVote/ApprovalWithVotes; `workflow/types/index.ts` Instruction/AgentInstance/
DlqEntry) stay (Q-2).

## 6. Characterization Test Plan

- **Backend (new/extended):** the primary safety net. For each route, a test asserting the
  serialized response shape — that adding `response_model` drops no field and the values are
  unchanged (id/date strings, enum values, nested `votes`). Seed the orchestration test
  fixtures used by `test_a2a_scope.py` and the approval/instruct/subagent services. **The
  DLQ regression:** a test that a DLQ entry with a stored string `attempt_count` serializes
  as JSON `number` `3` (pins Q-1). These must fail meaningfully before the change (the DLQ
  one asserts the *new* number behavior) and pass after.
- **Backend (existing):** every orchestration test must stay green unmodified — the
  response_model must not filter out a field a test asserts. `test_a2a_scope.py` and any
  approval/instruct/subagent/DLQ endpoint test are the guard against a shape mismatch
  500ing a live route.
- **Frontend:** `workflow/api/__tests__/index.spec.ts` (22 cases) must pass **unmodified** —
  the request contract is unchanged; the DLQ mock already uses `attempt_count: 3` (a
  number), so it already matches the fixed wire type. `pnpm typecheck` green after dropping
  the casts proves the generated models are assignable to the hand-rolled types.

## 7. Migration Steps

1. Define the six models in `orchestration.py`; convert `_approval_out`/`_instruction_out`/
   `_instance_out` to return instances (or add `response_model=` to decorators) and set
   each route's model per §5A. Keep AuthZ and helper logic identical.
2. Write the backend characterization/regression tests (§6), including the DLQ
   `attempt_count`→number case. Run `pytest` for the orchestration tests — green.
3. `ruff check`/`ruff format --check`/`mypy` on `orchestration.py` — green.
4. Regenerate: `python -m scripts.export_openapi` → `openapi.json` (UTF-8/LF), `pnpm gen:api`.
   Inspect the diff is bounded to the 9 new files + `OrchestrationService.ts` (§5B).
5. Drop the six casts in `workflow/api/index.ts` (§5C). `pnpm typecheck` — green with no
   consumer edits (proves assignability).
6. `pnpm test`, `pnpm lint` (changed files), `pnpm build` — green.

## 8. Risks and Rollback

- **response_model filters the response (highest blast radius).** A model missing a field
  the client reads → silent `undefined`; a required field absent from the dict → a
  validation 500 on a live approvals/instructions/DLQ route. Mitigated: the models mirror
  the explicit `_*_out` helpers field-for-field, and the existing orchestration backend
  tests must pass unmodified (they assert the served fields).
- **`attempt_count` coercion.** If a DLQ entry ever stored a non-integer `attempt_count`,
  `int` coercion would 500. It is always written as `str(attempt)` of an int
  (`a2a_streams.py:191`), so this is safe; the regression test pins it.
- **`votes` optionality.** `_approval_out` includes `votes` only for the single-approval
  route. Modeling the list route as `ApprovalOut` (no votes) and the single route as
  `ApprovalWithVotesOut` avoids an optional-votes ambiguity; a test covers both.
- **AuthZ untouched** — verified the change is return-typing only; the project-scoping
  guards are not in the edited lines.
- Rollback: `git revert` the backend commit + regen (or the whole task); the frontend cast
  removal reverts with it.

## 9. Acceptance Criteria

- [ ] AC-1: the seven orchestration read routes carry typed `response_model`s (six models);
      no route returns bare `dict[str, Any]` / `list[dict[str, Any]]`. `OrchestrationService`
      methods resolve the typed models, not `Record<string, any>`.
- [ ] AC-2: no behavior change beyond Q-1 — every existing orchestration backend test passes
      **unmodified** (no field dropped, no value changed, AuthZ intact), verified by
      `pytest`.
- [ ] AC-3: DLQ `attempt_count` serializes as a JSON number — a backend regression test
      feeds a stored string `attempt_count` and asserts the response value is `3` (number).
- [ ] AC-4: the six `as SliceType` casts in `workflow/api/index.ts` (:129,137,147,155,170,
      178) are removed; `pnpm typecheck` is green with **zero** frontend consumer edits
      (proves the generated `*Out` models are assignable to the hand-rolled types).
- [ ] AC-5: the regenerated `openapi.json` + client diff is bounded to the six new model
      files, the three new enum files, and `OrchestrationService.ts`; `pnpm run gen:api`
      leaves nothing else changed.
- [ ] AC-6: backend `ruff`/`mypy` green on `orchestration.py`; frontend `pnpm test`,
      `pnpm lint` (changed files), `pnpm build` green; the workflow api spec passes
      unmodified.

## 10. SRS Delta

None — the orchestration read contract already exists ([R24.13] / G.6-G.10); this types it
and corrects one field's wire type. No new or amended requirement.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- FU-1: type the remaining loosely-typed workflow returns — `WorkflowsService`/
  `WorkflowRunsService` `triggerRun`/`dryRun`/`cancelRun` resolve `Record<string, string>`
  and the `run_id`/`status` inline shapes are cast in `workflow/api/index.ts:85,95,117`;
  give the backend routes typed response models so those casts drop too.
