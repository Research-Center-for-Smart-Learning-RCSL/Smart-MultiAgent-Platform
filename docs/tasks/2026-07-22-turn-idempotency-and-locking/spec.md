---
type: bugfix
status: draft
created: 2026-07-22
requirements: []
depends_on: []
---

# The turn's four guards each work alone and compose badly

## 1. Summary

Seven findings on the turn's concurrency and idempotency surface. The turn has four independent
guards, each with a contract stated only in a comment, and none aware of the others:

| Guard | Where | Contract as written |
|---|---|---|
| **G1** arq retry | `backend/app/workers/main.py:252-312` (no `retry_jobs`/`max_tries` → defaults `True`/`5`) | "a job interrupted mid-flight should run again" |
| **G2** job timeout | `main.py:310` `job_timeout = 600`, no override for `wakeup_agent` (`:258`) | "runaway backstop" |
| **G3** turn lock + heartbeat | `backend/contexts/agents/infrastructure/turn_lock.py:23,44-51` → `backend/shared_kernel/realtime/distributed_lock.py:80-116`; TTL 300 (`:25`), refresh `ttl_s/3` = 100 (`:104`) | "one concurrent turn per agent per room" |
| **G4** coalescing mark | `backend/contexts/agents/application/runtime/turn_engine.py:245-324,597-604,617,628` | "exactly one follow-up turn for a trigger that landed mid-turn" |

**Two cross-guard invariants are never stated anywhere, and both are violated:**

- **(a) `job_timeout` must be shorter than the lock TTL, or losing the lock must abort the turn.**
  Here `600 > 300`, and the heartbeat's failure mode is silent, so a turn outliving its TTL is a
  supported state with no detection. → **F-23**.
- **(b) A retry may only re-run work not known to be committed.** The reply commits at
  `turn_engine.py:2189` with six awaits after it; the lock's `finally` releases during
  cancellation unwind; and there is no idempotency key at any layer. → **F-7**.

**The framing covers four of the seven, and this dossier says so rather than stretching it.**
F-22, F-39 and F-30 are self-contained consistency defects inside G4's own two-key protocol —
co-located, not composed. They belong here because they share the same ~90 lines and would be
reverted together.

**F-8 and F-18 are one bug seen from two frames, not two.** `_run_locked`'s `try` opens at
`:1778` and its last statement is the `return` at `:2246` inside `except Exception` (`:2220`),
immediately followed by `def _observer_memory_block` at `:2248` — **no `finally`**. Separately,
`run_turn` (`:589-628`) has no `try` at all, so the post-release drain at `:628` is plain
post-loop code. `CancelledError` inherits `BaseException`, so G2's cancellation escapes both. One
fix, in two places, closes both.

Source: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md` F-7, F-18, F-22, F-23,
F-39; plus `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-8 (major) and F-30 (minor).

**One defect neither audit recorded, found while gathering material.** `_pop_queued_trigger`
returns `None` for **both** "nothing parked" and "Redis raised" (`:306-313`). At `:597-604` that
conflation drives the `break`, so a transient Redis read failure produces exactly the F-22
outcome — `skipped/locked`, message dropped — from the *pop* side, which F-22's evidence covers
only from the *mark* side. The fix must disambiguate both functions.

## 2. Per-Finding Root Cause

**F-7 — a retried `wakeup_agent` double-posts.** No idempotency key exists anywhere on the path:
`wakeup_agent` is enqueued without `_job_id` (`backend/app/api/v1/messages.py:350`,
`backend/app/workers/tasks/orchestration.py:258`), `run_turn` is called without `request_id`
(`orchestration.py:139`), and `request_id` only ever reaches the audit row
(`message_service.py:239`), never `MessageRepository.create` (`message_repo.py:42-64`, no
idempotency parameter; `tables.py:127-153`, no idempotency column). Meanwhile arq's default
`retry_jobs=True`/`max_tries=5` re-queues on `CancelledError`, and the lock is already released
by the unwind (`distributed_lock.py:110-116`). The retry re-assembles history that now contains
the just-posted reply and commits a second one.

**F-8 — the timeout skips every cleanup.** `job_timeout=600` applies to `wakeup_agent` with no
`func(..., timeout=)` override, unlike `main.py:286,299,303`. The budget is 9 provider calls
(`MAX_TOOL_ROUNDS = 8` at `turn_engine.py:96` plus the final call at `:2749`) against a
**per-read** 300s timeout (`backend/contexts/keys/infrastructure/adapters/base.py:68`), plus
unbounded tool time. Nothing runs: no `agent.finished`, no `agent.turn_failed` audit, no
`_requeue_notifications` (`:2243`), no `_restore_compact_flag` (`:2245`), no drain. **No reaper
exists** — `main.py:313-332` holds workflow and activities watchdogs only. Terminal on try 1
(`TimeoutError` is an `Exception` on 3.12), so **the companion claim that `max_tries=5` replays
the turn is refuted** — a timed-out turn is lost once, silently, not replayed five times.

**F-18 — a cancelled turn strands its trigger.** `run_turn` has no `try/finally`, so `:628` is
unreachable on cancellation. `_QUEUED_TRIGGER_TTL_S = 3600` (`:245`) and no sweeper exists for
`turn:queued` outside `turn_engine.py`.

**F-22 — a swallowed mark-write becomes a false `skipped/locked`.** `_mark_trigger_queued`'s bare
`except Exception` (`:286-292`) returns `None` on both success and failure; the `break` at
`:597-604` asserts, without verification, that a `None` pop means the previous holder enqueued a
follow-up; `:623-625` then reports `locked` for a message nobody will answer.

**F-23 — silent heartbeat loss lets two turns run concurrently.** `_heartbeat_loop` logs and
`continue`s on exception (`distributed_lock.py:73-75`), costing a full 100s interval per failure,
and returns silently when the compare-and-pexpire yields 0 (`:76-77`). `distributed_lock` yields
a bool captured once at entry (`:109`) with no liveness token. Three consecutive failures = 300s
= the TTL, and `job_timeout=600` guarantees the turn can still be running.
**The repo already gets this right one lane over**: `backend/app/workers/tasks/graphrag.py:88`
sets `GRAPHRAG_BUILD_TIMEOUT_S = LOCK_TTL_S * 3` with an explicit comment, plus fail-closed
refresh checks at `graphrag_builder.py:357-358` and `:406-407`.

**F-39 / F-30 — the two-key pop is non-atomic.** `:304-305` is two unpipelined `GETDEL`s over
`turn:queued:{a}:{r}` and `turn:queued:msg:{a}:{r}`, against a two-popper interleave the design
explicitly expects (the comment at `:598-604`) and against a concurrent `_mark_trigger_queued`
(`:274-285`, `nx=True` first-wins for the trigger, plain `SET` last-wins for the id). Degrades
`_resolve_trigger_attachments` (`:983-997`) to its `latest_user_attachments` fallback.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Do all seven share a root cause? | **No — four do.** F-22, F-39, F-30 are co-located consistency defects in G4's own protocol. | Stated in the scope note so nobody hunts for a unifying patch that does not exist. |
| Q-2 | Can `_job_id` on the enqueue solve F-7? | **No — it is inert here.** | arq's `_job_id` dedups *enqueues*; a retry re-queues under the **same** id, so the dedup never fires for the case that matters. |
| Q-3 | Set `retry_jobs=False` worker-wide? | **No.** | Other lanes depend on it. Scope the change to `wakeup_agent` via `func(..., max_tries=1)`. |
| Q-4 | Is `max_tries=1` a behaviour change? | **Yes, and a deliberate trade.** | A turn killed by a rolling deploy is then answered by nobody rather than twice. Silent non-answer beats duplicate provider spend on the user's own BYO key, and C2's reaper converts the silence into a visible `agent.finished` error. **Say this out loud — it is not a pure bugfix.** |
| Q-5 | Is `except BaseException` enough for F-8? | **No — the awaits inside cleanup die too.** | Every `await` inside cleanup on a cancelling task re-raises `CancelledError` immediately. Cleanup awaits must be `asyncio.shield`-ed and individually suppressed, or the fix is no better than today. This is precisely why C1 alone cannot be the whole answer and C2 must not be deferred out of the same release. |
| Q-6 | Must the lock fix precede the idempotency fix? | **No — the preference runs the other way.** | They are orthogonal: C4 narrows the window, C6 catches what escapes it. Landing the duplicate-turn *detector* before the *guards* leaves a net in place if C3 or C4 regresses. |
| Q-7 | Where should the idempotency key live? | `messages.metadata` (already JSONB, `tables.py:148`) with a partial unique index — **not** `audit_logs.request_id`. | That column exists (`shared_kernel/audit.py:53`) but is **unindexed** on an append-only high-volume table; a per-turn lookup would be a seq scan. |
| Q-8 | `depends_on` on either draft dossier? | **No. `depends_on: []`, plus a coordination note (§9).** | Regions are disjoint by ~230 lines from the compaction dossier and by two lines from the tool-dispatch dossier — see §9 for the two real adjacencies, which are named rather than left to a merge conflict. |

## 4. Fix Design — six independently revertible commits

**C1 — run turn cleanup on cancellation (F-8, F-18).** Convert `:2220-2246` to catch
`BaseException` for the *cleanup only*, re-raising anything that is not an `Exception`; or keep
`except Exception` and add a sibling `finally`. Move the four idempotent cleanup steps — failure
emit (`:2226-2236`), `agent.turn_failed` audit (`:2237-2241`), `_requeue_notifications` (`:2243`,
`:1641`), `_restore_compact_flag` (`:2245`, `:2623-2634`) — into one `_finalize_failed_turn`
helper called from both paths, guarded so it never runs after a successful `:2218` return. Wrap
`run_turn`'s `:589-628` body in `try/finally` so the drain runs on the cancellation path.
Per Q-5, shield and suppress each cleanup await.

**C2 — stranded-turn reaper cron (F-8's durability half).** A per-minute cron mirroring
`workflow_watchdog` (`main.py:329`) that finds `agent.turn_started` audit rows
(`turn_engine.py:1781`) with no matching finish past a budget, emits `agent.finished` with an
error kind, writes the missing audit, and drains `turn:queued:*` for the pair. **This is the only
cleanup that survives a SIGKILL**, and both audits recorded that no reaper exists.

**C3 — scope the `wakeup_agent` timeout and disable its retry (F-7 trigger half, F-8 budget
half).** Replace the bare entry at `main.py:258` with
`func(wakeup_agent, name="wakeup_agent", timeout=WAKEUP_TURN_TIMEOUT_S, max_tries=1)`.
`max_tries=1` is arq's documented "prevent retrying", enforced pre-execution — the job is still
re-queued but is terminal-failed on pickup without re-running the turn. Choose
`WAKEUP_TURN_TIMEOUT_S` against the lock TTL following `graphrag.py:82-88`'s stated rationale, and
state the chosen relation in a comment. **Two lines, highest value per line, ships first.**

**C4 — abort a turn that has lost its lock (F-23).** In `distributed_lock.py`, replace the
yielded bool with a handle exposing `.held` (or set an `asyncio.Event` on loss): mark loss both on
exception-exhaustion and on `refreshed == 0` (`:73-77`), and retry the refresh *within* the
interval instead of waiting a full 100s. Thread it through `turn_lock.py:45-51`. Consume it at the
**existing** extension point: `cancel_check` at `turn_engine.py:2652` and `:2704`, which already
raises `_TurnCancelled` (`:336-337`, handled at `:884`) — `_run_locked` currently passes **no**
`cancel_check` (`:2106-2115`), so wiring one in is additive. Fail closed, exactly as
`graphrag_builder.py:357-358`.

**C5 — make the coalesced-trigger protocol atomic and honest (F-22, F-39, F-30, + the unrecorded
pop-side conflation).** Replace `:304-305` with a single Lua `EVAL` that GETs both keys and DELs
both, mirroring `distributed_lock.py:27-34` (a `pipeline(transaction=True)` also suffices and
matches `shared_kernel/auth/tokens.py:127`). Give `_mark_trigger_queued` a `bool` return and
`_pop_queued_trigger` a tri-state — `parked` / `absent` / `unknown` — so `:597-604` distinguishes
"someone popped it" from "Redis is unwell", and pick an explicit new reason for the genuine drop
so the audit stops reporting `locked` for a dropped message.

**C6 — turn idempotency key (F-7's durable half).** Thread arq's `ctx["job_id"]` from
`wakeup_agent` (`orchestration.py:72-181`) into `run_turn` → `_run_locked` →
`MessageService.send_agent` (`message_service.py:197-242`), store it in `messages.metadata`, add
a partial unique index, and add a cheap indexed pre-check at `_run_locked` entry so a replay
short-circuits **before** the provider spend. **Index-only migration** — the column is already
JSONB. This is the hard backstop for the F-23 path that C4 cannot fully close.

**Ordering.** Hard: **C1 before C4** — C4 makes a lost lock raise inside the turn, and that
exception lands on the very cleanup path C1 repairs, so shipping C4 first widens the F-8 loss
class. Recommended: **C6 early, ideally before C4** (Q-6). C2, C3, C5 are order-free; C3 first.

## 5. Reproduction

| # | Finding | Tier | Recipe |
|---|---|---|---|
| R1 | F-7 | unit | Fake `send_agent` + fake router; cancel the task between the commit at `:2189` and `:2217`, then invoke `run_turn` again with the same arguments. Two `send_agent` calls. No Redis needed if `turn_lock` and `get_redis` are patched, per `tests/unit/test_workflow_signals.py:299-320`. |
| R2 | F-7 end-to-end | integration + real Redis + a real arq worker | Two worker processes; SIGTERM worker A mid-turn; observe the retry on B and two `messages` rows. The only form that proves the arq half. **R1 is the gate; R2 is optional evidence.** |
| R3 | F-8 / F-18 | unit | Patch the router to sleep; wrap `run_turn` in `asyncio.wait_for(..., timeout=0.1)`. Assert **today**: no `agent.finished`, no `agent.turn_failed`, `pending_notify.requeue` never called, `turn:queued` key still present. |
| R4 | F-22 | unit | Fake Redis `set` raises on the mark write, pops return `None`. Assert `status="skipped", reason="locked"` while no follow-up was enqueued. |
| R5 | F-22 pop side (unrecorded) | unit | Same, with `getdel` raising instead. Same false `locked`. |
| R6 | F-23 | integration + **real Redis** | Acquire the lock with `ttl_s=2, heartbeat_interval_s=10`; let it lapse; assert a second `acquire_lock` on the same key succeeds while the first context manager is still open. **A fake `AsyncMock` Redis cannot express key expiry**, so this one genuinely needs Redis. |
| R7 | F-39 / F-30 | unit (scripted fake) | A fake Redis whose `getdel` on the trigger key runs a callback performing the competing `_mark_trigger_queued`, forcing the exact interleave. Assert `message_id is None` while the trigger key is still set. **Do not write it as a real two-task race** — that is non-deterministic. |

Only R2 and R6 need real infrastructure; only R2 needs two processes.

## 6. Blast Radius and Sibling Suspects

**`distributed_lock` users — exhaustively two.** `turn_lock.py:45` (this dossier) and
`turn_engine.py:2526` `compact:lock:{chatroom_id}`. **The compaction lock inherits F-23
identically** — same TTL, same silent heartbeat, wrapping a provider summarisation call. C4's fix
in `distributed_lock.py` covers it for free, but the *consumption* side at `:2526-2569` has no
abort point; note it and leave that wiring to the compaction dossier.

**Other lock implementations.** `contexts/knowledge/infrastructure/redis_lock.py:103-111` returns
a bool and its callers fail closed — **cleared, and this is the model C4 should imitate**.
`embedding_pin_repository.py:42,56` is a Postgres advisory lock — **cleared**.

**arq tasks without retry safety.** The real predicate is retry-safety, not `_job_id` presence
(Q-2). `drive_approver_turn` guards on approval state before spending — **partially cleared**,
and its vote-write half is owned by `2026-07-22-approval-resume-claim-reliability/`.
`compact_chatroom` → `run_compaction` (`turn_engine.py:2587-2605`) writes a summary message —
**confirmed suspect**, owned by `2026-07-22-compaction-scoping-and-durability/`.
`run_workflow_step` and siblings — **confirmed suspects**, owned by the workflow-dispatch
dossier. Cron-driven tasks — **cleared** (arq's cron lock plus idempotent predicates).
**State plainly that C3 fixes only `wakeup_agent`, and that the worker-wide `retry_jobs` default
remains a latent hazard for every non-idempotent task above.**

**Multi-step Redis sequences.** Already pipelined or scripted, all **cleared** and all reuse
candidates: `pending_notify.py:36,52,81`, `tokens.py:127,151,166,210`, `presence.py:92-144`,
`ratelimit.py:198,234`, `redis_buckets.py:101,121`, `join.py:100`, `tus_store.py:163`,
`graphrag_triggers.py:94`, `a2a_rendezvous.py:86`, `egress.py:93`, `search_rate_limiter.py:25`,
`run_engine.py:757`, `session_store.py:103`, `email_domain_policy.py:48`, `lockouts.py:38`.
**`turn_engine.py:304-305` is the only unpipelined multi-key sequence in the repo** — F-39/F-30 is
genuinely singular, which is itself worth stating.

## 7. Regression Test Plan

Confirmed by repo-wide grep: **no test references `_pop_queued_trigger`, `_mark_trigger_queued`,
`turn_lock`, `turn:queued` or `distributed_lock`.** Every assertion below is new coverage, not a
modified expectation.

**New `backend/tests/unit/test_turn_lock_and_coalescing.py`** (patch
`shared_kernel.auth.clients.get_redis` per `test_workflow_signals.py:299-320`):

**The failing test comes first** — `test_pop_queued_trigger_is_atomic`: assert one Redis
round-trip (`eval` or a single `pipeline.execute`), not two `getdel` calls. **Fails today**:
`:304-305` issues two.

Then: `test_pop_returns_message_id_under_concurrent_mark` (R7); `test_mark_trigger_queued_reports_failure`
(returns `False` when Redis raises — **fails today**, `:286-292` returns `None` unconditionally);
`test_pop_distinguishes_absent_from_error` (**fails today**, `:306-313` conflates them);
`test_run_turn_does_not_report_locked_when_mark_failed` (**fails today**, `:623-625`);
`test_run_turn_does_not_break_when_pop_errors` (the unrecorded defect).

**New `backend/tests/unit/test_turn_cancellation_cleanup.py`** (R3): on `CancelledError`,
`agent.finished` emitted, `agent.turn_failed` audited, `_requeue_notifications` called with the
drained notes, `_restore_compact_flag` called, `turn:queued` drained. **Fails today** at `:2220`
and `:589-628`. Plus `test_wakeup_agent_registered_with_max_tries_one` — import `WorkerSettings`
and assert the entry is an `arq.worker.Function` with `max_tries == 1` and a non-`None` `timeout`.
**Fails today**: `main.py:258` is the bare coroutine. Cheap, and it pins C3 against a refactor.

**New `backend/tests/unit/test_distributed_lock_liveness.py`**: the heartbeat marks loss both when
`eval` returns 0 and when it raises repeatedly, and the handle reports `held is False` —
**fails today**, the loop is silent in both branches. Plus: `_run_locked` passes a `cancel_check`
into `_stream_with_tools` and a lost lock raises `_TurnCancelled` at the round boundary —
**fails today**, `:2106-2115` passes none.

**New `backend/tests/integration/test_turn_lock_expiry.py`** (R6) — **requires a real Redis.**
Note the existing integration tier is Postgres-oriented and several files explicitly stub Redis
out (`test_auth_middleware_failure_paths.py:28-29`,
`test_retention_restore_barrier.py:130-133`), and **`fakeredis` is not a dependency**. The spec
must decide whether to add a Redis-backed fixture or route this to the compose/e2e tier;
`tests/integration/test_embedding_pin_race.py` is the precedent for a two-session race test and
its docstring argues exactly why unit fakes cannot carry it.

**For C6**: an alembic index migration plus a test that a second `send_agent` with the same
`turn_job_id` creates no second row (integration tier — the partial unique index needs Postgres).

## 8. Risks and Rollback

| Risk | Mitigation |
|---|---|
| **C3 loses a genuine retry** — a turn killed by a rolling deploy is answered by nobody | Deliberate trade per Q-4; C2's reaper makes the silence visible. Release-note it. |
| **C1's cleanup awaits die to the same cancellation** | `asyncio.shield` + individual `suppress` (Q-5); **C2 is the durable backstop and must ship in the same release** |
| **C4 aborts healthy turns during a Redis blip** — a fail-closed liveness check converts a transient degradation into a failed turn | Retry the refresh within the interval before declaring loss; require N consecutive failures; keep the abort at a round boundary only (`:2652`, `:2704`) so a partial provider stream is never truncated mid-response |
| C5 introduces a new Redis code path | Behaviour-identical uncontended; fake-Redis tests cover both branches |
| C6's partial unique index rejects a legitimate second reply if a job id is ever reused | Key on the arq job id (per-enqueue unique); verify the index predicate excludes rows without the key so existing rows are unaffected |
| Migration | C6 only, index-only; `alembic downgrade -1` drops it with no data movement |

**Rollback.** C1–C5 are pure code, each independently revertible. Unwind in reverse order
(C6 → C1), because C4 depends on C1's cleanup path being correct.

## 9. Coordination

**No `depends_on`; two named adjacencies instead**, so neither is discovered as a merge conflict.

**With `2026-07-22-compaction-scoping-and-durability/`** (draft). Its regions are `:2483-2585`,
`:2104`, plus `summariser.py` and `transcript.py`; ours are `:243-324`, `:576-650`, `:1778-1790`,
`:2220-2246`, `:2652`/`:2704`, `distributed_lock.py`, `turn_lock.py`, `main.py`. **Disjoint by
~230 lines**, with three couplings:
1. **C4 changes `distributed_lock`'s signature** (bool → handle) and the compaction lock at
   `:2526` is a caller. Whichever lands second updates the other's call site; keep the handle
   truthy so this is a non-event.
2. That dossier's §6 names `turn_lock.py:45` as "the model to imitate" and marks it **Cleared**.
   C4 changes that model — not a conflict, but its reasoning should be re-read after C4 lands.
3. Semantic, not textual: C1's cleanup calls `_restore_compact_flag`, and the compaction fix's
   step 2 makes the flag correctly *not* restorable after a committed fold via
   `_compact_forced_rooms.discard`. **They compose correctly** — `_restore_compact_flag` already
   no-ops on a room not in the set (`:2626-2627`) — **provided that discard is not deferred**,
   which that dossier already insists on.

**With `2026-07-22-tool-dispatch-failure-categories/`** (draft). Its Q-10 already judges this
dossier "different lines, but sequence with awareness". One correction: **C4 inserts a
lock-liveness `cancel_check` at `turn_engine.py:2652`, two lines above that dossier's
request-builder extraction at `:2654-2664`.** That is the only real textual adjacency between the
two. Trivially resolvable in either order — the check sits above the extracted builder call
either way — but it is named in both specs rather than left to a merge.

## 10. Acceptance Criteria

- [ ] AC-1: `test_pop_queued_trigger_is_atomic` (§7) fails against current code and passes after.
- [ ] AC-2: a cancelled turn emits `agent.finished`, audits `agent.turn_failed`, requeues drained
      notifications, restores the compact flag, and drains its queued trigger.
- [ ] AC-3: a stranded turn with no matching finish is resolved by the reaper within its budget,
      including after a SIGKILL.
- [ ] AC-4: `wakeup_agent` is registered with `max_tries=1` and a scoped timeout shorter than the
      lock TTL, with the relation stated in a comment.
- [ ] AC-5: a turn that loses its lock aborts at the next round boundary rather than continuing;
      a transient Redis failure does **not** abort a healthy turn.
- [ ] AC-6: the coalesced-trigger pop is a single atomic Redis operation.
- [ ] AC-7: a Redis failure on either the mark or the pop side produces an explicit drop reason,
      never `reason="locked"`.
- [ ] AC-8: a replayed turn job does not create a second reply row, and short-circuits before any
      provider call.
- [ ] AC-9: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`.

## 11. SRS Delta

None. No `[Rxx.yy]` states the turn's concurrency or idempotency guarantees — the contracts live
entirely in code comments, which is itself the condition that let two cross-guard invariants go
unstated and unmet. See FU-1.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — No SRS entry states the turn's idempotency or concurrency guarantees. The two
  cross-guard invariants in §1 should be written down somewhere a future change can violate
  visibly.
- **FU-2** — The worker-wide `retry_jobs` default remains a latent hazard for every
  non-idempotent task listed in §6. C3 fixes only `wakeup_agent`.
- **FU-3** — `run_turn:621-622`'s `if result is None and attempt == 0: continue` is a no-op as
  the loop's last statement; and the attempt-0 → attempt-1 retry (`:589-622`) spins with no
  backoff, so the "re-check" comment at `:618-620` describes a window a tight loop barely covers.
- **FU-4** — `_QUEUED_TRIGGER_TTL_S`'s comment at `:243-245` ("popped after every turn so it never
  lingers under normal operation") is the assumption F-18 falsifies. Update it rather than leave a
  comment documenting a guarantee the code does not make.
- **FU-5** — SoC: the queued-trigger helpers at `turn_engine.py:243-324` are application code
  talking to Redis directly. Their home is `contexts/agents/infrastructure/`, which would also
  make them testable in isolation. Four copies of a function-local
  `from shared_kernel.auth.clients import get_redis` (`:2607-2621`, `:2623-2634`, and the two
  trigger helpers) are the smell.
- **FU-6** — `audit_logs.request_id` is unindexed on an append-only table (Q-7). If it is ever
  wanted as a lookup key, it needs an index first.
</content>
