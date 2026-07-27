---
type: bugfix
status: implemented
created: 2026-07-27
requirements: [R15.05b, R15.09]
depends_on: [2026-07-22-presence-transition-and-release-wakeup]
---

# Wake-up sweep hygiene: unrolled-back refresh failures and a presence hook that became a no-op

## 1. Summary

From `docs/audits/2026-07-27-wakeup-subsystem/findings.md` F-3 and F-4, both minor. The hourly
`wakeup_refresh` sweep catches per-agent exceptions but never rolls back, and commits once after the
whole loop (`backend/app/workers/tasks/orchestration.py:309-319`), so a database-level failure on one
agent poisons the session, cascades through every remaining agent, and discards every successful
refresh in that sweep — while the log reports only isolated per-agent failures. Separately, the
retention presence scrub still drives `evaluate_presence_change(has_live_users=False)`
(`backend/app/workers/tasks/retention.py:722-728`), but after
`2026-07-22-wakeup-trigger-state-and-bounds` removed the cached presence flag, that call reaches a
loop body that does nothing (`backend/contexts/orchestration/application/wakeup_service.py:239-252`).
The protection its comment describes is now delivered by the roster read at
`wakeup_service.py:227-230`; what remains is a per-room query that cannot affect anything and a
comment that will mislead the next person to touch this path. Neither is user-visible: F-3 self-heals
on the next hourly tick and F-4 has no behavioral effect at all. They are bundled because they are
two halves of the same worker-sweep surface and one reviewable change.

**These two findings do not share a root cause.** F-3 is a missing error-recovery step in a sweep
loop; F-4 is a call site left behind by a design change in a different module. They share the
subsystem and the review, not a mechanism.

## 2. Observed vs Expected

**F-3 (minor, refresh sweep loses a whole sweep on one failure)**

- **Observed** `backend/app/workers/tasks/orchestration.py:313-318` catches `Exception` per agent and
  logs, with no `await db.rollback()`. The single `await db.commit()` sits after the loop at `:319`.
  Each `refresh_wakeup_config` call issues an `agents` UPDATE via `patch_agent`
  (`backend/contexts/orchestration/application/wakeup_service.py:402-408`) plus an
  `agent.wakeup_refreshed` audit row (`:432-443`), all uncommitted until then.
- **Expected** the same sweep shape 25 lines above, `evaluate_silence`
  (`backend/app/workers/tasks/orchestration.py:285-291`), rolls back per item with the comment
  "One bad pair must not abort the sweep; clear any aborted transaction so subsequent reads on this
  session succeed". That is the documented in-repo contract for a per-item guard on a shared session;
  `wakeup_refresh` does not honor it. R15.09's per-agent reset is per agent, so one agent's failure
  is not a reason to discard another agent's completed reset.

**F-4 (minor, retention presence hook is a no-op)**

- **Observed** `backend/app/workers/tasks/retention.py:715-728` states the call exists so "a
  self-opening silence agent doesn't fire into a room whose last member dropped uncleanly", and
  `:728` is the only caller in the repo that passes `has_live_users=False`.
  `backend/contexts/conversation/application/triggers.py:120-138` lists every binding in the room
  before delegating. `backend/contexts/orchestration/application/wakeup_service.py:250-252` acts only
  when `has_live_users` is true, so the false branch of the loop body is empty.
- **Expected** `docs/implement/G-orchestration.md:81` names `ws:presence:{room_id}` as the
  authoritative silence signal (R15.05b), and
  `docs/tasks/2026-07-22-wakeup-trigger-state-and-bounds/spec.md` Q-4 made that explicit by deleting
  the cached flag the retention call used to clear. A call site whose stated purpose has moved
  elsewhere should either be removed or have its purpose restated truthfully; it should not remain as
  a comment describing a mechanism that no longer exists.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Fix F-3 by rolling back per agent, or by committing per agent? | Roll back per agent inside the `except`, keeping the single post-loop commit. | It is the minimal correction of the named defect and it matches `evaluate_silence` (`orchestration.py:285-291`) exactly, so the two sweeps in one file stay one idiom. Per-agent commit was considered and rejected here: it changes the sweep's transaction granularity, and the audit rows and the config write for a single agent should land together or not at all. |
| Q-2 | Should the post-loop `commit()` also be guarded? | Yes — wrap it so a failing commit is logged as a sweep-level failure with the count of refreshes lost, rather than raising out of the arq task. | With Q-1 applied, a commit failure is the only remaining way to lose the sweep, and losing it silently is the half of F-3 that made the log misleading. The task already returns a summary string (`:324`); it should be able to say the sweep failed. |
| Q-3 | Remove the retention presence call, or keep it and fix its comment? | Remove the `has_live_users=False` call entirely (`retention.py:722-728`), and narrow `on_presence_changed` to the signal it now carries. | Keeping a call that provably cannot affect anything is how the next reader learns the wrong model of the system. The behavior it once provided is now provided unconditionally by the roster read at `wakeup_service.py:227-230`, and that read is pinned by `tests/unit/test_wakeup_service.py:52-81`, so removal is covered by existing tests rather than trusted. |
| Q-4 | Does removing the call mean `has_live_users` should leave the signature too? | Yes. With the only `False` caller gone, `on_presence_changed`'s parameter has one possible value; drop it and rename the method to say what it does (`on_users_present`), updating `OrchestrationFacade` (`interfaces/facade.py`), `evaluate_presence_change` (`triggers.py:120-138`) and `app/api/ws/chatroom.py:38-43`. | A boolean parameter with one reachable value is a lie about the interface. This is the part of the change most likely to conflict with `2026-07-22-presence-transition-and-release-wakeup` — see Q-5. |
| Q-5 | `2026-07-22-presence-transition-and-release-wakeup` (draft) edits `wakeup_service.py:227-237` and its §7 A2 still references `set_silence_active`, deleted by `2026-07-22-wakeup-trigger-state-and-bounds`. How is that handled? | That dossier is re-baselined against the post-fix code as a separate, immediate step (the user's call, 2026-07-27), and this dossier declares it in `depends_on` as an overlap prerequisite. | Both dossiers edit the presence path in `wakeup_service.py` within ten lines of each other, and that one additionally changes what the roster read can trust — which is the guarantee Q-3 relies on to justify removing the retention call. Building them concurrently would produce conflicting diffs over a shared argument; building that one first means this one's removal is verified against the reconciling roster read rather than the current one. There is no logical prerequisite beyond that. |
| Q-6 | Should the sweep's unbounded candidate query be fixed here? | No. It is the prior dossier's FU-10 and stays there. | It is a scale concern, not a defect, and it changes the query shape this dossier's tests would otherwise pin. Two changes to the same loop in one dossier, for two unrelated reasons, is how a small fix becomes unreviewable. |

## 4. Reproduction

**F-3** (not deterministically reproducible in production; deterministic under test)

Preconditions: more agents with a drifted `wakeup_config` and a non-null `wakeup_authored_snapshot`
than one, and the `wakeup_refresh` cron registered (`backend/app/workers/main.py:322`).

1. Arrange three such agents. Make the second one's `patch_agent` raise a database-level error — in
   production, a serialization failure or a connection blip; under test, a stubbed facade that raises
   on the second call.
2. Run `wakeup_refresh`.
3. Observe: the log carries one "wakeup refresh failed" line for agent 2 and, if the driver reports
   it, further failures for agent 3 — all reported as isolated per-agent problems. The
   `await db.commit()` at `:319` raises out of the task.
4. `GET /api/agents/{agent 1}`: still drifted. Its successful refresh was rolled back with the rest,
   and nothing in the log says so.
5. The next hourly tick refreshes agents 1 and 3 normally, so the end state self-heals. The loss is
   one sweep plus a misleading log.

The hypothesis for the nondeterminism is stated in the finding: `AgentVersionMismatch`, the expected
failure on this path, is raised by the repository without poisoning the session, so a genuine
DB-level error is required. §8's T-1 asserts the rollback rather than attempting to hit a real one.

**F-4** (100% reproducible; no user-visible symptom)

1. Open room R as Alice with agent A bound and `silence_minutes` enabled.
2. Kill Alice's connection without a close frame, so the roster keeps a ghost member.
3. Wait for the retention presence scrub to run.
4. Observe: `scrub_stale_presence` removes the ghost and `retention.py:728` calls
   `evaluate_presence_change(has_live_users=False)`, which lists R's bindings and then does nothing
   (`wakeup_service.py:250-252`). Redis state before and after the call is byte-identical. The
   agent correctly does not fire, but that is the roster read at `wakeup_service.py:227-230` doing
   the work — remove the retention call entirely and step 4's outcome is unchanged, which is the
   finding.

## 5. Root Cause Analysis

**F-3, root cause: the per-agent `except` in `wakeup_refresh` restores control flow but not the
session, so the loop continues against a transaction that can no longer commit.**

1. `orchestration.py:313-316` calls `refresh_wakeup_config`, which issues an UPDATE and an audit
   INSERT on the shared session.
2. `:317-318` catches and logs. **This is the earliest link whose correction prevents the symptom**:
   a `rollback()` here confines the damage to the one agent that failed.
3. `:319` commits once for the whole loop, so every earlier agent's work is bound to the outcome of
   the last one. Aggravating factor: with link 2 corrected, a rolled-back session commits the
   remaining agents' work cleanly.

**F-4, root cause: `2026-07-22-wakeup-trigger-state-and-bounds` C1 removed the state that
`on_presence_changed(has_live_users=False)` existed to write, and the call sites were left in place.**

1. That dossier's C1 deleted `set_silence_active` and made the live roster authoritative
   (`wakeup_service.py:227-230`), correctly.
2. `wakeup_service.py:250-252` kept `touch_silence_timestamp` on join, which is the surviving purpose
   of the hook, and the `False` branch became empty. **This is the earliest link**: the method's
   contract narrowed and its signature did not follow.
3. `triggers.py:120-138` and `retention.py:722-728` still call it with `False`, paying a binding query
   per emptied room for nothing, and `retention.py:715-721`'s comment still describes the deleted
   mechanism.

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- F-3: one sweep of R15.09 refreshes across the whole deployment, once per triggering error. Bounded
  by the hourly cadence: the next tick redoes the lost work, so no permanent drift. The real cost is
  diagnostic — an operator reading the log sees three per-agent failures and no indication that 400
  successful refreshes were discarded.
- F-4: one `ChatroomAgentRepository.list` query per room emptied by the retention scrub, plus a stale
  comment. No user-visible symptom, no data effect. Severity rests entirely on the comment: it tells
  a future reader that removing the call would reintroduce a bug, which is false, and that the
  presence flag mechanism still exists, which is also false.

**Sibling suspects**

Per-item guards on a shared session in a sweep loop:

- **CONFIRMED, in scope** `orchestration.py:313-318` — F-3.
- **CLEARED** `orchestration.py:285-291` (`evaluate_silence`) rolls back explicitly. This is the
  exemplar the fix copies.
- **CONFIRMED, out of scope, see FU-1** `app/workers/tasks/retention.py:726-732` catches per room
  around `evaluate_presence_change` without a rollback, on a session shared with the rest of the
  retention sweep. Materially narrower than F-3: after Q-3 removes the call, the guard has nothing
  left to guard and is deleted with it, so it needs no separate fix — but the same shape exists
  elsewhere in `retention.py` and should be swept when that file is next opened.
- **CLEARED** `app/workers/tasks/orchestration.py:165-168` (the post-commit workflow-signal dispatch)
  catches without rollback, but it runs after `async with async_session()` has exited and touches no
  session.

Call sites left behind by the flag removal:

- **CONFIRMED, in scope** `retention.py:722-728` and the `has_live_users=False` path through
  `triggers.py:120-138` and `wakeup_service.py:239-252`.
- **CLEARED** `app/api/ws/chatroom.py:38-43` calls `evaluate_presence_change` for both edges; its
  `True` call is the surviving purpose (it re-arms the silence clock on join,
  `wakeup_service.py:250-252`) and must be kept. Q-4's rename touches it but does not remove it.
- **CLEARED** repo-wide grep for `set_silence_active` / `is_silence_active` returns only `docs/`
  history — verified in the audit's AC-3 row. No other code references the deleted mechanism.

**Existing debt in the touched files** (record, do not silently fix): `wakeup_refresh` materializes
every candidate agent in one unbounded query including large fields such as `system_prompt`
(`orchestration.py:312`) — the prior dossier's FU-10, deliberately out of scope per Q-6.
`app/workers/tasks/orchestration.py` mixes three unrelated task families (wake-up, approvals, DLQ
audit) in one module; this change does not restructure it.

**Patterns to follow**: the per-item guard idiom is `orchestration.py:285-291` verbatim — rollback,
then `logger.bind(...).exception(...)` with the item's identifier. The facade rename must keep the
`app/api/` → `contexts/*/interfaces/facade.py` direction intact (`backend/CLAUDE.md`); no route or
worker may reach past the facade into the service.

**Reuse inventory**: `logger.bind(...)` from loguru is the established structured-logging call in
this module (`:289,318`). `OrchestrationFacade` (`contexts/orchestration/interfaces/facade.py:129+`)
is the only legal entry point for the rename; `evaluate_presence_change`
(`contexts/conversation/application/triggers.py:120-138`) is the only conversation-side caller.

## 7. Fix Design

Two changes, one per finding.

**C1, F-3: roll back per agent and report a failed sweep**
(`backend/app/workers/tasks/orchestration.py:309-324`)

- Add `await db.rollback()` as the first statement of the per-agent `except` at `:317`, before the
  log, matching `:288`.
- Track failures in the loop and wrap the post-loop `commit()` (Q-2): on failure, log a sweep-level
  error carrying the count of refreshes that were about to land, and return a summary string that
  says the sweep failed rather than propagating out of the task.
- The return value at `:324` becomes informative (`"refreshed=N failed=M"`), matching
  `evaluate_silence`'s `f"fired={fired}"` shape at `:296`.

Why this corrects rather than masks: the symptom is "a sweep silently loses its work", and the
shortcut is to commit per agent, which makes the symptom disappear by changing the transaction
granularity of a task that has no reason to change it. Rolling back the failed unit is what the
sibling sweep in the same file already does and what the shared-session contract requires.

**C2, F-4: delete the dead presence path and narrow the hook**
(`backend/app/workers/tasks/retention.py`,
`backend/contexts/conversation/application/triggers.py`,
`backend/contexts/orchestration/application/wakeup_service.py`,
`backend/contexts/orchestration/interfaces/facade.py`,
`backend/app/api/ws/chatroom.py`)

- Remove the `evaluate_presence_change(..., has_live_users=False)` call and its surrounding guard
  from `retention.py:722-732`, and rewrite the enclosing docstring (`:715-721`) to state what the
  scrub now does: it removes ghost members from `ws:presence`, which is what makes the roster read at
  `wakeup_service.py:227-230` correct. That sentence is the real contract and is worth keeping.
- Drop `has_live_users` from `WakeupService.on_presence_changed` (`wakeup_service.py:239-252`),
  renaming it `on_users_present`, and propagate through `OrchestrationFacade` and
  `evaluate_presence_change` (`triggers.py:120-138`), which loses its own `has_live_users` parameter.
- `app/api/ws/chatroom.py:38-43` calls it only on the join edge; the leave edge stops calling it.
  Verify against that file's `roster_size == 1` / `== 0` branches when applying (the prior dossier
  cites them at `:127-128` and `:141-142`).

Why this corrects rather than masks: the symptom is a wasted query, and the shortcut is an early
`if not has_live_users: return` in the service. That removes the query but keeps a parameter with one
reachable value and a comment describing a deleted mechanism, which is the part of the defect that
will actually cost someone time.

**Data repair position (explicit).** None for either finding. F-3 writes nothing wrong — it discards
writes, and the next sweep redoes them. F-4 writes nothing at all.

## 8. Regression Test Plan

**T-1 (the failing test, write this first)**
`backend/tests/unit/test_workers.py::test_wakeup_refresh_rolls_back_a_failed_agent_and_keeps_the_rest`

Fake session recording `rollback()` and `commit()` calls; fake `AgentsFacade` returning three
candidates; stub `WakeupService.refresh_wakeup_config` to succeed, raise, succeed. Assert exactly one
`rollback()` occurred, it happened before the third agent was processed, `commit()` was called once,
and the returned summary reports one failure.

Fails today: `orchestration.py:317-318` never rolls back, so the recorder sees zero rollbacks.

**T-2** `backend/tests/unit/test_workers.py::test_wakeup_refresh_reports_a_failed_commit`

Same fixture with `commit()` raising. Assert the task does not propagate the exception, logs a
sweep-level error, and returns a summary naming the sweep as failed. Fails today: `:319` raises out
of `wakeup_refresh`.

**T-3** `backend/tests/unit/test_retention_deep.py::test_presence_scrub_does_not_call_the_wakeup_hook`

Stub `scrub_stale_presence` to return two emptied rooms and assert `OrchestrationFacade` is not
touched. Fails today: `retention.py:728` calls it twice.

**T-4, guard against over-fixing** `backend/tests/unit/test_wakeup_service.py:52-81` — the empty-roster
suppression tests for both `allow_self_open` values — must keep passing unchanged. They are the only
thing standing between C2 and a regression of R15.05b, because they pin that the roster read, not the
removed hook, is what stops a silence wake-up in an empty room. Passes today and must pass after.

**T-5** `backend/tests/unit/test_wakeup_service.py::test_join_still_rearms_the_silence_clock`

After the Q-4 rename, assert `on_users_present` still calls `touch_silence_timestamp` for every bound
agent. This is the surviving half of the hook and the thing C2 must not delete by accident. Passes
today under the old name; the test is renamed with the method.

**T-6** repo-wide grep for `has_live_users` returns no hits outside `docs/` after C2. Mechanical, but
it is the check that catches a missed call site in a chain that crosses four modules.

## 9. Risks and Rollback

- **C1 changes what a partially failed sweep persists**, from nothing to everything except the failed
  agents. That is the fix, but it means a sweep that previously appeared to do nothing now writes —
  including audit rows. No agent is refreshed that would not have been refreshed on the next tick.
- **C1's guarded commit swallows a commit failure** that today fails the arq job. An operator relying
  on arq's failure signal for this task loses it, and gains a log line and a return string instead.
  Q-2 accepts this deliberately: a failed sweep is a recoverable condition on an hourly cron, and the
  next tick retries. If the deployment alerts on arq failures rather than on logs, this is worth
  raising before rollout.
- **C2 crosses four modules for a rename** (`wakeup_service` → `facade` → `triggers` → `chatroom.py`
  and `retention.py`). The risk is a missed call site, which T-6 catches, and a textual conflict with
  `2026-07-22-presence-transition-and-release-wakeup`, which is why that dossier is in `depends_on`
  (Q-5).
- **C2 removes a defensive call.** If the roster read at `wakeup_service.py:227-230` were ever wrong,
  the removed call is not what would have saved us — it wrote nothing. But the *reconciliation* that
  makes that read trustworthy is the subject of the `depends_on` dossier, which is the substantive
  reason for the ordering rather than merely a textual one.
- **Rollback** C1 and C2 are independently revertable and share no code. C2 must be reverted as a
  unit (the rename and the deletion together); reverting only the deletion leaves `retention.py`
  calling a method that no longer takes the argument.

## 10. Acceptance Criteria

- [x] **AC-1** T-1 fails before the fix and passes after: one agent's failure in `wakeup_refresh`
      rolls back only that agent's work, and the remaining agents' refreshes commit. (T-1 was
      rewritten around a fake session that models real transaction/savepoint semantics after an
      independent review caught the original fix's bare `rollback()` discarding prior agents'
      flushed writes -- see D-3. AC-1 is verified against the corrected implementation and the
      stronger test.)
- [x] **AC-2** T-2 passes: a failing post-loop commit is logged as a sweep-level failure and reported
      in the return value instead of propagating.
- [x] **AC-3** T-3 passes and `retention.py` no longer imports or calls `evaluate_presence_change`;
      its docstring describes the ghost-member removal rather than the deleted flag mechanism.
- [x] **AC-4** T-4 passes unchanged: an empty roster still suppresses a silence wake-up for both
      `allow_self_open` values.
- [x] **AC-5** T-5 passes: the join edge still re-arms the silence clock for every bound agent.
- [x] **AC-6** T-6 passes: `has_live_users` appears nowhere outside `docs/`, and
      `on_presence_changed` has been renamed through the facade, `triggers.py` and `chatroom.py`.
      (Repo-wide grep confirmed; two pre-existing test call sites not named in §8 also needed
      updating to the new signature -- `test_agent_trigger_wiring.py`'s
      `test_evaluate_presence_change_forwards_flag` (renamed
      `test_evaluate_presence_change_forwards_to_on_users_present`) and
      `test_retention_deep.py`'s three `_scrub_stale_presence` tests -- see D-2.)
- [x] **AC-7** Definition of Done: `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` in
      `backend/`. No frontend change, so the `pnpm` gates are not required for this dossier.
      (`pytest tests/unit -q` run in place of the bare `-q`, for the same environment reason recorded
      in the presence-transition dossier's D-1: 6056 passed, 6 skipped for pre-existing, unrelated
      environment reasons.)

## 11. SRS Delta

None. R15.05b and R15.09 are correct as written; the code carries a call site and an error path that
diverged from them.

## 12. Deviation Log

Appended by /build.

**D-1.** AC-7 names `pytest -q` in `backend/`; the build instead ran `pytest tests/unit -q`, for the
same reason recorded in the presence-transition dossier's own D-1: `pyproject.toml`'s
`testpaths = ["tests"]` includes `tests/integration` and `tests/wiring`, which need a live
Postgres/Redis/Vault stack not present in this build environment. The unit suite is a complete,
reliable substitute for this task's verification; it does not substitute for a pre-deploy run
against a live stack.

**D-2.** §8's Regression Test Plan named T-1 through T-6 as the tests this build would touch, but
C2's rename (`on_presence_changed` → `on_users_present`, dropping `has_live_users`) also broke two
pre-existing tests it did not name: `test_agent_trigger_wiring.py::test_evaluate_presence_change_forwards_flag`
(asserted the old signature directly; renamed to `test_evaluate_presence_change_forwards_to_on_users_present`
and updated) and three tests in `test_retention_deep.py::TestFacadeDelegatingPolicies` that asserted
the now-removed retention→facade call (`test_scrub_stale_presence_pauses_silence_for_emptied_rooms`
and `test_scrub_stale_presence_survives_one_room_dispatch_failure` deleted outright since they test
removed behavior; `test_scrub_stale_presence` simplified; T-3's
`test_presence_scrub_does_not_call_the_wakeup_hook` added in their place). Found via AC-6's
repo-wide `has_live_users` sweep, which is exactly the mechanism §8 built in to catch a missed call
site across the rename's four modules — it also caught these two, one module short of where the
spec's citation list stopped looking.

**D-3 (post-close-out correction).** An independent `/code-review` of the branch, run after this
dossier was first closed out as implemented, caught a critical regression in C1: `refresh_wakeup_config`
issues a real `agents` UPDATE plus an audit INSERT per agent, flushed but not committed until the
post-loop `commit()`. The per-agent `except`'s bare `await db.rollback()` — copied from
`evaluate_silence`'s shape without checking that `evaluate_silence`'s per-item body never writes to
the DB through that session, only reads — rolled back the *whole* transaction on any later agent's
failure, silently discarding every earlier agent's already-flushed write even though `refreshed` had
already counted it. This reintroduced the exact class of silent data loss the dossier existed to fix,
just via `rollback()` instead of a poisoned `commit()`; AC-1's own regression test used a bare
`AsyncMock` for the session and could not catch it, since an `AsyncMock` has no notion of what a real
rollback actually discards. Fixed by wrapping each agent's refresh in its own SAVEPOINT
(`db.begin_nested()`): a failure now rolls back only that agent's write, leaving prior agents'
flushed work intact for the single post-loop commit — the outcome AC-1 asked for, achieved by the
mechanism that actually delivers it rather than the one that merely looks like `evaluate_silence`'s.
The test was rewritten around a fake session that models a pending/committed write list plus a
savepoint mark, and was verified to fail against the reverted bare-rollback code for the right reason
(asserting on which writes reached `committed`, not merely that `rollback()` was called) before
confirming it passes against the corrected code. AC-1 is re-verified against this stronger test; no
other AC is affected. Full Definition of Done (unit suite, ruff, mypy, quality audit, security audit)
re-run against the corrected commit — see the closing summary below.

## 13. Follow-ups

- **FU-1** `app/workers/tasks/retention.py` has other per-item `except` blocks on a shared session
  without a rollback. C2 deletes the one at `:726-732` by removing what it guarded; the pattern
  should be swept across that file the next time it is opened, on the same reasoning as C1.
- **FU-2** The wake-up sweeps have no failure metric. `WAKEUP_FIRES`
  (`contexts/orchestration/infrastructure/metrics.py`) counts fires only, so after C1 an operator can
  read a failed sweep in the logs but cannot alert on it. Shared with
  `2026-07-27-wakeup-config-type-validation`'s FU-4; one counter would serve both.
- **FU-3** `wakeup_refresh` materializes every candidate agent in one unbounded query
  (`orchestration.py:312`), including `system_prompt`. Out of scope per Q-6; this is
  `2026-07-22-wakeup-trigger-state-and-bounds`'s FU-10, still open.
