---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R15.15, R15.16, R15.17, R14.08]
depends_on: []
---

# The instruct terminal state is written without a guard, and its deadline job cannot survive its own retry

## 1. Summary

Two findings on the instruct settlement path, sharing one property: **the instruction row's
terminal state is decided by whoever writes last, and the deadline job that writes it cannot
be re-run.**

F-15 — `InstructionRepository.update_state`
(`backend/contexts/orchestration/infrastructure/repositories.py:319-333`) is an unconditional
`UPDATE ... WHERE id = :id`. Two independent writers in two processes race to set the row's
terminal state: the A2A consumer on turn completion (`a2a_handler.py:134`) and the deadline
worker (`workflow_approvals.py:223`). Either can overwrite the other. A successfully completed
instruct can be persisted as `TIMEOUT` and route its workflow down the `failure` branch.

F-16 — `workflow_instruct_timeout` (`workflow_approvals.py:207-228`) writes and commits
`TIMEOUT`, then enqueues the resume **after** the commit and **outside** any try/except
(`:224-226`). If that enqueue raises, arq retries the job, which now reads its own committed
`TIMEOUT`, hits the guard at `:217-222`, and hard-`return "noop"`s without ever reaching the
enqueue. The run stays WAITING until `workflow_watchdog` force-fails it — the authored `failure`
edge is never taken.

User-visible impact: a workflow branches on a lie (F-15), or does not branch at all and dies as
a generic engine failure instead of taking the port its author wired (F-16).

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-15 (`:461-485`) and
F-16 (`:487-508`), both **major, confirmed**, adversarially verified. Not re-verified here.

## 2. Observed vs Expected

### F-15 — no guard on the terminal write

**Observed.** `update_state` (`repositories.py:319-333`) takes a target state and writes it with
no predicate on the current state and no version column; the table has neither
(`contexts/orchestration/infrastructure/tables.py:105-141` — `state` is a PG enum at `:125-138`,
`resolved_at` at `:140`, nothing else that could order writers). Its sole read partner, `get`
(`:305-317`), is a plain `select()` with no `with_for_update()`.

Three writers reach it, all through `InstructService`:

| Writer | Path | Target state |
|---|---|---|
| A2A consumer, turn completed | `a2a_handler.py:134` → `facade.py:269` → `instruct_service.py:182-186` | `COMPLETED` |
| A2A consumer, turn failed | `a2a_handler.py:141` → `instruct_service.py:194-206` | `TIMEOUT` |
| Deadline worker | `workflow_approvals.py:223` → `facade.py:272` → `instruct_service.py:188-192` | `TIMEOUT` |
| A2A consumer, on pickup | `a2a_handler.py:128` → `facade.py:266` → `instruct_service.py:176-180` | `DELIVERED` |

The deadline worker's guard (`workflow_approvals.py:217-222`) is a read, a test, a write and a
commit as four separate statements under READ COMMITTED with no row lock. Between its `get` and
its `UPDATE`, the A2A consumer can commit `COMPLETED`; the `UPDATE` then acquires the lock, sees
the new committed row and overwrites it with `TIMEOUT` without error.
`workflow_resume_instruct` reads `TIMEOUT` and maps it to the `failure` port
(`workflow_approvals.py:162-163`).

The inverse race is wider and equally unguarded: if the turn is still running at the deadline,
the timeout commits first and `mark_completed` clobbers it back to `COMPLETED`. The docstring at
`workflow_approvals.py:133-135` — "The committed instruction state decides the port, so
completion and timeout can't disagree" — is wrong in both directions.

**A third instance neither finding names, following from the same missing predicate:**
`mark_delivered` (`instruct_service.py:176-180`) uses the same unguarded `update_state`. A short
`completion_timeout_seconds` (`executors/instruct.py:62`, default 120) whose deadline fires while
the envelope is still queued in the target's inbox leaves the row `TIMEOUT`; the consumer then
picks it up and writes `DELIVERED` (`a2a_handler.py:128`), reviving a settled instruction. This is
the same defect on a different transition and is fixed by the same guard.

**Expected.** `completed`, `timeout` and `rejected_loop` are terminal
(`contexts/orchestration/domain/models.py:302-307`; the state machine in
`docs/implement/G-orchestration.md:165-171`; `[R15.17]` requires the audit record to carry the
instruction's `result`, which presumes one settled result). A terminal state is written **once**;
a second writer's transition is rejected, not silently applied.

### F-16 — the deadline job poisons its own retry

**Observed.** `workflow_instruct_timeout` (`workflow_approvals.py:207-228`) runs in this order:
guard (`:217-222`, a hard `return "noop"` that does not fall through) → `mark_instruct_timeout`
(`:223`) → `db.commit()` (`:224`) → session exit → `enqueue_job("workflow_resume_instruct")`
(`:226`). The enqueue is post-commit, unwrapped, and is the only thing that starts the resume.
`app/workers/main.py:252-312` registers the task at `:276` and sets no `max_tries`, so arq's
default of 5 applies (`main.py:310-312` sets only `job_timeout`, `max_jobs`, `keep_result`).

On a Redis fault at `:226`, the retry re-reads the row, finds its own committed `TIMEOUT` at
`:218-221`, and returns `"noop"`. `wf:instruct:{id}` is never claimed,
`workflow_resume_instruct` never runs, and the run stays WAITING until `workflow_watchdog`
(`app/workers/tasks/workflow_watchdog.py:63-77`) calls `RunEngine.force_fail`, which sets
`RunState.FAILED` (`run_engine.py:402-416`) — not the `failure` port.

A partial recovery exists and bounds the blast radius: `a2a_handler.py:147-153` independently
enqueues `workflow_resume_instruct` if the target's turn eventually finishes. The unrecoverable
case is a target that never responds — precisely the case the deadline exists for.

**Aggravating factor, same liveness hole one step earlier.** `executors/instruct.py:71-76` arms
the deadline inside `with suppress(Exception)`. A failed arm is swallowed **with no log at all**,
so an instruct whose deadline was never armed is indistinguishable from one whose deadline has
not yet fired.

**Expected.** `docs/workflow.schema.md:25` gives `instruct` the ports `success`/`failure`, and
`:143` (§5.1 rule 13, port coverage) states that ports exist so a run cannot silently stall. A
timed-out instruct resumes its run at `failure`. Per `[R14.08]` the run's terminal state should
reflect the authored path, not a watchdog force-fail.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What shape should the terminal-state guard take: a row lock, a version/epoch column, a Redis claim, or a compare-and-set on `state`? | **Compare-and-set on `state`**, expressed as an allowed-predecessor predicate in the `UPDATE`'s `WHERE` clause, with the repository returning whether the transition occurred. | The invariant is *"a terminal state is never overwritten"* — a predicate on the current value, so it belongs in the write's `WHERE` clause. An **epoch/version column** expresses *ordering* between writers; there is no meaningful order between "completed" and "timeout", the rule is first-writer-wins, and it costs a migration on a table that already carries the discriminator. A **row lock** (`get` with `with_for_update()` plus write in one transaction) is also correct but relies on every writer remembering to route through it — three writers in two processes (`a2a_handler.py:128,134,141`; `workflow_approvals.py:223`); CAS puts the invariant in the repository where it cannot be bypassed. A **Redis claim** is wrong twice over: the state of record is in Postgres and a Redis-mediated guard can diverge from it, and `wf:instruct:{id}` exists only for *workflow* instructs (`executors/instruct.py:64-68`), leaving direct instructs unguarded. |
| Q-2 | Which writer wins the inverse race — a completion arriving after the deadline already committed `TIMEOUT`? | **The deadline wins. `TIMEOUT` stands; the late completion is rejected and audited.** | Once `TIMEOUT` commits, `workflow_instruct_timeout:226` has already enqueued the resume and the run may already have executed its `failure` branch — issuing further instructs, driving turns, sending notifications. Flipping the row to `COMPLETED` afterwards cannot un-execute any of that; it only makes the row disagree with the branch that ran. Consistency with the branch actually taken beats optimism about the outcome. This is a **behaviour change** — today the completion clobbers (F-15 blast radius, `findings.md:479-482`) — and must be release-noted. |
| Q-3 | Should a lost CAS raise, or report? | **Report** (`bool`), preserving the existing `ValueError` for a genuinely absent row. | `repositories.py:332-333` raises `ValueError` on `rowcount == 0` today, and callers rely on that meaning "not found". A CAS miss returns `rowcount == 0` for *both* "absent" and "not allowed from this state". Distinguish on the miss path only (a follow-up `SELECT`, rare by construction) so the absent-row contract is unchanged and a lost race is a normal, testable outcome rather than an exception at four call sites. |
| Q-4 | Does `executors/instruct.py:64-76` belong here? | **Split. The timeout arm (`:71-76`) is folded in; the pre-commit claim-key write (`:64-68`) is cleared.** | The approval dossier's §5 (`docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md:162-166`) records both as confirmed siblings of its F-18 and routes them to "the instruct dossiers". Of the two lines it names: the **claim-key write is explicitly sanctioned** by `run_engine.py:643-646` ("some parked executors write their Redis claim key *inside* this transaction's executor call … resume tasks therefore tolerate a not-yet-WAITING run by retrying"), exactly as `wait_for_event` does, which that dossier itself clears at `:167-168`. The **silently-suppressed arm** is F-16's own failure mode reached one step earlier — an unarmed deadline is the identical liveness hole — so it belongs with F-16 and nowhere else. |
| Q-5 | Does `instruct_service.py:156` (the A2A envelope on the wire before the `instructions` row commits) belong here? | **No — it is already owned by `docs/tasks/2026-07-22-a2a-scope-context-wiring/`.** | The approval dossier calls it "the broadest instance of the class … owned by the instruct dossiers" (`:165-166`). But the a2a-scope dossier **already owns that exact statement**: its §2 (`:51-56`) names the `:128-136` INSERT-and-audit-before-send ordering and the orphan `issued` row it leaves, its §7 (`:205-207`) directs "move the `instructions` INSERT and `instruct.issued` audit (`:128-136`) after the send, or compensate them", and its AC-6 (`:315-316`) pins the outcome. Two dossiers rewriting the same thirty lines would conflict. **It stays there.** The approval dossier's FU-2 should be read as "the instruct-authorization dossier", not this one. |
| Q-6 | Who owns the `wf:instruct:{id}` TTL-versus-retry-budget mismatch (F-32's identical sibling)? | **The approval dossier owns it. This dossier does not touch `workflow_common.py`.** | `docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md:178-183` tabulates `wf:instruct:{id}` (`executors/instruct.py:67`, `timeout + 300` from creation vs ~630s of consumer budget from `workflow_common.py:27-28`) as "**Confirmed, identical sibling — same helper, fix together**"; its §6 Part 4 (`:228-236`) designs the `min_ttl` parameter on `_restore_claim` (`workflow_common.py:43-45`); and its **AC-7** (`:325-326`) explicitly requires the invariant to hold "for approval, instruct and wait-for-event alike". The shared fix is designed, scoped and acceptance-tested there. Duplicating it here would produce two conflicting edits to a five-line helper. **Exactly one dossier owns it, and it is not this one.** The instruct call site it will touch is `workflow_approvals.py:187`; this dossier touches `:160-165` and `:207-228` — see §6 for the merge note. |
| Q-7 | `depends_on`? | `[]`. | No ordering constraint. Checked against `docs/tasks/BOARD.md` and the audit triage table (`findings.md:1172-1190`). Two dossiers share files at non-overlapping line ranges; recorded as a merge note in §6, not a dependency. |
| Q-8 | Data repair for rows already persisted with a wrong terminal state? | **None. Forward-only.** | See §7. |

## 4. Reproduction

**F-15, deterministic (the guard, not the race).** Insert an instruction; from session A call
`InstructService.mark_completed` and commit; from session B call `mark_timeout` and commit. Read
the row: `state = 'timeout'`, `resolved_at` from B (`repositories.py:324-331`). No error is
raised at any point. The same script with the two calls swapped yields `'completed'` — the row
is whatever was written last.

**F-15, the actual interleaving.** Two sessions, READ COMMITTED. Session B (deadline) executes
`get` (`repositories.py:305-317`) and observes `DELIVERED`, passing the guard at
`workflow_approvals.py:217-222`. Session A (`a2a_handler.py:134`) then commits `COMPLETED`.
Session B executes its `UPDATE` (`:223`) and commits (`:224`). Final state `timeout`;
`workflow_resume_instruct` maps it to `failure` (`:162-163`) and the workflow takes its failure
edge for a completed instruct. The audit trail holds an `instruct.issued`
(`instruct_service.py:158-172`) with no matching completion. Window: the two round-trips between
B's read and B's write. `tests/integration/test_embedding_pin_race.py` is the existing precedent
for driving this shape against a real Postgres.

**F-15, the third instance.** An `instruct` node with `completion_timeout_seconds: 1`
(`executors/instruct.py:62` reads it unclamped from node config). The deadline commits `TIMEOUT`
before the consumer drains the inbox; the consumer then writes `DELIVERED`
(`a2a_handler.py:128`). The row leaves its terminal state and `workflow_resume_instruct` returns
`"pending"` (`workflow_approvals.py:164-165`) forever.

**F-16.** Park a run on an `instruct` node. When `workflow_instruct_timeout` fires, make
`ctx["redis"].enqueue_job` at `:226` raise (Redis fault, or monkeypatch). `TIMEOUT` is already
committed at `:224`. Arq retries the job; the retry reads `TIMEOUT` at `:216`, matches
`:217-221`, returns `"noop"`. Observed: `wf:instruct:{id}` still present in Redis, no
`workflow_instruct_resumed` log line (`:201-203`), run state `waiting` until the watchdog logs
`idle_max_seconds exceeded` (`workflow_watchdog.py:71-72`) and force-fails it to `FAILED`
(`run_engine.py:414`).

**F-16's aggravating factor.** Make `shared_kernel.queue.enqueue` raise during
`executors/instruct.py:72-76`. Observed: the node parks normally and **nothing is logged** —
`suppress(Exception)` at `:71` swallows it with no handler body.

## 5. Root Cause Analysis

**Root cause (F-15): `InstructionRepository.update_state` (`repositories.py:319-333`) expresses a
state *assignment* where the domain requires a state *transition*.** It is the earliest link
whose correction prevents the symptom: with a predicate on the current state, every downstream
writer becomes safe regardless of interleaving, and no caller can reintroduce the defect.

Causal chain:

1. `repositories.py:327-331` — `UPDATE instructions SET state=…, resolved_at=… WHERE id=:id`. No
   state predicate. The table offers one (`tables.py:125-138`); the query ignores it.
2. `repositories.py:305-317` — `get` takes no lock, so a read-then-write across a transaction
   boundary is not serialized against a concurrent writer.
3. `workflow_approvals.py:216-224` — the deadline worker therefore implements its guard in
   application code as four separate statements: read (`:216`), test (`:217-221`), write
   (`:223`), commit (`:224`). Under READ COMMITTED this is a TOCTOU, not a guard.
4. `a2a_handler.py:126-142` — the competing writer, in a different process, commits its own
   terminal state at `:142` with no coordination.
5. `workflow_approvals.py:160-165` — the resume maps whatever state survived to a port;
   `TIMEOUT` → `failure`.
6. The workflow executes its failure branch for a completed instruct.

**Aggravating factor, not the root cause:** `mark_failed` (`instruct_service.py:194-215`) also
writes `TIMEOUT` because the DB enum has no `failed` member (`tables.py:127-133`), recording the
real cause only as an `instruct.failed` audit row (`:207-215`). This does not cause F-15, but it
is why a clobbered row cannot be told apart from a legitimately failed one afterwards — see §7's
data-repair position.

**Root cause (F-16): the guard at `workflow_approvals.py:217-222` conflates "another actor
settled this" with "a previous attempt of *this job* settled this", and terminates instead of
falling through to the step it still owes.** The job's contract is two effects — settle the row
*and* start the resume — but only the first is idempotent-by-retry. The post-commit, unwrapped
position of the enqueue (`:224-226`) is the aggravating factor that makes the poisoned retry
reachable; moving the enqueue alone would not fix it, because any failure after the commit
reproduces the same dead end. The correction must make the *whole job* re-runnable.

**Why both survived.** `workflow_instruct_timeout` has **no test anywhere**: repo-wide grep finds
it only in source (`workflow_approvals.py:207`, `executors/instruct.py:73`) and in registration
and re-export lists (`app/workers/main.py:70,276`; `app/workers/tasks/workflow.py:24,73`). And
`TestInstructStateTransitions` (`tests/unit/test_orchestration_services.py:510-536`) asserts only
which enum member was passed to a mocked `update_state` (`:517-518,526,534`) — a test that would
pass identically against a correct or an unguarded repository.

## 6. Blast Radius and Sibling Suspects

**Blast radius.**

- Every parked `instruct` node with `wait_for_completion: true` (`executors/instruct.py:47`) —
  the default. Both defects are per-instruction, not per-tenant; there is no cross-tenant leak.
- **Data already written**: `instructions` rows whose `state` does not reflect what happened, and
  the workflow runs that branched on them. Not repairable — §7.
- F-16 additionally converts an authored `failure` branch into a generic run failure
  (`run_engine.py:414`), so operators see `workflow.run_finished` with
  `reason="idle_max_seconds exceeded"` instead of the instruct's own timeout.

**Sibling suspects.**

| Site | Verdict | Evidence |
|---|---|---|
| `instruct_service.py:176-180` (`mark_delivered`) | **Confirmed — same defect, fixed here** | Same unguarded `update_state`; `DELIVERED` can overwrite a terminal row (§2, §4). |
| `instruct_service.py:182-186` (`mark_completed`), `:188-192` (`mark_timeout`), `:194-206` (`mark_failed`) | **Confirmed — the three terminal writers, all fixed here** | All route to `repositories.py:319-333`. |
| `executors/instruct.py:71-76` (suppressed deadline arm) | **Confirmed — folded in per Q-4** | `with suppress(Exception)` with no logging; an unarmed deadline is F-16's hole reached earlier. |
| `executors/instruct.py:64-68` (pre-commit claim-key write) | **Cleared** | Explicitly sanctioned by `run_engine.py:643-646`; identical to `wait_for_event`, which `docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md:167-168` clears on the same grounds. |
| `instruct_service.py:156` (envelope on the wire pre-commit) | **Confirmed, but owned elsewhere** | Owned by `docs/tasks/2026-07-22-a2a-scope-context-wiring/spec.md:51-56,205-207,315-316`. Per Q-5. |
| `wf:instruct:{id}` TTL vs consumer budget | **Confirmed, but owned elsewhere** | `docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md:178-183` (table row), `:228-236` (the `_restore_claim` `min_ttl` design), `:325-326` (AC-7 names instruct explicitly). Per Q-6. |
| `ApprovalRepository.update_state` — the same pattern on approvals | **Cleared** | It already returns a boolean and its call sites test it: `tests/unit/test_orchestration_services.py:353,366,398` assert on `approvals.update_state.return_value`, and `tests/unit/test_approval_gate_fixes.py:190` fakes the same signature. The approval path additionally serializes resolution through a single `ApprovalService` code path rather than two processes. |
| `workflow_resume_instruct` double-resume from a fall-through enqueue | **Cleared as harmless** | Single-shot by construction: `workflow_approvals.py:151-152` returns `"noop:no_claim"` when the key is absent and `:168-170` returns `"noop:claimed_elsewhere"` when the `GETDEL` loses. A duplicate enqueue costs one Redis read. |
| `contexts/agents` / `contexts/conversation` for a comparable unguarded terminal write | **Cleared for this dossier** | The A2A subsystem's other terminal transitions are covered by their own dossiers per `findings.md:1172-1190` (F-5/F-19/F-20 delivery idempotency, F-11 join epoch). No additional unguarded terminal write on the instruct surface. |

**Merge note (not a dependency — Q-7).** Three dossiers touch two files at non-overlapping
ranges. Whichever lands second rebases; there is no ordering constraint.

| File | This dossier | Other dossier |
|---|---|---|
| `app/workers/tasks/workflow_approvals.py` | `:160-165`, `:207-228` | approval-resume-claim-reliability: `:61-72`, `:187` |
| `contexts/workflow/application/executors/instruct.py` | `:71-76` | a2a-scope-context-wiring: `:39-43` |
| `contexts/orchestration/application/instruct_service.py` | `:176-206` | a2a-scope-context-wiring: `:128-156` |

## 7. Fix Design

### Part 1 — make the terminal write a compare-and-set (F-15)

`InstructionRepository.update_state` (`repositories.py:319-333`) gains an allowed-predecessor
predicate and returns whether the transition occurred:

- The `UPDATE` gains `AND state IN (<allowed_from>)` alongside the existing
  `WHERE instructions.c.id == instruction_id`.
- Return `bool` (per Q-3). On `rowcount == 0`, issue one `SELECT` to distinguish absent (keep
  raising `ValueError`, preserving today's contract at `:332-333`) from rejected (return
  `False`).
- Allowed predecessors, mirroring the state machine in `domain/models.py:302-307` and
  `docs/implement/G-orchestration.md:165-171`:
  - → `DELIVERED`: from `{ISSUED}`
  - → `COMPLETED`: from `{ISSUED, DELIVERED}`
  - → `TIMEOUT`: from `{ISSUED, DELIVERED}`
  - `REJECTED_LOOP` is never a target of `update_state` — it is only ever set at INSERT
    (`instruct_service.py:74-83`), so it needs no transition.

`InstructService.mark_delivered` / `mark_completed` / `mark_timeout` / `mark_failed`
(`instruct_service.py:176-215`) pass their allowed-from set and propagate the boolean. On a
rejected transition, emit an `instruct.terminal_conflict` audit event carrying the attempted and
actual states, so post-fix occurrences are visible and countable. `mark_failed`'s existing
`instruct.failed` audit (`:207-215`) is emitted only when the transition wins.

Callers:
- `a2a_handler.py:128,134,141` — a rejected transition is not an error. Log at warning, do **not**
  raise, and still reach the post-commit resume enqueue at `:147-153`; the resume reads whatever
  state is committed and picks the matching port.
- `workflow_approvals.py:223` — feeds Part 2.

**Why this corrects rather than masks.** The symptom is a wrong port; the mask would be to make
the resume smarter about which port to take, or to narrow the race window with a lock in one of
the two writers. Neither closes it: the row would still hold a state that contradicts what
happened, and the third writer (`mark_delivered`) would still be unguarded. Putting the predicate
in the `WHERE` clause makes "terminal is terminal" an invariant of the *storage*, enforced by
Postgres in the same statement that does the write, with no window and no caller able to opt out.

### Part 2 — make the deadline job re-runnable (F-16)

Restructure `workflow_instruct_timeout` (`workflow_approvals.py:207-228`) so that settling and
resuming are separately idempotent and the job owes the enqueue on every attempt:

- Keep the absent-row exit: `instruction is None` → `return "noop:gone"` (nothing to resume).
- Replace the hard `return "noop"` at `:217-222` with a **fall-through**: attempt the CAS to
  `TIMEOUT` (which is now a no-op returning `False` if the row already settled — Part 1), then
  **always** proceed to the enqueue. A row already in `COMPLETED` also falls through, which
  covers a lost enqueue at `a2a_handler.py:151`.
- Move `enqueue_job("workflow_resume_instruct")` so that a raise is a retryable job failure
  whose next attempt reaches the enqueue again. It may stay after the commit — that position is
  correct, because the resume must read a committed state — but it must no longer sit behind a
  branch that the commit itself makes unreachable.
- Return values become diagnosable: `timed_out` (this attempt settled it), `already_settled`
  (another writer did; resume enqueued anyway), `noop:gone`.

The duplicate-resume risk this introduces is already neutralised — see §6, `workflow_resume_instruct`
is single-shot via `GETDEL` (`:151-152,168-170`).

**Why this corrects rather than masks.** Wrapping the enqueue in a `try/except` and swallowing
would mask it — the resume would still never happen. Raising it into arq's retry only helps if
the retry can complete the work, which is exactly what the fall-through restores. The job becomes
what its docstring already claims (`:208`, "mark timeout, then resume"): a two-effect operation
that converges on re-execution.

### Part 3 — stop swallowing the deadline arm (F-16 aggravating factor)

`executors/instruct.py:71-76`: keep the arm best-effort (the node is parked and the A2A path can
still settle it, per the comment at `:69-70`), but replace the bare `suppress(Exception)` with a
handler that logs at **warning** with `instruction_id`, `run_id` and `node_id`. A deadline that
was never armed must be distinguishable from one that has not yet fired. Deliberately **not**
made load-bearing: failing the node here would convert a recoverable degradation into a hard
failure, and `workflow_watchdog` remains the floor.

### Data repair — position: none, forward-only

F-15 **can** persist a wrong terminal state, so this needs an explicit answer rather than
silence.

**Affected rows cannot be identified.** `mark_failed` (`instruct_service.py:194-206`) writes
`TIMEOUT` for a genuine turn failure, so `state = 'timeout'` alone does not mean "deadline". The
only discriminator is the `instruct.failed` audit row (`:207-215`) — and neither the clobbering
timeout nor a legitimate deadline emits anything that distinguishes them. The instruct's reply
text, which would prove completion, **is not persisted anywhere**:
`workflow_approvals.py:234-236` states that the A2A turn result "lives only in memory in
`a2a_handler`", which is why `_store_instruct_output` stores the instruction id instead.

**Even if identifiable, replay is unsafe.** A clobbered instruct's run has already executed its
`failure` branch to completion — further instructs, agent turns against users' BYO keys,
notifications, variable writes. Re-driving the `success` branch would double every one of those
side effects. There is no compensating transaction for an LLM turn that already ran.

**Decision.** No backfill, no migration, no repair script. Instead:

1. The `instruct.terminal_conflict` audit from Part 1 makes every *future* conflict observable
   and countable, converting a silent corruption into a monitored event.
2. Release notes state that `instructions` rows settled before this fix may misattribute a
   completion as a timeout, and that affected workflow runs took their `failure` edge.
3. Operators who need a bound on exposure get a read-only diagnostic — instructions in `timeout`
   with **no** `instruct.failed` audit row, whose `resolved_at` is within a second of
   `issued_at + completion_timeout_seconds` — offered as FU-2, not as part of the fix. It
   narrows the candidate set; it does not identify victims.

## 8. Regression Test Plan

Failing tests first. Every test below is asserted to fail against current code, with the reason.

### New: `backend/tests/integration/test_instruct_terminal_state_race.py` (`-m integration`, real Postgres)

Precedent for the shape: `tests/integration/test_embedding_pin_race.py`. Registered alongside
`tests/integration/test_worker_tasks.py`.

- **T-1 `test_timeout_does_not_overwrite_completed`** — *the primary failing test.* Insert an
  instruction; session A `mark_completed` + commit; session B `mark_timeout` + commit. Assert the
  row is still `COMPLETED`, `resolved_at` is A's, and B's call returned `False`.
  **Fails today**: `repositories.py:327-331` is an unconditional `UPDATE`, so the row reads
  `timeout` — and today `mark_timeout` returns `None`, so the boolean assertion has nothing to
  read.
- **T-2 `test_concurrent_deadline_and_completion_leaves_one_terminal_state`** — the exact F-15
  interleaving: session B reads (`repositories.py:305-317`) and passes the guard; session A
  commits `COMPLETED`; session B then updates and commits. Assert the final state is `COMPLETED`
  and exactly one terminal transition succeeded.
  **Fails today**: the final state is `timeout` (§4).
- **T-3 `test_completed_does_not_overwrite_timeout`** — the inverse race, pinning Q-2. Session B
  `mark_timeout` + commit; session A `mark_completed` + commit. Assert `TIMEOUT` stands and A
  returned `False`.
  **Fails today**: the row flips to `completed` (`instruct_service.py:182-186` →
  `repositories.py:327-331`).
- **T-4 `test_delivered_does_not_revive_a_settled_instruction`** — the third instance.
  `mark_timeout` + commit, then `mark_delivered`. Assert `TIMEOUT` stands.
  **Fails today**: the row reverts to `delivered` (`instruct_service.py:176-180`).
- **T-5 `test_update_state_on_absent_row_still_raises`** — the Q-3 contract guard. Assert
  `ValueError` for an unknown id, distinct from a rejected transition.
  Passes today (`repositories.py:332-333`); pins that the CAS does not degrade it.

### Extend: `backend/tests/unit/test_workflow_k4.py`

`_FakeRedis` (`:30-61`, including `ttl` at `:61`) and `_FakeSession` (`:453-461`) already exist;
`test_resume_instruct_completed_resumes_success` (`:474-511`) and
`test_resume_instruct_pending_does_not_claim` (`:514-534`) are the anchors for the fake wiring.
**No test for `workflow_instruct_timeout` exists anywhere in the repo** (§5) — this section
creates the first.

- **T-6 `test_instruct_timeout_enqueues_resume_when_row_already_timed_out`** — the F-16 failing
  test. A facade returning `state=TIMEOUT` (its own prior committed effect). Assert
  `ctx["redis"].enqueue_job` was awaited once with `("workflow_resume_instruct", str(iid))` and
  the return value is not `"noop"`.
  **Fails today**: `workflow_approvals.py:217-222` returns `"noop"` before reaching `:226`, so
  `enqueue_job` is never called.
- **T-7 `test_instruct_timeout_retry_after_enqueue_failure_still_resumes`** — first invocation
  with `enqueue_job` raising; assert it propagates (so arq retries — `app/workers/main.py:276`
  sets no `max_tries`, so the default applies). Second invocation against the now-`TIMEOUT` row;
  assert the resume is enqueued.
  **Fails today**: the second invocation returns `"noop"` — the exact poisoned-retry scenario.
- **T-8 `test_instruct_timeout_completed_row_enqueues_resume_without_writing_state`** — a
  `COMPLETED` row (belt for a lost `a2a_handler.py:151` enqueue). Assert no state write is
  attempted and the resume is enqueued.
  **Fails today**: same `return "noop"` at `:217-222`.
- **T-9 `test_instruct_timeout_absent_row_is_noop_gone`** — `get_instruction` returns `None`;
  assert no enqueue and `"noop:gone"`. Guards the fall-through from becoming unconditional.

### Extend: `backend/tests/unit/test_orchestration_services.py`

`TestInstructStateTransitions` (`:510-536`) currently asserts only the enum member passed to a
mocked `update_state` (`:517-518`, `:526`, `:534`) — the blind spot from §5.

- Extend `test_mark_delivered`, `test_mark_completed`, `test_mark_failed_uses_timeout_state` to
  additionally assert the allowed-from predecessor set is passed.
  **Fail today**: `update_state` has no such parameter (`repositories.py:319-323`).
- **T-10 `test_mark_completed_reports_rejection_and_audits_conflict`** — a repository stub
  returning `False`; assert `mark_completed` returns `False`, does not raise, and emits
  `instruct.terminal_conflict`.
  **Fails today**: `mark_completed` returns `None` and no such audit action exists.

### Extend: `backend/tests/unit/test_a2a_turn_dispatch.py`

`test_handle_instruct_marks_states` (`:843-876`) uses a hand-rolled `_Facade` (`:846-857`) whose
mark methods only record.

- **T-11 `test_handle_instruct_tolerates_rejected_completion`** — `mark_instruct_completed`
  returns `False` (row already `TIMEOUT`); assert `handle_envelope` does not raise and the
  post-commit resume enqueue at `a2a_handler.py:147-153` still happens.
  **Fails today** as written against the fixed signature; more importantly it pins the Q-2
  behaviour, which has no test at all today.

### Extend: `backend/tests/unit/test_workflow_executors.py`

- **T-12 `test_instruct_logs_when_deadline_arm_fails`** — patch `shared_kernel.queue.enqueue` to
  raise; assert the node still returns `StepOutcome(park=True)` (`executors/instruct.py:79-84`)
  **and** that a warning was logged.
  **Fails today**: `with suppress(Exception)` at `:71` has no handler body and emits nothing.

## 9. Risks and Rollback

| Risk | Severity | Mitigation |
|---|---|---|
| An allowed-from set is too narrow, rejecting a legitimate transition, so a run never resumes | **high** | The sets mirror `domain/models.py:302-307` and `G-orchestration.md:165-171` exactly. T-1..T-5 pin every legal transition as well as every rejected one. `ISSUED` is included in the predecessors of both terminal states so an instruct that times out before delivery still settles. |
| **Behaviour change (Q-2)**: a completion arriving after the deadline no longer flips the row | medium | Intentional and argued in Q-2; pinned by T-3; must appear in release notes. Strictly more consistent than today — the row now agrees with the branch the run actually executed. |
| `update_state`'s return type changes from `None` to `bool`, and callers must not treat `False` as fatal | medium | Q-3 keeps `ValueError` for absent rows, so no existing caller's error path changes. `mypy .` catches unhandled call sites. T-11 pins that `a2a_handler` tolerates `False`. |
| The F-16 fall-through double-enqueues resumes | low | Single-shot by construction (`workflow_approvals.py:151-152,168-170`); cost is one Redis read. T-8 asserts it is harmless. |
| Letting the enqueue raise turns a transient Redis fault into a visibly failed arq job | low | Correct and desirable — today it fails invisibly. A persistent outage burns arq's default tries and falls back to `workflow_watchdog` exactly as it does now: strictly no worse. |
| The extra `SELECT` on the CAS-miss path adds a round trip | low | Only on the miss path, which is by definition rare (it is the race). No cost on the common path. |
| Merge conflict with the two dossiers sharing these files | low | Non-overlapping line ranges, tabulated in §6. Not a `depends_on`. |

**Rollback.** Two independently revertible commits, in this order:

1. `fix(backend): resume the workflow when the instruct deadline job retries` — Parts 2 and 3.
   Touches `workflow_approvals.py:207-228` and `executors/instruct.py:71-76` only. No schema, no
   API, no persisted state. Revert restores the poisoned retry.
2. `fix(backend): guard instruct terminal state transitions with a compare-and-set` — Part 1.
   Touches `repositories.py:319-333`, `instruct_service.py:176-215` and the three
   `a2a_handler.py` call sites. Revert restores last-writer-wins; **nothing persisted becomes
   invalid**, because the fix only ever *declines* writes that today would have been applied.

**No Alembic migration** — the CAS predicates the existing `state` enum column
(`tables.py:125-138`); this is precisely why Q-1 rejected a version column. **No API contract
change**, so no `pnpm run gen:api` and no openapi-drift risk. **No frontend change.** **No
`workflow_common.py` change** — that file belongs to the approval dossier (Q-6).

## 10. Acceptance Criteria

- [ ] **AC-1**: T-1 (`test_timeout_does_not_overwrite_completed`, §8) fails against current code
      and passes after the fix.
- [ ] **AC-2**: a terminal instruction state (`completed`, `timeout`, `rejected_loop`) is never
      overwritten by any subsequent write, in either direction and from any of the four writers
      (`a2a_handler.py:128,134,141`; `workflow_approvals.py:223`).
- [ ] **AC-3**: the guard is enforced in the `UPDATE`'s `WHERE` clause, not in application code
      between a read and a write; no `update_state` call site can bypass it.
- [ ] **AC-4**: a rejected transition returns `False`, does not raise, and emits an
      `instruct.terminal_conflict` audit event; an absent row still raises `ValueError`.
- [ ] **AC-5**: T-6 fails against current code and passes after the fix — `workflow_instruct_timeout`
      enqueues `workflow_resume_instruct` when it finds the row already in `TIMEOUT`.
- [ ] **AC-6**: a `workflow_instruct_timeout` whose enqueue fails resumes the run on retry; the
      run reaches the instruct node's `failure` port rather than a watchdog force-fail
      (`run_engine.py:414`).
- [ ] **AC-7**: a failed deadline arm in `executors/instruct.py` is logged at warning; the node
      still parks.
- [ ] **AC-8**: no `depends_on` violation and no edits to `workflow_common.py`,
      `instruct_service.py:128-156`, or `executors/instruct.py:39-43` — those belong to the
      dossiers named in Q-5 and Q-6.
- [ ] **AC-9**: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`.
- [ ] **AC-10**: release notes record the Q-2 behaviour change and the §7 data-repair position.

## 11. SRS Delta

**None.** `[R15.15]`–`[R15.17]` do not describe a settlement race; this restores the state
machine those requirements presume. `[R14.08]`'s run states are unchanged.

One **documentation** correction, outside the SRS: `docs/implement/G-orchestration.md:165-171`
enumerates the instruct states and rejection rules but never states that `completed`, `timeout`
and `rejected_loop` are terminal and first-writer-wins. Add that sentence, since it is the
invariant this fix enforces. Also correct the now-false docstring at
`backend/app/workers/tasks/workflow_approvals.py:133-135` ("completion and timeout can't
disagree") — after this fix they cannot disagree *because of the guard*, which is worth saying
explicitly rather than leaving the original claim in place.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — `mark_failed` (`instruct_service.py:194-206`) settles a turn failure as `TIMEOUT`
  because `instruction_state` has no `failed` member (`tables.py:127-133`), preserving the real
  cause only in an audit row. This is why §7 concludes that affected rows cannot be identified
  retroactively. Adding the enum member needs a migration plus a resume-port mapping decision
  (`workflow_approvals.py:162-163`) — its own dossier.
- **FU-2** — A read-only operator diagnostic for pre-fix exposure: instructions in `timeout` with
  no `instruct.failed` audit row whose `resolved_at` sits within a second of
  `issued_at + completion_timeout_seconds`. Narrows the candidate set; does not identify victims.
  Deliberately not part of this fix (§7).
- **FU-3** — `executors/instruct.py:62` reads `completion_timeout_seconds` from node config with
  no lower bound (`int(config.get("completion_timeout_seconds", 120))`). A very small value makes
  the `TIMEOUT`-before-`DELIVERED` instance of §2 routine rather than exceptional. Whether the
  schema should floor it mirrors Q-6 of the approval dossier
  (`docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md:100`), which rejected raising
  `timeout_seconds`' floor on schema-compatibility grounds; the same reasoning likely applies, but
  it has not been checked against `docs/workflow.schema.json` here.
- **FU-4** — `workflow_instruct_timeout` had no test of any kind before this dossier
  (§5). The registration list at `app/workers/main.py:253-306` has no mechanism ensuring each
  registered task has a test; a registry-completeness test in the spirit of
  `tests/unit/test_workflow_k4.py`'s defect-1 backstop (`:5`) would catch the next one.
- **FU-5** — Pre-commit side effects on the instruct path are tracked as FU-2 of the approval
  dossier (`:348-349`). Per Q-5 the remaining live instance (`instruct_service.py:156`) is owned
  by `docs/tasks/2026-07-22-a2a-scope-context-wiring/`, not by this dossier; that FU-2's wording
  ("the instruct dossiers") should be narrowed to name it, so no reader concludes this dossier
  dropped it.
</content>
