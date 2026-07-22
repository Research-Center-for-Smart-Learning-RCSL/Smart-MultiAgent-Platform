---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R9.14, R9.15, R9.16]
depends_on: []
---

# A2A delivery duplicates an in-flight turn, drops the newest notifications, and never stops a dead agent's consumer

## 1. Summary

Three confirmed defects on the agent-to-agent delivery path. **F-5** (major): the periodic
`XAUTOCLAIM` reclaim treats PEL idle time as a liveness signal, but no live consumer ever
advances that clock, so an envelope whose handler is still running is stolen by a peer worker at
60s and the callee's turn is run a second and third time — duplicate provider spend on the user's
own BYO key and duplicate tool side effects, at zero load. **F-19** (minor): `pending_notify.requeue`
applies a head-relative LTRIM window to a queue whose head is the *oldest* entry, so a failed turn
that requeues a full batch discards the newest notifications — the ones a fresh approval ballot
would sit in — contradicting its own docstring. **F-20** (minor): the A2A consumer supervisor's
reconcile is create-only and derives agent liveness from a Redis key its own consumer loop
recreates, so every worker replica keeps polling forever for every agent ever created.

**Do these share a root cause? No.** They are grouped by change surface, not by cause. F-5 and F-20
share one file (`backend/contexts/orchestration/application/a2a_consumer.py`) and nothing else:
F-5 is a missing mutual-exclusion guard around handler execution, F-20 is a missing lifecycle
teardown in a supervisor. F-19 shares neither file nor cause with either; it is in
`backend/contexts/orchestration/infrastructure/pending_notify.py` and is a one-line index-window
error. What F-5 and F-19 do share is an *impact class* — an A2A message that is delivered wrong,
F-5 too many times and F-19 not at all — which is why they sit in one dossier and one revert
window. That is a change-surface grouping, and this dossier says so rather than inventing a
unifying patch.

## 2. Observed vs Expected

**F-5 — reclaim steals an in-flight envelope.**

- **Observed** — `backend/contexts/orchestration/application/a2a_consumer.py:299-302` short-circuits
  a re-delivery only if the processed marker exists, and `:326` writes that marker *after*
  `await handler(envelope)` at `:323` returns. There is no guard held *during* the handler.
  Meanwhile `backend/contexts/orchestration/infrastructure/a2a_streams.py:148-155` issues
  `XAUTOCLAIM` with `_CLAIM_MIN_IDLE_MS = 60_000` (`:30`) as its sole predicate, and
  `run_consumer_loop` fires it every `_CLAIM_INTERVAL_SECONDS = 30.0`
  (`a2a_consumer.py:52,153-161`). Nothing anywhere refreshes the idle clock of an entry that is
  actively being processed. A CALL handler runs a full agent turn
  (`a2a_handler.py:86` → `_run_turn_with_db:166-189` → `TurnEngine.run_input_turn`,
  `backend/contexts/agents/application/runtime/turn_engine.py:652`); the caller's default budget is
  `_DEFAULT_CALL_TIMEOUT = 300.0` (`a2a_service.py:42`). `deploy/compose/docker-compose.prod.yml:142-143`
  runs `replicas: 3`, each starting its own supervisor (`backend/app/workers/main.py:213-221`) with a
  per-process consumer name (`a2a_streams.py:27,41-50`).
- **Expected** — [R9.15] the `call` type blocks the caller until *a* matching reply arrives; the
  module's own stated contract at `a2a_consumer.py:44-49` is at-least-once delivery with a dedup
  that prevents "duplicate LLM spend + side effects". One inbound CALL envelope must run the
  callee's turn once. `a2a_streams.py:134-143` states reclaim exists for entries "stranded by a
  crashed or stalled consumer" — an entry whose owner is alive and working is neither.

**F-19 — the cap trims the wrong end.**

- **Observed** — `pending_notify.py:37-38`: `push` is `RPUSH` followed by
  `ltrim(key, -_MAX_PENDING, -1)`. RPUSH appends to the tail, so **head = oldest, tail = newest**,
  and that trim keeps the last 50 — the newest. `:82-84`: `requeue` LPUSHes `reversed(notes)` so the
  restored batch lands at the head (oldest position, correct), then applies
  `ltrim(key, 0, _MAX_PENDING - 1)` — indices 0 through 49 counted **from the head**. That window
  retains the oldest 50 and discards everything past index 49, which is the tail, which is the
  newest. **Direction confirmed from the code, stated exactly: today `requeue` keeps the oldest and
  drops the newest; the corrected trim must keep the tail.**
- **Expected** — the function's own docstring at `:75`: "the cap still trims oldest-first", and the
  module constant comment at `:22-23`: "oldest entries past the cap are trimmed on push".
  `docs/implement/N-conversation-a2a-fixes.md:1042-1049` (APP-1) already records this defect
  verbatim, names `ltrim(key, -_MAX_PENDING, -1)` as the fix, and remains unimplemented. Intent
  source is unambiguous and pre-existing.

**F-20 — consumer loops for deleted agents are never stopped.**

- **Observed** — `a2a_consumer.py:238-250`: `_reconcile` only creates tasks; `self._loops`
  (`:209`) is popped nowhere except `_stop_all` (`:264-272`), which runs on shutdown.
  `_discover_agents` (`:252-262`) derives the agent set purely from the existence of
  `a2a:agent:*` Redis keys, and `run_consumer_loop:145` calls `ensure_consumer_group`, which is
  `xgroup_create(..., mkstream=True)` (`a2a_streams.py:58`) — the loop recreates the very key that
  proves its own subject exists. Agent deletion is soft-delete only
  (`backend/contexts/agents/application/agent_service.py:644-674`) and performs no Redis work;
  `backend/app/workers/tasks/retention.py` contains no Redis reference for this key class.
- **Expected** — `docs/implement/G-orchestration.md:28`: "One consumer per **live** agent runtime."

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Do the three share a root cause? | **No — grouped by change surface.** F-5 and F-20 share `a2a_consumer.py` and nothing else; F-19 shares neither file nor cause. | Recorded so no reviewer hunts for a unifying patch that does not exist. Three independent commits, §7. |
| Q-2 | Which end does F-19 actually trim? | **It keeps the oldest and drops the newest.** Head is oldest (`push` RPUSHes, `:37`); `ltrim(key, 0, 49)` (`:84`) is head-relative. Fix = `ltrim(key, -_MAX_PENDING, -1)`, byte-identical to `:38`. | A fix that trims the other end is the same bug mirrored. Verified against both call sites, not from the finding text. |
| Q-3 | Can F-5 be fixed by raising `_CLAIM_MIN_IDLE_MS` above the 300s call budget? | **No — that masks it.** | 300 is `_DEFAULT_CALL_TIMEOUT` on the *caller* (`a2a_service.py:42`) and is per-call configurable under [R9.15]; the callee's turn is bounded by nothing on this path. No constant is safe, and raising it delays genuine post-crash reclaim by the same amount. See §7. |
| Q-4 | Can F-20 be fixed inside Redis alone — cancel loops whose stream key vanished? | **No, and a naive version is a no-op.** `ensure_consumer_group`'s `mkstream=True` (`a2a_streams.py:58`) recreates the key from inside the loop on every restart. | Liveness must come from the DB. See §7 C4. |
| Q-5 | Does F-5's fix resolve the audit's FU-6 (the A2A CALL path takes no turn lock)? | **It sidesteps it.** F-5's fix makes one *envelope* run once; FU-6 asks whether two *different* envelopes may run concurrent headless turns for one agent. | With `replicas: 3` each running a loop per agent with distinct consumer names (`a2a_streams.py:41-50`), `XREADGROUP ">"` hands different new entries to different replicas, so concurrent headless turns for one agent are reachable today and remain reachable after this fix. Neither resolved nor depended upon — see §6. |
| Q-6 | `depends_on` on the turn-idempotency dossier? | **No. `depends_on: []`, plus a coordination note in §6.** | Textual overlap is one file with regions ~35 lines apart, and one semantic adjacency. Both are named there rather than left to a merge conflict. |
| Q-7 | Is there a durable backstop if the F-5 lease is lost? | **No, and this is stated rather than papered over.** | The sibling dossier's C6 (idempotency key in `messages.metadata`) cannot cover this path: `run_input_turn` persists no reply (`turn_engine.py:662-665`, "no reply persistence"). The lease is the only guard; §9 records the residual risk. |

## 4. Reproduction

**F-5** — deterministic at unit level, no timing and no real Redis required. The reclaim is the
trigger, not the mechanism; the mechanism is that `_process_entry` holds nothing while the handler
runs.

1. Patch `a2a_consumer.get_redis` with the fake at `backend/tests/unit/test_a2a_idempotency.py:16-38`
   and `a2a_streams.xack` with an `AsyncMock` (the pattern at `:57-60`).
2. Use a handler that blocks on an `asyncio.Event`.
3. Run two `_process_entry(agent_id, "1-0", fields, handler, 1)` calls concurrently, then set the
   event.
4. **Today both run the handler.** `:299` finds no marker in either task because `:326` writes it
   only after `:323` returns.

Production shape (for the record, not required as a test): agent A CALLs agent B; worker W1 begins
B's turn; the turn takes 90s, well inside the 300s budget; at t≈60s W2's reclaim tick
(`a2a_consumer.py:153-161`) `XAUTOCLAIM`s the entry because its PEL idle time crossed
`_CLAIM_MIN_IDLE_MS`; W2 finds no marker and runs the turn again; at t≈120s W3 does it a third
time. All three eventually `deliver_reply` on the same correlation id; the caller's `BLPOP`
(`a2a_rendezvous.py:106-109`) takes whichever landed first and the rest expire at
`_REPLY_TTL_SECONDS = 900` (`a2a_rendezvous.py:32`).

**F-19** — deterministic, fake Redis. Precondition: `_MAX_PENDING = 50` (`pending_notify.py:24`),
so the queue must exceed the cap or the LTRIM is a no-op.

1. `push` 45 notes named `old1..old45`; `drain` them.
2. `push` 10 more named `n1..n10` (simulating arrivals while the turn ran).
3. `requeue(agent_id, old1..old45)`.
4. **Today the queue is `old1..old45 + n1..n5`; `n6..n10` are gone.** Expected: the 50 survivors end
   with `n1..n10`.

**F-20** — deterministic, no timing.

1. Construct `A2AConsumerSupervisor` with a stub handler; seed the fake Redis SCAN with one
   `a2a:agent:{id}` key; run `_reconcile` once — `self._loops` has one entry.
2. Remove the key from the fake (or soft-delete the agent) and run `_reconcile` again.
3. **Today `self._loops` still holds the task and it is not cancelled** — `:238-250` has no removal
   branch. Against real Redis the key is additionally recreated by `ensure_consumer_group`
   (`a2a_streams.py:58`) the moment the loop restarts, so even step 2's premise does not hold in
   production.

## 5. Root Cause Analysis

**F-5.** The causal chain:

1. `run_consumer_loop` fires reclaim every 30s (`a2a_consumer.py:52,153-161`).
2. `xautoclaim_stale` (`a2a_streams.py:148-155`) selects entries by PEL idle time alone, against
   `_CLAIM_MIN_IDLE_MS = 60_000` (`:30`).
3. **No live consumer ever advances that idle clock.** `a2a_streams.py` issues no `XCLAIM` refresh
   anywhere (the module's full command surface is `:58,:71,:83,:99,:116,:127,:148,:206`), and
   `run_consumer_loop:148-179` has no per-entry heartbeat. Idle time therefore measures *time since
   delivery*, not *time since the owner was last alive* — the two are identical only for entries
   nobody is working on.
4. The stolen entry surfaces in the thief's `xread_pending` (`a2a_consumer.py:92`, XREADGROUP with
   id `0`, `a2a_streams.py:99-104`) and reaches `_process_entry`.
5. `_process_entry:299` finds no processed marker, because `:326` writes it only after `:323`
   returns. The handler runs again.

**Root cause — the earliest link whose correction prevents the symptom: link 3.** The reclaim
predicate has no liveness input; `_CLAIM_MIN_IDLE_MS`'s stated meaning at `:28-30` ("delivered to a
consumer but left un-ACKed longer than this are treated as stranded by a crashed/stalled process")
is a claim the code never makes true. **Link 5 is the second root cause and cannot be omitted**: it
is what converts a steal into a duplicate turn, and it is the only guard that survives a starved
refresh. Both are fixed (§7 C2 and C3); the dossier does not pretend one suffices.

*Aggravating factors, not causes*: `replicas: 3` (`docker-compose.prod.yml:142-143`) supplies the
peers; the 300s CALL budget (`a2a_service.py:42`) against a 60s idle threshold makes the window
routine rather than exotic. Sub-60s turns ACK before any peer can claim, which is why this is major
rather than critical.

**F-19.** Single link. `pending_notify.py:84` expresses the cap window head-relative
(`0, _MAX_PENDING - 1`) while the queue's ordering convention, fixed by `push`'s RPUSH at `:37`, is
oldest-at-head. Root cause: **the index window at `:84` is expressed in the wrong reference frame**;
`push:38` already holds the correct one. Nothing upstream contributes — `requeue`'s LPUSH ordering
at `:82-83` is correct and preserves oldest-first, and the pipeline is not the issue.

**F-20.** Two links, both required:

1. `_reconcile` (`:238-250`) has no branch that cancels or pops `self._loops`; removal exists only
   in `_stop_all` (`:264-272`), reached from `run`'s `finally` (`:230-232`) on shutdown.
2. `_discover_agents` (`:252-262`) derives membership from `a2a:agent:*` key existence, and
   `run_consumer_loop:145` → `ensure_consumer_group` → `xgroup_create(..., mkstream=True)`
   (`a2a_streams.py:58`) recreates that key on every loop start.

**Root cause: link 2 — discovery derives liveness from an artefact the consumer itself
manufactures.** This is the load-bearing statement: correcting link 1 alone produces a fix that
cancels the loop and then recreates it on the next 10s scan, i.e. a churn loop rather than a
teardown. Soft-delete (`agent_service.py:644-674`) doing no Redis work is the aggravating factor,
not the cause — deleting the key would not help either, for the same `mkstream` reason.

## 6. Blast Radius and Sibling Suspects

**Blast radius.**

- **F-5** — duplicate provider spend on the user's own key and duplicate tool side effects (a turn
  that writes files or posts messages does so N times). Fires with a single message in flight; no
  contention required, only a turn exceeding 60s. **No persisted duplicate rows**: `run_input_turn`
  writes no reply (`turn_engine.py:662-665`). The surplus replies land on the rendezvous list and
  rot until `_REPLY_TTL_SECONDS = 900` (`a2a_rendezvous.py:32`); the caller reads exactly one
  (`await_reply:100-117`). Reachable for CALL and INSTRUCT (`a2a_handler.py:46-52`); the INSTRUCT
  path additionally re-drives the instruction state machine (`:126-142`).
- **F-19** — bounded: only when restored plus concurrently-pushed exceeds 50 in one failed turn
  (`_MAX_PENDING`, `:24`); below the cap the LTRIM is a no-op. Reachable from
  `turn_engine.py:1641-1656` (`_requeue_notifications`), called from seven sites
  (`:897,:907,:1605,:1687,:2079,:2096,:2243`). The lost notes include `approval_request` payloads
  (`a2a_handler.py` NOTIFY branch `:54-67`; approval notes pushed by
  `orchestration/application/approval_service.py`), so the visible consequence is a gate falling to
  its timeout port.
- **F-20** — no correctness impact. One asyncio task per ever-created agent per replica, each doing
  a 1s-blocking XREADGROUP (`a2a_streams.py:111,121`) plus a 30s XAUTOCLAIM, forever. Cleared by any
  worker restart; accumulates within a process lifetime.

**Sibling suspects.**

- **Other `XAUTOCLAIM` / reclaim sites — none.** Repo-wide grep for `xautoclaim` returns
  `a2a_streams.py:130-161` and its references only. **Cleared: F-5's reclaim-side defect is
  singular.**
- **Other consumer-group readers — none.** `xreadgroup` appears only at `a2a_streams.py:99` and
  `:116`, both in this module. `a2a_rendezvous.py:1-14` documents explicitly that the synchronous
  `call` deliberately does *not* read the stream, which is why there is no second reader to race.
  **Cleared.**
- **Other handler-dispatch loops lacking an in-flight guard.** The `_process_entry` pattern
  (`:275-364`) is not duplicated elsewhere; arq-driven tasks are the analogous surface and their
  retry-safety is owned by `docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md` §6.
  **Out of scope here, and stated so rather than silently omitted.**
- **Other `LTRIM` sites — exactly two, both in `pending_notify.py`.** `:38` (correct) and `:84` (the
  defect). No other list in the repo is capped. **Cleared: F-19 is singular.** Note that the sibling
  dossier's §6 lists `pending_notify.py:36,52,81` as **cleared** — that clearance is about
  *pipelining/atomicity* of multi-step Redis sequences and says nothing about trim direction. The
  two assessments do not conflict and neither supersedes the other; recorded so the earlier
  "cleared" is not read as covering F-19.
- **Other per-entity supervisors holding a task dict.** `A2AConsumerSupervisor._loops` (`:209`) is
  the only one. The other long-lived tasks started in `_startup` are singletons:
  `revocation_listener.run()` (`main.py:206-209`) and `reap_idle_kernels()` (`:226-229`). The latter
  is a reaper by construction and is the model C4 should imitate. **Cleared.**
- **Restore path.** `agent_service.py:676-694` (`admin_restore`) clears `deleted_at`. C4 must not
  break it; because reconcile re-scans every `_SCAN_INTERVAL_SECONDS = 10.0` (`:196`), a restored
  agent's loop is recreated automatically. **Confirmed compatible, and asserted in §8.**

**Coordination with `docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md` (draft) — checked,
with line ranges.**

| | That dossier's regions | This dossier's regions |
|---|---|---|
| `turn_engine.py` | `:243-324`, `:576-650`, `:1778-1790`, `:2220-2246`, `:2652`, `:2704` | **none — this dossier edits no line of `turn_engine.py`** |
| `distributed_lock.py`, `turn_lock.py` | both, substantially | none |
| `app/workers/main.py` | `WorkerSettings` at `:252-312`, specifically the `wakeup_agent` entry at `:258` | `_startup` at `:213-221` (supervisor construction) |
| `a2a_consumer.py` | none | `:92-124`, `:127-180`, `:238-250`, `:264-272`, `:275-364` |
| `a2a_streams.py` | none | `:19-30`, `:130-161` |
| `pending_notify.py` | none (cited only, §6) | `:22-24`, `:67-86` |

**Verdict: one shared file, disjoint by ~35 lines and by object — `_startup` (`:213-221`) versus
`WorkerSettings` (`:252-312`). Every other region is disjoint by file.** Two non-textual couplings,
named here rather than discovered at merge:

1. **Semantic, and it changes ordering preference.** That dossier's C1 moves
   `_requeue_notifications` (it cites `turn_engine.py:1641` and `:2243`) into a
   `_finalize_failed_turn` helper that also runs on the **cancellation** path, which today skips it.
   That strictly *increases* the number of `requeue` calls and therefore the exposure of F-19. The
   two fixes compose correctly — C1 changes the caller, this dossier's C1 changes `pending_notify.py:84`
   — but **F-19 should land before or with that dossier's C1**, not after.
2. **FU-6 (`findings.md:1215-1217`) — the A2A CALL path takes no turn lock at all, unlike the room
   path (`turn_engine.py:590`, key `turn:lock:{agent}:{chatroom}`), and the audit calls the broader
   question a design question rather than a defect. This dossier's fix *sidesteps* it: it neither
   resolves nor depends on it.** F-5's fix guarantees one envelope runs once, at the transport
   layer. It does not serialize two different envelopes to the same agent, and cannot: with
   `replicas: 3` and per-process consumer names (`a2a_streams.py:41-50`), `XREADGROUP ">"`
   distributes distinct new entries to distinct replicas, each of which then runs
   `run_input_turn` (`turn_engine.py:652`) with no lock. **After this dossier lands, concurrent
   headless turns for one agent remain reachable.** That is FU-6's territory and it stays open;
   `a2a_handler.py:3-4`'s claim that "one loop per agent" serialises the inbox is true per process
   and false across replicas, which is worth recording (FU-2).

## 7. Fix Design

Three independent commits, in this order. Each is separately revertible; none requires a migration.

**C1 — correct the requeue trim window (F-19).** Replace `pending_notify.py:84`'s
`pipe.ltrim(key, 0, _MAX_PENDING - 1)` with `pipe.ltrim(key, -_MAX_PENDING, -1)`, identical to
`push`'s trim at `:38`, and correct the docstring at `:68-76` so it describes what the code does.
**Why this corrects rather than masks**: the queue's ordering frame is fixed by `push`'s RPUSH at
`:37` (head = oldest); the defect is that one of the two trims is expressed in the opposite frame.
Making both trims head-agnostic (tail-relative) removes the frame mismatch itself rather than
compensating for it downstream — no caller changes, no cap change, no special case. This is the fix
`docs/implement/N-conversation-a2a-fixes.md:1046-1047` already specifies.

**Data repair position for F-19: none is possible, and none is warranted.** A dropped notification
was `DEL`ed from Redis with no durable copy — a NOTIFY is pushed to Redis only
(`a2a_handler.py:60-67`), and no DB row records it. Approval-request notes are recoverable in
principle from the gate's own state, but a gate whose notes were dropped has already fallen to its
timeout port and is settled; re-notifying a settled gate would be a second defect. The `_TTL_SECONDS
= 86400` expiry (`:25`) means any queue still holding pre-fix state ages out within a day.

**C2 — hold an in-flight lease for the duration of the handler (F-5, link 5).** In `_process_entry`,
between the processed-marker check at `:299-302` and `await handler(envelope)` at `:323`, acquire
`a2a:inflight:{agent_id}:{stream_id}` with `SET ... NX EX`. On failure to acquire, **return 0
without ACK, without DLQ, and without writing a retry record** — the same shape as the backoff skip
at `:96-98`: the entry stays in the PEL, the true owner will ACK it, and the delivery-count/DLQ
budget (`_MAX_RETRIES`, `:37`, consumed at `:338`) is not spent on a non-failure. Release the lease
in the three terminal branches that already clear `retry_key`: success (`:328`), parse-failure DLQ
(`:316`), retry-exhausted DLQ (`:341`); on non-final failure (`:352-364`) release it too, so the
backoff retry can re-acquire. Refresh the lease from a task running alongside the handler at an
interval well below its TTL.

**Why this corrects rather than masks**: the module already names the correct invariant at `:44-49`
— an entry must run its handler once — and already has a token for it. The defect is that the token
is written at the wrong moment (`:326`, after) to serve as mutual exclusion. C2 supplies the token
that covers the interval the marker cannot, and it does so at the same layer, keyed the same way,
released at the same three points. It is not a wider timeout, not a retry cap, and not a suppression
of the reclaim: a genuinely dead owner's lease expires and the entry is processed exactly as
intended.

**C3 — give the reclaim predicate a liveness input (F-5, link 3).** Add
`a2a_streams.xclaim_refresh(agent_id, stream_ids)` issuing `XCLAIM` with `justid=True` (which resets
the entry's idle clock without incrementing its delivery count), and call it from the same refresh
task that renews C2's lease. `_CLAIM_MIN_IDLE_MS` (`a2a_streams.py:30`) then measures what its
comment at `:28-30` says it measures — time since the owner was last alive — and
`xautoclaim_stale`'s docstring claim at `:134-143` becomes true.

**Why C3 and not "raise `_CLAIM_MIN_IDLE_MS`"** (Q-3): a static threshold is a bet on a maximum turn
duration, and no such maximum exists on this path. `_DEFAULT_CALL_TIMEOUT = 300.0`
(`a2a_service.py:42`) bounds the *caller's* wait and is per-call configurable under [R9.15]; it does
not bound the callee's turn, which continues until `run_input_turn` returns (the caller's timeout
only sets the cancel flag consulted at `a2a_handler.py:177-182` via
`a2a_rendezvous.is_call_cancelled:52-54`, checked at round boundaries). Any constant large enough to
be safe is also large enough to strand a genuinely crashed worker's messages for that same duration.
C3 removes the guess; raising the constant relocates it.

**Why both C2 and C3**: C3 alone leaves the steal reachable whenever the refresh task is starved —
the same failure class the sibling dossier records for the lock heartbeat. C2 alone leaves the churn
(entries change owners under a working consumer, and reclaim log lines at `a2a_consumer.py:168-173`
become permanent noise). Together, C3 makes the steal rare and C2 makes it harmless.

**Data repair position for F-5: none is possible for the side effects, and none is needed for
state.** Duplicate provider spend and duplicate tool effects are irreversible and already incurred;
no compensating action exists and none is proposed. No DB rows were duplicated
(`turn_engine.py:662-665` — the A2A path persists no reply), so there is nothing to de-duplicate.
Surplus rendezvous replies self-clear at 900s (`a2a_rendezvous.py:32`) and are already correctly
ignored by `await_reply` (`:100-117`). Operators wanting a retrospective count can look for repeated
`agent.turn_started` audit rows for one agent inside one call window; that is an observability item,
not a repair (FU-5).

**C4 — stop consumer loops for agents that are no longer live (F-20).** Give
`A2AConsumerSupervisor.__init__` (`:200-209`) an optional liveness predicate — a
`Callable[[set[uuid.UUID]], Awaitable[set[uuid.UUID]]]` filtering a discovered set down to the live
ones — and wire it in `app/workers/main.py:213-217`, where a DB session is available. In
`_reconcile` (`:238-250`), intersect `_discover_agents()`'s result with the predicate's, then add
the missing removal branch: cancel and pop every `self._loops` entry not in the live set, mirroring
`_stop_all`'s cancel-then-await shape (`:266-271`). On predicate error, **fail open** — keep every
loop running, log, and retry on the next scan; a DB blip must not silence live agents' inboxes.

**Why this corrects rather than masks** (Q-4): the root cause is that liveness was read from a Redis
key the consumer itself recreates via `mkstream=True` (`a2a_streams.py:58`). C4 replaces that
self-referential signal with the authoritative one — `AgentsFacade.get_agent` already excludes
soft-deleted agents by default (`backend/contexts/agents/interfaces/facade.py:81-82`), which is the
same predicate `run_input_turn` relies on for its `agent_gone` result (`turn_engine.py:681-683`). A
removal branch without the predicate change would cancel each loop and recreate it 10s later; that
variant is explicitly rejected and §8 pins it with a test. **SoC**: `a2a_consumer.py`'s docstring
(`:10-16`) commits the module to Redis I/O plus a caller-provided callback; injecting the predicate
rather than importing `AgentsFacade` keeps that boundary and matches how `handler` and `on_dlq` are
already supplied (`main.py:213-216`). Restore (`agent_service.py:676-694`) needs no work — the next
scan recreates the loop.

**Data repair position for F-20: none needed.** The leaked objects are process-local asyncio tasks;
they vanish on the next worker restart, which the deploy of this fix performs. The orphan
`a2a:agent:{id}` stream and its `:dlq` sibling remain in Redis and are deliberately left alone,
since deletion would break restore — recorded as FU-1.

## 8. Regression Test Plan

The audit bounds coverage explicitly at `findings.md:67-71`: no existing test touches
`A2AConsumerSupervisor` or `xautoclaim`. Everything below is new coverage except the two named
extensions.

**The failing test comes first.** New `backend/tests/unit/test_a2a_inflight_lease.py`:

- `test_concurrent_process_entry_runs_handler_once` — two concurrent `_process_entry` calls for the
  same `(agent_id, stream_id)` with a handler blocked on an `asyncio.Event`; assert
  `handler.await_count == 1` after release, and that the loser returned `0`.
  **Fails today**: `a2a_consumer.py:299` reads a marker that `:326` does not write until after
  `:323`, so both tasks pass the check.
- `test_inflight_loser_does_not_ack_or_dlq` — assert `a2a_streams.xack` was not called by the loser,
  `a2a_streams.move_to_dlq` was not called, and no `a2a:retry:*` hash was written.
  **Fails today**: the second caller runs the handler to completion and ACKs at `:327`, so a naive
  C2 that returns early through the failure branch would trade one bug for a DLQ leak. This test
  pins the required shape.
- `test_lease_released_on_handler_failure` — handler raises; assert the inflight key is gone so the
  backoff retry at `:352-364` can re-acquire. **Fails today**: no such key exists.
- `test_lease_released_on_dlq` — attempt `_MAX_RETRIES`; assert the key is gone alongside
  `retry_key`'s deletion at `:341`.

New `backend/tests/unit/test_a2a_streams_claim_refresh.py`:

- `test_xclaim_refresh_issues_justid_for_inflight_ids` — assert `redis.xclaim` is called with the
  group `agent-runtime`, this process's consumer name (`a2a_streams.py:41-50`), the in-flight ids,
  and `justid=True`. **Fails today**: `a2a_streams.py` issues no `XCLAIM` at any line; the function
  does not exist.

Extension to `backend/tests/unit/test_a2a_idempotency.py`: its `_FakeRedis` (`:16-38`) must gain
`set(..., nx=True)` semantics — today `set` at `:24-25` unconditionally assigns and returns `None`,
so it cannot express an NX failure. The two existing tests (`:56-77`, `:80-94`) must keep passing
unchanged; C2 adds a guard *before* `:323` and must not disturb the post-success marker path.

Extension to `backend/tests/unit/test_a2a_turn_dispatch.py`'s pending-notify block (`:1004-1073`).
The `_FakePipe` there already implements `ltrim` with correct negative-index normalisation
(`:1017-1018`, `:1036-1042`) and needs `lpush` added:

- `test_requeue_over_cap_drops_oldest_not_newest` — push 45, drain, push 10, requeue the 45; assert
  the 50 survivors *end with* all 10 new notes and that the 5 dropped are the oldest.
  **Fails today**: `pending_notify.py:84` keeps head indices 0-49, so the assertion sees the queue
  end at `n5` with `n6..n10` missing. Asserting the *tail* contents, not just the length, is what
  makes this test catch the mirrored fix.
- `test_requeue_under_cap_is_lossless` — 10 restored plus 5 concurrent; assert all 15 survive and the
  order is restored-then-concurrent. Passes today; it pins that C1 does not regress the
  below-cap no-op (`_MAX_PENDING` at `:24`).

New `backend/tests/unit/test_a2a_consumer_supervisor.py`:

- `test_reconcile_stops_loop_for_deleted_agent` — seed two agents, make the liveness predicate
  return only one, run `_reconcile` twice; assert the dead agent's task is cancelled and popped from
  `self._loops`. **Fails today**: `:238-250` contains no removal branch at all.
- `test_deleted_agent_loop_is_not_recreated_by_stream_key` — the guard against the mirrored fix. The
  fake SCAN keeps returning `a2a:agent:{deleted_id}` (modelling `mkstream=True` at
  `a2a_streams.py:58` recreating it); assert the loop stays stopped across three reconcile rounds.
  **Fails today, and would still fail under a Redis-only fix** — this is the test that forces the
  predicate to come from the DB.
- `test_restored_agent_loop_is_recreated` — flip the predicate back; assert a new task appears.
  Pins the `admin_restore` path (`agent_service.py:676-694`).
- `test_liveness_error_keeps_all_loops` — predicate raises; assert no task is cancelled.
  Pins the fail-open decision in §7 C4.
- `test_stop_all_still_clears_loops` — `_stop_all` (`:264-272`) behaviour is unchanged by C4.

## 9. Risks and Rollback

| Risk | Mitigation |
|---|---|
| **C2's lease TTL expires under a legitimately long turn**, and a peer re-runs it — the original bug with a larger constant | TTL set well above the refresh interval; and the refresh task (C3) renews both the lease and the PEL idle clock from the same tick, so the two cannot drift apart |
| **The refresh task is starved** (event-loop saturation), losing the lease while the handler still runs | Accepted residual, stated rather than hidden. There is no durable backstop on this path — the sibling dossier's idempotency-key approach cannot apply because `run_input_turn` persists nothing (`turn_engine.py:662-665`, Q-7). C2 shrinks the window from "any turn over 60s" to "any turn whose refresh task was starved for a full TTL" |
| **C3's `XCLAIM` is unavailable or errors** | Wrap it exactly as the existing reclaim is wrapped at `a2a_consumer.py:160-166` — best-effort, logged, never able to skip `consume_once`. Degraded refresh falls back to C2's protection |
| **C2 returns 0 for a lease conflict and the caller counts it as a failure** | It must not touch `retry_key` or the DLQ budget (`_MAX_RETRIES`, `:37`); pinned by `test_inflight_loser_does_not_ack_or_dlq` |
| **C4 stops a live agent's loop** on a transient DB failure, silently dropping its inbox | Fail open on predicate error; pinned by `test_liveness_error_keeps_all_loops` |
| **C4 adds a DB round trip to a 10s scan loop** in every replica | Filter the whole discovered set in one query rather than per agent; the scan already walks every `a2a:agent:*` key (`:254`), so this is not a new order of cost |
| **C1 changes which notes survive**, so a system currently relying on the old truncation sees different context | It is a bugfix toward the documented intent (`:75`, N-dossier APP-1) and only bites above 50 entries |
| Migration | **None.** All three commits are pure code |

**Rollback.** Three independent commits, revertible in any order — they touch disjoint functions
(`pending_notify.requeue`; `_process_entry` + `a2a_streams`; `A2AConsumerSupervisor._reconcile`).
If C2/C3 must be reverted together, revert C3 first: C2 without C3 is safe (more reclaim churn, no
duplicate turns), whereas C3 without C2 is the weaker of the two.

## 10. Acceptance Criteria

- [ ] AC-1: `test_concurrent_process_entry_runs_handler_once` (§8) fails against current code and
      passes after C2.
- [ ] AC-2: a lease conflict returns without ACK, without DLQ, and without consuming a retry
      attempt; the true owner's ACK still settles the entry.
- [ ] AC-3: an entry actively being processed has its PEL idle clock refreshed, so
      `xautoclaim_stale` does not select it regardless of how long the handler runs; a crashed
      owner's entry is still reclaimed after `_CLAIM_MIN_IDLE_MS`.
- [ ] AC-4: `_CLAIM_MIN_IDLE_MS` (`a2a_streams.py:30`) is **not** raised as part of this fix
      (Q-3), and its comment at `:28-30` is true after C3.
- [ ] AC-5: `pending_notify.requeue` over the cap retains the **newest** entries; the surviving tail
      contains every concurrently-pushed note, and the docstring at `:68-76` matches the code.
- [ ] AC-6: below the cap, `requeue` remains lossless and order-preserving.
- [ ] AC-7: a soft-deleted agent's consumer loop is cancelled and removed from `self._loops`, and
      **stays** removed across subsequent scans despite the stream key being recreated.
- [ ] AC-8: a restored agent's loop is recreated by the next reconcile; a liveness-check failure
      cancels nothing.
- [ ] AC-9: the two existing tests in `backend/tests/unit/test_a2a_idempotency.py` (`:56-77`,
      `:80-94`) pass unchanged.
- [ ] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`.

## 11. SRS Delta

None. All three fixes restore documented behaviour: [R9.15] (a `call` gets one turn and one reply),
[R9.16] (`notify` is parked for the next turn), [R9.14] plus
`docs/implement/G-orchestration.md:28` ("one consumer per **live** agent runtime"), and for F-19 the
already-written fix at `docs/implement/N-conversation-a2a-fixes.md:1042-1049`. Two documentation
corrections belong with the code and are not SRS changes: `pending_notify.py:68-76`'s docstring,
which asserts the opposite of what `:84` does, and `a2a_handler.py:3-4`'s "one loop per agent, so an
inline turn only serialises *that* agent's inbox", which is true per process and false across the
three replicas in `docker-compose.prod.yml:142-143` (see FU-2).

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — A deleted agent's `a2a:agent:{id}` stream and its `:dlq` sibling are never removed from
  Redis. C4 deliberately leaves them so `admin_restore` (`agent_service.py:676-694`) keeps working,
  but nothing reclaims them for an agent that will never be restored. A retention sweep would need a
  restore-window policy first; `app/workers/tasks/retention.py` has no Redis reference today.
- **FU-2** — The audit's FU-6 (`findings.md:1215-1217`) stays open: `run_input_turn`
  (`turn_engine.py:652`) takes no turn lock, and this dossier's fix does not change that (Q-5). The
  concrete open question is whether two envelopes for one agent, landing on two of the three
  replicas, may run concurrent headless turns. They may. Decide it as a design question, and correct
  `a2a_handler.py:3-4` either way.
- **FU-3** — Every replica SCANs `a2a:agent:*` every 10s (`a2a_consumer.py:196,252-262`), which is
  O(agents) per replica per scan forever. C4 makes the *task* count correct without making the
  *scan* cheaper; an event-driven or cached discovery is the follow-up.
- **FU-4** — `_PROCESSED_KEY_TTL` and `_RETRY_KEY_TTL` are both 3600 (`:43,:49`) with comments
  asserting they "far exceed any redelivery/reclaim window". After C2/C3 the reclaim window is
  governed by lease renewal rather than by a fixed budget; re-read those two comments and restate the
  relation explicitly, as `app/workers/tasks/graphrag.py:82-88` does for its own pair.
- **FU-5** — There is no metric for reclaim volume or duplicate suppression: `metrics.py` exposes
  `A2A_DLQ` and `A2A_MESSAGES` only, and reclaim is visible solely as a log line
  (`a2a_consumer.py:168-173`). Without a counter, neither the pre-fix duplicate rate nor the
  post-fix lease-conflict rate is observable — which is also why §7 offers no retrospective repair
  for F-5.
</content>
