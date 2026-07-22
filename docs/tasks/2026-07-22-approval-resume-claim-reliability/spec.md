---
type: bugfix
status: draft
created: 2026-07-22
requirements: []
depends_on: []
---

# Approval-gate side effects are dispatched before the transaction commits, and their claim key can outlive nothing

## 1. Summary

Three findings that are genuinely one change, plus one that is not and is routed away.

The approval gate's create path performs every externally-visible side effect inline — two WS
publishes, the timeout arm, the approver notifications, the approver-turn dispatch — while owning
**no transaction**. The commit is four call frames away. The same file already knows the rule:
`cast_vote` commits and *then* calls `_emit_resolution_effects`, whose docstring is literally
"Post-commit side effects". Only the create path violates it.

- **F-18 and F-31 are the same line of code seen from opposite ends.** F-18: the dispatch happens
  pre-commit. F-31: and the job it dispatched has no retry when the row is invisible. Not two
  bugs — one ordering defect plus its missing mitigation. **Fixing F-18 dissolves most of F-31**,
  whose remainder becomes defence in depth.
- **F-32 is the same "not sequenced against each other" clause on a time axis**: the
  `wf:approval:{id}` claim key's life is anchored at gate *creation* while its consumer's retry
  budget is anchored at gate *resolution*. Two lifetimes each reasonable in isolation, never
  reconciled.
- **F-29 does not fit and is not fixed here** — see §3.

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-31, F-32; plus
`docs/audits/2026-07-22-agent-config-runtime/findings.md` F-18 (major, confirmed) and F-29
(minor, **plausible**).

## 2. Observed vs Expected

**F-18.** `ApprovalService.create_gate`
(`backend/contexts/orchestration/application/approval_service.py:64-131`) runs: room publish
(`:97-110`), workflow publish (`:112-118`), timeout arm (`:162-167`), `pending_notify.push`
(`:170`), `drive_approver_turn` enqueue (`:179-185`) — all inline. One frame up,
`backend/contexts/workflow/application/executors/approval_gate.py:87-91` writes the claim key and
`:93-101` publishes a **second** `approval.requested`. The commit lives in
`backend/app/workers/tasks/workflow_steps.py:39`, `backend/app/api/v1/workflows.py:478`,
`workflow_cron.py:92` or `workflow_signals.py:325` — five variants, none of them the executor.

The reachable trigger is a raise in `_execute_node`'s tail *after* the executor returns —
`update_step` (`run_engine.py:613`), `emit_step_event` (`:619`), `update_variables` (`:627`),
`update_state(WAITING)` (`:648`) — propagating out of `run_step` (`:220-229`) so the commit never
runs. The executor's own broad `except Exception` (`approval_gate.py:112-118`) means
executor-internal failures do **not** reach this path; only post-return DB failures do.
`_mark_run_failed_isolated` (`run_engine.py:464-482`) exists precisely because that rollback path
is treated as live: *"must be written on a separate session so it is not lost in the caller's
rollback."*

The author saw part of it: the comment at `approval_service.py:174-178` explains a 2s deferral of
`drive_approver_turn` (`_APPROVER_TURN_DISPATCH_DELAY_S`, `:49`) — a commit barrier approximated
by sleeping, applied to the approver job only, not to the publishes or the timeout arm.

**The frontend half.** `frontend/src/shared/stores/orchestration.ts:15-45` is a plain `reactive`
map with no query, no TTL, and no expiry derived from the `timeout_seconds` it stores at `:24`.
`useChatroomSocket.ts:268-283` inserts the card; `:284-291` mutates it only on
`approval.resolved`; `removeApproval` (`orchestration.ts:37-41`) **has no caller**.
`ChatroomView.vue:706` renders straight from the store and issues no approval query. So a
`requested` with no matching `resolved` pins the card until reload.

**F-31.** `approval_timeout` (`backend/app/workers/tasks/orchestration.py:184-206`) cannot tell
"rolled back / never existed" from "not committed yet": `handle_timeout` returns `None` for a
missing row (`approval_service.py:246-248`), the task maps that to `"noop:gone"` (`:202-204`) and
exits permanently. Its sibling `drive_approver_turn` faces the identical condition and retries
5 × 2s (`backend/app/workers/tasks/approvals.py:60-76`) — the inconsistency is real.
Reachability floor: `timeout_seconds` may be 1 (`contexts/orchestration/domain/models.py:290-292`,
`docs/workflow.schema.json:278`), and the linter warns only above 3600
(`linter.py:756-762`), so a 1-2s gate is lint-clean and schema-legal.

**F-32.** `approval_gate.py:87-91` sets `ex = timeout_seconds + 300` from **creation**;
`workflow_approvals.py:28-29` gives the consumer ~630s of retry budget spent from **resolution**;
and `workflow_common.py:43-45` (`_restore_claim`) writes back the *decayed* TTL, re-read each pass
at `workflow_approvals.py:77`, never refreshed — with `_CLAIM_RESTORE_TTL_S = 60`
(`workflow_common.py:31`) as a fallback **shorter than the budget it guards**.

**A fourth contributor neither finding names**: the pending-poll branch
(`workflow_approvals.py:61-72`) never touches the key at all — it neither reads its TTL nor
extends it across up to 210 retries. The key can expire during the *pending* phase, before the
restore path is ever reached, after which `:51-52` returns `"noop:no_claim"` and the chain ends
silently. **This is the more likely of the two to bite**, because the pending phase is where the
retries actually accumulate.

**Expected.** No externally-visible effect of gate creation escapes before the row is durable, and
no claim key expires inside its own consumer's retry budget.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is an outbox warranted for F-18? | **No.** | `create_gate` has exactly **one** production caller (`approval_gate.py:73`), so a table plus migration plus dispatcher is disproportionate. And the effects are not durability-critical: WS events are ephemeral and recoverable by reload once the frontend reconciles; the `pending_notify` push has a 24h TTL and is already best-effort by design (`approval_service.py:120-123`); the timeout arm is backstopped by `workflow_watchdog`. Nothing here needs exactly-once. |
| Q-2 | Which post-commit shape, then? | **An announce-job indirection.** | Replace the publishes, the notify pushes, the timeout arm and the approver dispatches with a single pre-commit `enqueue("approval_gate_announce", approval_id, chatroom_id)`. The worker opens its own session, **re-reads the row**, and only then performs the effects, retrying on the budget `drive_approver_turn` already uses (`approvals.py:27-28`). The only thing crossing the transaction boundary is an enqueue of a job that verifies before acting; an orphaned enqueue after rollback is a harmless no-op. It also collapses the bespoke visibility retry into one place and **removes the 2s magic delay**, which exists only to approximate the barrier the announce job now enforces properly. |
| Q-3 | Why not ride `RunEngine._pending_enqueues`, the engine's existing post-commit channel? | **Rejected on coordination grounds, not design.** | It is the right *shape* — `run_engine.py:484-495` states the DB-1 contract and `:633-634` already shows a non-timeout job riding it — but its tuple `(task, run_id, node_id, delay_ms, from_edge)` cannot carry an approval id, and generalising it puts this dossier directly on top of the workflow-dispatch dossier's change surface (its F-33). |
| Q-4 | Does the claim key move too? | **No — it stays pre-commit in the executor.** | It is Redis state the resume path must be able to find; `run_engine.py:643-646` documents that parked executors legitimately write claim keys before the WAITING commit; and an orphan self-cleans at `workflow_approvals.py:59-60`. |
| Q-5 | The executor's second `approval.requested` publish (`approval_gate.py:93-101`)? | **Fold `node_id`/`question` into the service's workflow-channel payload and delete the executor's publish.** | It is also pre-commit and is a duplicate of `approval_service.py:112-118` with a different payload shape. Consolidating removes a duplicate event as a bonus. |
| Q-6 | Raise the `timeout_seconds` floor to make F-31 unreachable? | **No — rejected.** | `domain/models.py:291` (1..86400) matches `docs/workflow.schema.json:278` (`minimum: 1`); changing either would reject existing definitions and is a schema and behaviour change outside this dossier's remit. |
| Q-7 | Fix F-32 at the producer (longer TTL) or the consumer? | **Consumer.** | The workflow worker owns `wf:approval:*`; having `contexts/orchestration` extend a `wf:*` key would breach the layer boundary. Give `_restore_claim` a `min_ttl` and pass the remaining budget. This also fixes two siblings for free (§5). |
| Q-8 | Is F-29 in scope? | **No.** | See below. |
| Q-9 | `depends_on`? | `[]`. | Checked against `BOARD.md`. Q-3 deliberately avoids the workflow-dispatch dossier's surface. |

**Why F-29 is routed away rather than fixed here.** It is about `pending_notify`'s *drain
semantics* — `drain` (`contexts/orchestration/infrastructure/pending_notify.py:43-64`) is a
destructive, agent-keyed, single-consumer read, and `_requeue_notifications`
(`turn_engine.py:1641-1656`) fires only for misrouted observations and failed or skipped turns.
Nothing about the transaction, nothing about the ordering of gate side effects; the note is
consumed correctly, at the right time, by a turn entitled to it. Its fix surface is
`turn_engine.py:1563-1656` and `pending_notify.py` — **zero file overlap with this dossier**. It is
already triaged to `docs/tasks/2026-07-22-pending-notify-room-routing/` (a2a audit F-8), which
lives in exactly that code. It is also *plausible*, not confirmed: the harm step — an LLM in a
chat turn declining to vote — is not statically traceable.

For the record, the design call if it is ever pulled in: **neither requeue-on-no-vote nor bare
timeout-acceptance.** `drive_approver_turn` cannot tell "no vote cast" from "voted and completed"
(it sees only `result.status`) and is not the turn that drained. The correct shape is at the drain
site — after a turn, re-push any `approval_request` note whose approval is still `PENDING`, so the
note survives any number of non-voting turns and is consumed only by resolution. That is F-8's
dossier, reached by a different route.

**One cheap improvement that does belong here**: `approvals.py:104-113` logs a voteless driven
turn at info (`:113`) because the diagnostic at `:104-111` only fires on non-`completed`, so
nothing connects it to the timeout minutes later. Raising that to a warning needs
`TurnResult` to expose whether a vote was cast — scope it as optional.

## 4. Reproduction

**F-18.** A workflow with an `approval_gate` node bound to a chatroom
(`config.chatroom_id`, `approval_gate.py:67`); open that room. Monkeypatch
`StepRecorder.update_step` or `WorkflowRunRepository.update_variables` (called at
`run_engine.py:627`) to raise. Trigger the run.

Observed: `approval.requested` arrives and the card renders; `run_step` re-raises (`:222-229`);
`workflow_steps.py:39` never commits; the approval row does not exist. `GET /api/orchestration/approvals/{id}`
404s while the card still shows `pending`, and it stays until reload. `drive_approver_turn` logs
"approver turn gave up: approval never became visible" (`approvals.py:73-75`) after 5 attempts;
`approval_timeout` later logs "approval timeout: approval gone" (`orchestration.py:203`) and emits
nothing.

Unit-level, no DB: drive `create_gate` with a fake session whose `commit` is never called, and
assert `Publisher.emit`, `pending_notify.push` and `enqueue` were all called anyway.

**F-31.** An `approval_gate` with `timeout_seconds: 2` — lint-clean and schema-legal. Delay the
commit so it lands more than 2s after `approval_service.py:162`. `approval_timeout` fires,
`handle_timeout` returns `None`, the task returns `"noop:gone"` and never re-arms. Cast no votes:
the gate sits `pending` with no backstop until `workflow_watchdog` force-fails the run at
`idle_max_seconds`.

**F-32.** A gate with `timeout_seconds: 60` ⇒ key TTL 360s. Resolve at t ≈ 60s, leaving ~300s of
key life against a ~630s budget. Hold the run in `RUNNING` — a parallel sibling branch — so
`resume_at_port` keeps returning falsy (`workflow_approvals.py:86-87`). Each pass reads the decayed
TTL at `:77` and restores it unchanged at `:94`. At t ≈ 360s the key expires; the next attempt hits
`:51-52` and returns `"noop:no_claim"`. ~270s of budget goes unused and the node never resumes.

**Faster variant exercising the un-covered branch**: keep the gate `PENDING` and let the
pending-poll (`:61-72`) run past the key's TTL — no restore happens on that branch at all.

## 5. Blast Radius and Sibling Suspects

**Other pre-commit dispatches:** `executors/instruct.py:64-76` writes `wf:instruct:{id}` and arms
`workflow_instruct_timeout` — **confirmed sibling, milder** (the arm is wrapped in
`suppress(Exception)` at `:71` and the resume treats a missing row as `"noop:gone"`); record, do
not fix here. `instruct_service.py:156` puts the A2A envelope on the wire before the
`instructions` row commits — **confirmed, the broadest instance of the class**, owned by the
instruct dossiers. `executors/wait_for_event.py:55-82` — **cleared**, explicitly sanctioned by
`run_engine.py:643-646`, and its timeout job rides `_pending_enqueues` post-commit.
`step_recorder.py:56,119` and `run_engine.py:170,427,460,680,836` publish run and step telemetry
pre-commit — **confirmed pre-commit but cleared for this dossier**: they are self-correcting
progress indicators backed by their own queries, not durable user-visible artifacts. **Say so in
the scope note so a reviewer does not read the untouched sites as an oversight** — they are the
largest remaining instance of the class.

**TTL-versus-budget mismatches — fixing `_restore_claim` covers three at once**, which is the
strongest argument for Q-7's consumer-side design:

| Key | TTL | Consumer budget | Status |
|---|---|---|---|
| `wf:approval:{id}` (`approval_gate.py:90`) | `timeout + 300` from creation | ~630s from resolution | **Confirmed — F-32** |
| `wf:instruct:{id}` (`instruct.py:67`) | `timeout + 300` from creation | ~630s (`workflow_common.py:27-28`) | **Confirmed, identical sibling** — same helper, fix together |
| `wf:wait:{run}:{node}` (`wait_for_event.py:65`) | `timeout + 60` | ~630s (`workflow_signals.py:76-84`) | **Confirmed and worse** — only 60s of grace |
| `wf:subagent_callback:{id}` (`subagent_spawn.py:92`) | `timeout + 60` | n/a — no claim/restore/retry at all | **Different defect**; out of scope |

**Frontend stores fed only by WS with no reconciliation:** `shared/stores/orchestration.ts` —
**confirmed**, the only entry paths are `useChatroomSocket.ts:270,285`, no query anywhere,
`removeApproval` unused. `slices/activities/stores/activities.ts` — **cleared**, it has an
authoritative HTTP seed (`:24-33`) and its docblock states the design.
`slices/conversation/stores/conversation.ts` — **cleared**, its docblock confines it to presence
and per-turn ephemera, entries self-clear on `agent.finished`, and there is a client watchdog.
Everything else is not WS-fed.

## 6. Fix Design

**Part 1 — announce job (F-18).** Per Q-2. Caveats to record as decisions: the timeout deadline
shifts by announce latency (typically under a second, worst case ~10s if all 5 retries burn) —
acceptable, and strictly safer than firing before the row exists; and per Q-5 the executor's
duplicate publish is deleted after folding its payload fields into the service's.

**One thing that must be preserved.** Today the timeout arm deliberately raises to fail gate
creation (`approval_service.py:157-161`). Consolidating into one enqueue means **that enqueue
inherits the load-bearing role**: if it fails, gate creation must still fail so the caller rolls
back. `shared_kernel/queue.py:21-38` raises, so this works by default — but verify it, because
losing it silently would trade a visible defect for an invisible one.

**Part 2 — frontend reconciliation (F-18's other half; the fix is incomplete without it).**
Preferred: on `approval.requested` and on socket (re)connect, reconcile `liveApprovals` against
the server — for each pinned pending approval past its own `timeout_seconds` grace, call
`getApproval` and either `resolveApproval` or `removeApproval` (both already exist in
`orchestration.ts:27-41`). Belt: expire client-side from the `timeout_seconds` already stored at
`:24` plus `started_at` at `:28`. The query already exists server-side
(`backend/app/api/v1/orchestration.py:207-226`, with full project-membership AuthZ at `:52`) and
client-side (`frontend/src/slices/workflow/api/index.ts:126-128`) — **do not add a new endpoint**.

**Boundary note the implementer must not discover late**:
`frontend/src/slices/workflow/index.ts:6-13` does **not** re-export `getApproval`. Consuming it
from the conversation slice requires adding it to that barrel — one line, but it is a
public-surface change that `eslint-plugin-boundaries` enforces, so it is an explicit decision, not
an incidental edit.

**Part 3 — retry the timeout on an invisible row (F-31).** Under Part 1 the pre-commit arm
disappears, so F-31's primary scenario is gone. Add the guard anyway as defence in depth, since
`approval_timeout` is the gate's liveness backstop and the cost is five cheap reads: when
`handle_timeout` returns `None`, re-enqueue with `attempt + 1` and `_defer_by=2s` up to 5
attempts — an exact copy of `approvals.py:63-76`. `handle_timeout` is idempotent
(`approval_service.py:249-250,265-267`), so a spurious retry is free.

**Part 4 — anchor claim TTLs to the consumer's budget (F-32).** Give `_restore_claim`
(`workflow_common.py:43-45`) a `min_ttl` and write `ex = max(ttl or 0, min_ttl)`; callers pass
`(MAX_ATTEMPTS - attempt) * DELAY + margin`. Raise or delete `_CLAIM_RESTORE_TTL_S = 60` — a
fallback shorter than the budget it guards is the bug in miniature. **Cover the pending-poll
branch** (`workflow_approvals.py:61-72`), which today never extends the key: `EXPIRE` to at least
the remaining budget before re-enqueuing on `pending:retry`. Add `min_ttl` as an **optional
keyword with a safe default** so the two sibling call sites (`workflow_signals.py:75,277`,
`workflow_approvals.py:187`) compile unchanged, then opt each in — keeps the diff reviewable and
the rollback surface small.

Rejected and recorded: re-anchoring the TTL at resolution from `_emit_resolution_effects`. It is
the cleanest read of `approval_gate.py:88-91`'s stated intent, but it puts `wf:*` writes in
`contexts/orchestration`. SoC wins.

## 7. Regression Test Plan

**`backend/tests/unit/test_approval_gate_fixes.py`** (extend — the primary home; the `_service`
helper at `:198-222` already patches `Publisher`, `pending_notify.push` and `enqueue`):

**The failing test comes first** — `test_create_gate_publishes_nothing_before_commit`: record the
call order of `commit` versus each side effect and assert **no** publish, push,
`drive_approver_turn` or `approval_timeout` entry appears in the effect log at all — only the
announce enqueue. **Fails today**: `approval_service.py:97-118,162-185` runs all of them inline.

Then: `test_announce_retries_invisible_row_then_gives_up`;
`test_announce_arms_timeout_and_drives_approvers_once_visible`;
`test_approval_timeout_retries_when_row_not_visible` (**fails today** — `orchestration.py:202-204`
returns immediately and never enqueues; a direct mirror of the existing
`test_drive_approver_turn_retries_not_yet_visible_gate` at `:149-164`); and
`test_drive_approver_turn_no_longer_deferred`, guarding against the 2s workaround being left
behind.

**`backend/tests/unit/test_orchestration_services.py` — fix the blind spot.**
`TestApprovalCreateGate::test_create_gate` (`:297-334`) **patches `_notify_and_arm` away entirely**
(`:298-301`), which is exactly why the pre-commit position was never caught. Either drop that patch
and assert ordering, or add a sibling that does. **Preserve the existing assertion at `:326-334`**
(the room payload carries `workflow_run_id`, B5) — whatever builds the payload after the refactor
still has to carry it.

**`backend/tests/unit/test_workflow_k4.py` (F-32)** — extend `_FakeRedis` to track `ex`:
`test_resume_approval_extends_claim_ttl_across_pending_retries` (**fails today** —
`workflow_approvals.py:61-72` never touches the key); `test_resume_approval_restores_claim_with_budget_floor`
(**fails today** — `workflow_common.py:45` writes back the decayed TTL verbatim);
`test_resume_approval_expired_claim_is_not_silently_dropped`. Anchor on the existing
`test_resume_approval_retries_while_pending` (`:537-559`), which asserts only `"pending:retry"`.
Mirror the two assertions for `workflow_resume_instruct` and `workflow_event_resume`, which share
the helper.

**`backend/tests/unit/test_workers.py`** — `test_restore_claim` (`:210-216`) and
`test_restore_claim_default_ttl` (`:218-222`) extended for `min_ttl`: assert
`ex = max(ttl, min_ttl)` and that the fallback is no longer shorter than the budget.

**Frontend** — new `frontend/src/shared/stores/__tests__/orchestration.test.ts` (no store test
exists today) covering upsert/resolve/remove plus the reconciliation entry point; and
`slices/conversation/__tests__/ChatroomView.test.ts`: a card inserted by `approval.requested` for
an approval the server reports absent disappears without a reload — **fails today**, nothing ever
removes the entry. `slices/workflow/api/__tests__/index.spec.ts:227-232` already pins
`getApproval`; reuse it.

## 8. Risks and Rollback

| Risk | Mitigation |
|---|---|
| The announce hop delays the card and the notifications under queue backpressure | Keep the announce job small — no provider calls, no engine work — and leave `drive_approver_turn` a separate job so a slow turn cannot delay the publish |
| The timeout deadline shifts by announce latency; proportionally large for a 1-2s gate | No correctness mitigation needed — current behaviour on those values is strictly worse. Part 3's retry covers it either way. Record as an accepted semantic change |
| **The announce enqueue inherits the load-bearing failure role** from the timeout arm | Verify gate creation still fails when the enqueue fails; `shared_kernel/queue.py:21-38` raises by default |
| Two of the three F-32 sibling fixes touch code owned by other dossiers | `min_ttl` as an optional keyword with a safe default; opt each sibling in separately |
| Frontend reconciliation fans out on a burst of `approval.requested` | Reconcile on connect and on a timer keyed off the stored `timeout_seconds`, not per event; or use client-side expiry as primary and fetch only on reconnect |
| **The test blind spot recurs** | Whatever replaces `_notify_and_arm` must not be patched out wholesale in its own ordering test (§7) |

**Rollback** — three independently revertible commits, in this order:

1. `fix(backend): retry approval_timeout on an invisible gate row` — one file, zero coupling.
2. `fix(backend): anchor workflow resume claim TTLs to the retry budget` — Redis-only, no schema,
   no API. Revert restores the shorter TTLs; nothing persists a wrong value beyond the key's life.
3. `fix(backend): dispatch approval-gate side effects post-commit` + the frontend reconciliation —
   the largest and last. Reverting restores the pre-commit dispatch; no migration, no persisted
   state, no API contract change. **Ship the frontend reconciliation in the same or an adjacent
   commit** — it is independently valuable (it also covers dropped-WS-event cases) and can stay in
   even if the backend half is rolled back.

**No Alembic migration anywhere in this dossier. No API contract change**, so no `gen:api` and no
openapi-drift risk. The only public-surface change is the frontend barrel export in §6 Part 2.

## 9. Acceptance Criteria

- [ ] AC-1: `test_create_gate_publishes_nothing_before_commit` (§7) fails against current code and
      passes after the fix.
- [ ] AC-2: no WS publish, notify push, timeout arm or approver dispatch occurs before the
      approval row is durable.
- [ ] AC-3: a rolled-back gate creation produces **no** user-visible artifact — no pinned card, no
      approver note, no armed timeout.
- [ ] AC-4: gate creation still fails when the announce enqueue fails.
- [ ] AC-5: exactly one `approval.requested` is published on the workflow channel, carrying
      `node_id` and `question`, and the room payload still carries `workflow_run_id`.
- [ ] AC-6: `approval_timeout` retries a not-yet-visible row on the same budget
      `drive_approver_turn` uses, and gives up with `noop:gone` only after it.
- [ ] AC-7: a claim key never expires inside its consumer's remaining retry budget — including
      across the pending-poll branch — for approval, instruct and wait-for-event alike.
- [ ] AC-8: `_APPROVER_TURN_DISPATCH_DELAY_S` is deleted.
- [ ] AC-9: a pinned approval card for an approval the server reports absent clears without a
      reload.
- [ ] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck` pass in `frontend/`.

## 10. SRS Delta

None. No `[Rxx.yy]` states the approval gate's dispatch ordering; this brings the create path into
line with the resolution path in the same file, which already implements the rule correctly.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- **FU-1** — Pre-commit WS telemetry is systemic (`step_recorder.py:56,119`;
  `run_engine.py:170,427,460,680,836`). This dossier fixes only the approval instance, because it
  is the one with a durable user-visible artifact. The rest is accepted debt and is the largest
  remaining instance of the class.
- **FU-2** — `executors/instruct.py:64-76` and `instruct_service.py:156` are confirmed siblings of
  F-18, owned by the instruct dossiers.
- **FU-3** — The claim-TTL constants are scattered across four producers and two consumers with no
  single place stating the invariant *key life ≥ consumer budget*. Encode it once — a named helper
  or a documented constant relationship — rather than adjusting four numbers.
- **FU-4** — `approvals.py:104-113` logs a voteless driven turn at info, so nothing connects it to
  the timeout minutes later. Needs `TurnResult` to expose whether a vote was cast; optional scope.
- **FU-5** — F-29 (`approval_request` notes destructively drained by a non-voting turn) is routed
  to `docs/tasks/2026-07-22-pending-notify-room-routing/`. The design call, if it lands there, is
  in §3.
- **FU-6** — `wf:subagent_callback:{id}` has a TTL but **no claim, restore or retry at all**
  (`workflow_steps.py:102-128`), a different defect from F-32; owned by the subagent work.
</content>
