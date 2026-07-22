---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R13.19, R15.05b, R28.07]
depends_on: []
---

# Presence transition loss and release wake-up parity (a2u F-5, F-21)

## 1. Summary

Two silent-failure defects on the agent wake-up entry path, both confirmed in
`docs/audits/2026-07-22-agent-to-user-conversation/findings.md` (F-5 at `:199-224`, F-21 at
`:551-567`).

**F-5 (major).** The room presence roster is a Redis SET that nothing reconciles against the
per-user connection sets at read time or at leave time. A connection that dies without a clean
close (pod kill, ungraceful termination) leaves a ghost member behind. The ghost inflates the
`SCARD` the last real leaver reads (`backend/contexts/conversation/infrastructure/presence.py:143`),
so `roster_size == 0` is never observed at `backend/app/api/ws/chatroom.py:141` and the
`has_live_users=False` transition is never delivered. The same missing reconciliation makes the
R15.05b defence-in-depth roster re-check (`backend/contexts/orchestration/application/wakeup_service.py:234-237`)
read the ghost as a live user, so it stops blocking. The result is not merely a stale roster: the
room's bound agents keep their silence timers armed
(`backend/contexts/orchestration/infrastructure/wakeup_state.py:133-144`, TTL 7 days at `:23`)
and keep firing `silence_minutes` turns into a room with nobody in it, every 30 seconds' sweep
(`backend/app/workers/main.py:320`), each one a full provider call charged to the user's own BYO
key. Severity is anchored on that: this is unattended spend on the customer's key with no
user-visible symptom, not a cosmetic presence-rail glitch.

**F-21 (minor).** `backend/contexts/agents/application/runtime/turn_engine.py:1725` decides whether a
skipped turn deserves a "why no reply" notice by comparing the trigger to the literal `"mention"`,
while the worker expresses the same classification as the tuple `("mention", "release")`
(`backend/app/workers/tasks/orchestration.py:80` and `:113`). A creator's release wake (R28.07) to an
agent unbound in the race window therefore produces nothing on any channel while the release UI
reports success.

**Do they share a root cause? No.** F-5 is a state-reconciliation defect in the presence layer;
F-21 is a duplicated predicate in the turn engine. They share no file and no mechanism. They are
grouped by change surface, exactly as the audit's hand-off rule states
(`findings.md:664-666`, `:673`): both are silent failures on the agent wake-up entry path, both are
reverted together, and both are proved by the same class of unit test.

**Coordination.** `depends_on: []` is deliberate: neither fix requires code from another dossier and
neither blocks on one. There is, however, a real ordering preference and a hard constraint on
`docs/tasks/2026-07-22-chatroom-socket-lifecycle/` (F-1/F-4/F-18) — see §6.

## 2. Observed vs Expected

**F-5**

- **Observed** — the roster SET `ws:presence:{room_id}` is written by `join`
  (`presence.py:75-100`) and pruned only by `leave` (`:122-147`) or the nightly
  `scrub_stale_presence` (`:154-192`). `list_room` (`:149-151`) is a bare `SMEMBERS` with no
  liveness cross-check, and `_ROSTER_LEAVE_LUA` (`:71`, invoked at `:143`) `SCARD`s the
  ghost-inflated set. Consequently `chatroom.py:141` does not observe `roster_size == 0`, so
  `_notify_presence(has_live_users=False)` (`chatroom.py:32-43`, `:142`) never runs, so
  `evaluate_presence_change` (`backend/contexts/conversation/application/triggers.py:120-138`) →
  `on_presence_changed` (`wakeup_service.py:246-260`) → `set_silence_active(..., False)`
  (`wakeup_state.py:133-144`) never runs. The single remaining guard,
  `wakeup_service.py:234-237`, reads the same unreconciled set and passes.
- **Expected** — R15.05b (`REQUIREMENTS.md:753`) defines a live user as one who "currently has an
  open WebSocket connection to the Chat Room (`ws:presence:{room_id}` contains their user_id)", and
  states "When the live-user set becomes empty, the silence timer pauses." The module's own contract
  (`presence.py:16-21`) says a connection that dies without a clean leave costs "at most one TTL
  window of UI lag" — the conns SET TTL, 150 s (`presence.py:35`). Both are violated: roster
  membership currently outlives the connection by up to the nightly sweep, and the pause edge can be
  lost outright. Intent source is documented; no user confirmation of "expected" is required.

**F-21**

- **Observed** — `turn_engine.py:1724-1727`: `if trigger == "mention"` guards the `not_bound`
  notice; a `"release"` trigger returns `TurnResult(status="skipped", reason="not_bound")` emitting
  nothing. `emit_agent_finished_error` (`backend/contexts/conversation/infrastructure/channels.py:16-31`)
  is the only channel for this signal, and the frontend already maps the reason
  (`frontend/src/slices/conversation/constants/agentErrors.ts:9-10`), so the client side is ready and
  the backend simply never emits.
- **Expected** — R28.07 (`REQUIREMENTS.md:2066`): "an explicit release wake bypasses autostop like a
  mention". The worker already implements that parity and states it in its own comments —
  `orchestration.py:80` (`agent_gone` guard) and `:113` (autostop bypass) both use
  `("mention", "release")`, with `:83-87` and `:106-107` recording that release "is the same shape of
  explicit call and must not fail silently either". The engine's copy of the predicate was not
  updated when `"release"` was introduced (`backend/app/api/v1/observations.py:237-241`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Repair F-5 at the roster source, or add another downstream guard in the silence evaluator? | At the source: reconcile roster membership against the per-user conns keys on read and on leave. Add level-triggered convergence in the evaluator as a second, independent property — not as the fix. | A downstream guard leaves the roster lying about liveness for every other consumer (`chatrooms.py:648` REST snapshot, `wakeup_service.py:109` every_n gate). R15.05b defines the roster itself as the source of truth, so the roster is what must be correct. |
| Q-2 | Where does the shared explicit-trigger set live, so the engine and the worker cannot diverge again? | `EXPLICIT_TRIGGERS: Final = frozenset({"mention", "release"})` in `backend/contexts/orchestration/domain/models.py`, imported by both call sites. | That module already owns the wake-up trigger vocabulary (`WakeupTriggers` at `:129-133`) and already hosts the same anti-divergence pattern — `autostop_limit_for` at `:117-121` exists verbatim so "the worker gate and the domain evaluator can't diverge". It is pure-domain (no framework imports), and `turn_engine.py` already imports other contexts' domain models directly (`:58`, `:86`). |
| Q-3 | Should `depends_on` name the socket-lifecycle dossier? | No. Record the ordering preference and the TTL constraint in §6/§9 instead. | Neither fix needs the other's code. A hard `depends_on` would serialise two independent changes; the real coupling is a constraint the other dossier must honour, which is better stated than encoded. |
| Q-4 | Should reconciliation publish `presence.left` for evicted ghosts so other members' rails heal immediately? | No — out of scope, FU-1. | Reconciled reads already heal the rail on the next `resyncPresence` (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:107,354` → `chatrooms.py:631-649`). Publishing from a read path is a design change, not a defect fix. |
| Q-5 | Data repair for silence flags already armed in production? | None. Converge, do not script. | State is Redis-only and self-healing after the fix (§7). |

## 4. Reproduction

**F-5, deterministic (unit level, no stack).** The existing fake-Redis harness in
`backend/tests/unit/test_presence.py:13-102` reproduces it exactly; `:161-176` already builds the
ghost fixture:

1. `p.join(room, user=stale, conn=c1)`; `p.join(room, user=live, conn=c2)`.
2. Delete `ws:presence:{room}:{stale}:conns` to simulate the conns SET expiring after an unclean
   death (`test_presence.py:171` does this).
3. `p.leave(room, user=live, connection_id=c2)` → returns `(True, 1)`. It must return `(True, 0)`.
4. `p.list_room(room)` → `[stale]`. It must return `[]`.

Step 3 is the missed transition; step 4 is why the R15.05b backstop
(`wakeup_service.py:234-237`) does not catch it.

**F-5, stack level (non-deterministic by nature).** Preconditions: room R with one bound agent whose
`silence_minutes` trigger is enabled and `t_minutes` short; users U and V connected.

1. Kill a backend pod (`SIGKILL`) while U is connected to R. `on_close`
   (`chatroom.py:130-142`) never runs; U remains in the roster.
2. V leaves cleanly. `leave` returns `roster_size == 1` (the ghost), so
   `_notify_presence(has_live_users=False)` does not fire; `silence_active` stays set.
3. Within the next 300 s (`_SET_TTL_SECONDS`, `presence.py:36`) the 30-second silence sweep
   (`orchestration.py:209-279`, cron at `main.py:320`) evaluates the trigger:
   `is_silence_active` is True (`wakeup_service.py:212`), the elapsed window has passed, and the
   roster re-check at `:235` sees the ghost, so the wake fires. A full provider turn runs against an
   empty room.
4. Any user joining R re-`EXPIRE`s the roster key (`presence.py:95`) without clearing the ghost, so
   in a room with normal come-and-go traffic steps 2-3 repeat indefinitely until the 03:30 UTC
   nightly sweep (`retention.py:680-707`, `_POLICIES` at `:751`, cron at `main.py:318`).
5. If instead nothing refreshes the key, it expires at +300 s. The room is then invisible to the
   sweep's `scan_iter` (`presence.py:173-177`), never lands in `emptied_rooms` (`:190-191`), and the
   `has_live_users=False` edge is lost permanently — the flag survives on its own 7-day TTL
   (`wakeup_state.py:23,142`).

Nondeterminism is in the crash timing and the roster-key TTL race only; the unit repro above is
fully deterministic and is what §8 pins.

**F-21, deterministic (unit level).** `backend/tests/unit/test_no_response_notices.py:41-119` already
provides the harness. `_wire_locked(bound=False)` then `_run_locked(trigger="release")` returns
`reason == "not_bound"` with `emitted == []`; the same call with `trigger="mention"` emits
(`:144-149`).

**F-21, stack level.** Creator releases an observation privately to agent X with `wake=true`
(`observations.py:236-243`); X is unbound before the Arq job runs; the job's `role_of`
(`turn_engine.py:1723`) returns `None`; the release UI reports success and the room shows nothing.

## 5. Root Cause Analysis

### F-5

Causal chain, trigger to symptom:

1. `join` writes the per-user conns SET first (`presence.py:92`, TTL 150 s at `:35`) and the room
   roster second (`:95`, TTL 300 s at `:36`). Liveness lives in the conns SET; the roster is a
   derived list that is only ever corrected by an explicit `leave` or the nightly scrub.
2. A connection dying without `on_close` leaves the roster entry orphaned. Only the conns SET
   expires. The audit confirms the ghost is returned to roster reads (`findings.md:203-205`).
3. **Root cause** — `leave` (`presence.py:137-147`) and `list_room` (`:149-151`) treat the roster
   SET as authoritative for liveness with no cross-check against the conns keys. This is the earliest
   link whose correction prevents every downstream symptom: with a reconciling read/leave, step 4,
   5 and 6 below cannot occur regardless of how the connection died.
4. `_ROSTER_LEAVE_LUA` (`:71`, called at `:143`) therefore returns a ghost-inflated cardinality, so
   `chatroom.py:141` never sees `0` and `_notify_presence(has_live_users=False)` (`:142`) never runs.
5. The silence-pause chain downstream of that call — `triggers.py:120-138` →
   `wakeup_service.py:246-260` → `wakeup_state.set_silence_active(..., False)` (`:133-144`) — is
   edge-triggered only. There is no periodic re-derivation, so a lost edge is a permanently wrong
   state bounded solely by the flag's 7-day TTL (`wakeup_state.py:23`).
6. The one guard designed for exactly this staleness — `wakeup_service.py:229-237`, whose comment
   names the unclean-disconnect-versus-scrub window — calls `list_room`, i.e. the same
   unreconciled `SMEMBERS`. Root cause and backstop fail from the same missing check, which is why
   the defect is reachable at all.
7. Symptom: `evaluate_silence_trigger` returns True for an empty room, the sweep enqueues
   `wakeup_agent` (`orchestration.py:258-263`), and a provider call is spent on the user's key.

**Aggravating factors, not root causes.** (a) The only reconciler runs once nightly at 03:30 UTC
(`retention.py:751`, `main.py:318`), so even the recoverable case is up to ~24 h late.
(b) `_SET_TTL_SECONDS = 300` (`presence.py:36`) converts a delayed miss into a permanent one when
the roster key expires before the sweep sees it — the audit's correction at `findings.md:211-215`.
(c) F-1's reconnect churn keeps ghost-holding roster keys alive, which today keeps rooms inside the
reconciler's reach while simultaneously extending the false-wake window (§6).

### F-21

1. The "explicit call" predicate exists in two places: as a tuple in the worker
   (`orchestration.py:80`, `:113`) and as a literal `== "mention"` in the engine
   (`turn_engine.py:1717`, `:1725`).
2. `"release"` was added as a trigger kind at `observations.py:241`; the worker's copy was updated
   (with comments at `orchestration.py:83-87`, `:106-107` recording the intent), the engine's copy
   was not.
3. **Root cause** — the predicate is duplicated rather than named once. The missing `"release"`
   branch is the instance; the duplication is the cause. Adding a second literal at `:1725` would
   leave the next trigger kind to reproduce the identical divergence.

## 6. Blast Radius and Sibling Suspects

### Blast radius — F-5

- **Provider spend on the user's own key**, the primary impact: every `silence_minutes` wake that
  passes the ghost-inflated roster check runs a complete turn (`orchestration.py:139`,
  `turn_engine._run_locked`) into an empty room. In a room with ordinary come-and-go traffic the
  ghost is refreshed by every join (`presence.py:95`) and every heartbeat (`:118`), so the condition
  persists until the 03:30 sweep.
- **Every roster consumer reads the ghost**: the REST presence snapshot
  (`chatrooms.py:631-649`, which the frontend calls on every reconnect via `resyncPresence`,
  `useChatroomSocket.ts:107,354`), and the `every_n_messages` empty-room gate
  (`wakeup_service.py:109-112`) — the latter meaning a gated wake-up is *not* gated and the owner
  notification at `:111` is not sent.
- **Persisted data**: none. All affected state is Redis (roster SETs, `silence_active` flag,
  silence timestamps). The durable residue is the audit trail of `wakeup.fired` rows
  (`orchestration.py:143-160`) for turns that should never have run, and the provider charges those
  turns incurred. Neither is rewritten by this fix (§7).
- **Tenancy**: no cross-tenant exposure. Keys are room-scoped; a ghost affects only its own room.

### Blast radius — F-21

Confined to a transient WS notice. The release itself is already committed and the note already
pushed to `pending_notify` (`observations.py:224-235`) before the wake is enqueued, so no data is
lost by the missing emit; the creator simply gets no signal that the wake did nothing.

### Coordination with `docs/tasks/2026-07-22-chatroom-socket-lifecycle/` (F-1/F-4/F-18)

**Does fixing F-1 make F-5 more or less likely to bite? More — and in its worst form.**

Today, per `findings.md:108-114`, every socket is reaped at ~121 s and reconnects, so every socket
runs `on_close` → `on_open` roughly every two minutes. Two consequences follow from
`presence.py:95` and `:118`, both of which `EXPIRE` the roster key:

- Ghost-holding roster keys are continuously refreshed, so the room stays visible to the nightly
  reconciler's `scan_iter` (`presence.py:173-177`) and its `emptied_rooms` hook
  (`retention.py:698-705`) eventually delivers the missed transition — late, but delivered.
- The churn does **not** clear the ghost (nothing SREMs the roster except `leave` and the scrub),
  so the false-wake window is extended for as long as the churn continues.

Once F-1 is fixed and sockets are long-lived, nothing refreshes the roster key after the last real
user departs. The key expires at +300 s (`presence.py:36`), the room drops out of `scan_iter`
entirely, it never enters `emptied_rooms` (`presence.py:190-191`), and the `has_live_users=False`
edge is lost permanently rather than delivered late. The false-wake window shortens; the wrong state
becomes durable and unrepaired for up to the flag's 7-day TTL. That is precisely the branch the
audit's own correction identifies as "worse, not better" (`findings.md:211-215`).

**Ordering constraint.** Preference, not a blocker: land this dossier **before or together with** the
socket dossier. Fixing F-1 first removes the accidental repair path described above while F-5 is
still open. There is no code overlap — this fix touches `presence.py`, `wakeup_service.py` and
`turn_engine.py`; the socket dossier touches `connection.py`, `ws-manager.ts` and (for F-18)
`chatroom.py:130-142`, whose `on_close` this fix leaves unchanged.

**Hard constraint the socket dossier must honour.** After this fix, `_CONN_TTL_SECONDS = 150`
(`presence.py:31-35`) becomes a liveness authority, not just a scrub input: a live connection whose
conns key lapses would be evicted from the roster as a ghost. That TTL is documented as safe only
because the 120 s idle reaper forces at least one inbound frame per window. Whatever keepalive the
socket dossier introduces must keep its interval strictly below `_CONN_TTL_SECONDS`, or that
constant must be raised in the same change. Recorded here and in §9. Note the socket dossier's Q-3
proposes a 30 s heartbeat, which satisfies this comfortably — but the constraint must be recorded so
a later change to that interval cannot silently break the roster.

**Does F-5 belong to the §3-head coupling?** Yes, but the audit did not enumerate it there.
`findings.md:83-85` names F-8, F-11 and F-13 as coupled to F-1, with F-11 becoming *worse* once F-1
is fixed, and `findings.md:687-691` generalises the pattern. F-5 belongs to the F-11 direction of that
coupling — masked in degree by the churn today, worse after the fix — and is not one of the four
listed. This dossier records the coupling on the audit's behalf.

### Sibling suspects

**F-5 siblings (roster-read consumers).**

- `wakeup_service.py:109-112` (`every_n_messages` empty-room gate) — **confirmed affected by the same
  root cause**. Same `list_room` call, same ghost. Fixed by the same change; no separate work.
- `wakeup_service.py:235-237` (silence backstop) — **confirmed affected**, as analysed in §5 step 6.
- `chatrooms.py:648` (`GET /{id}/presence`) — **confirmed affected**; returns ghost user_ids to the
  presence rail. Fixed by the same change.
- `scrub_stale_presence` (`presence.py:154-192`) — **cleared.** It already performs exactly the
  cross-check this fix generalises (`:181-187`), and its `emptied_rooms` contract is correct,
  including the mixed live/stale case pinned at `test_presence.py:161-176`. Its only defect is
  cadence and reachability, recorded as FU-2.
- `ws:user:{user}:rooms` reverse index (`presence.py:43-44`, written at `:97-98`) — **cleared as a
  defect source**; it has no liveness-sensitive reader. It is written back by the scrub (`:187`) and
  must be kept consistent by the new reconciliation, which the fix does (§7).
- `presence.heartbeat` (`presence.py:102-120`) — **cleared.** Re-asserting membership on every frame
  is correct for a live connection; it cannot resurrect a dead one because a dead connection sends no
  frames.

**F-21 siblings — systemic sweep.** The systemic form is "a trigger kind tested against a literal
rather than the shared tuple". Repo-wide sweep over `backend/` (excluding `.venv`) for
`trigger ==`, `trigger !=`, `trigger in (`, `trigger not in` returns exactly four sites:

| Site | Form | Verdict |
|---|---|---|
| `app/workers/tasks/orchestration.py:80` | `trigger in ("mention", "release")` | **Cleared** — correct set, and the reference the finding cites as the intended parity. |
| `app/workers/tasks/orchestration.py:113` | `trigger not in ("mention", "release")` | **Cleared** — same set, autostop bypass. |
| `contexts/agents/application/runtime/turn_engine.py:1725` | `trigger == "mention"` (`not_bound`) | **Confirmed defective** — F-21 as filed. |
| `contexts/agents/application/runtime/turn_engine.py:1717` | `trigger == "mention"` (`agent_gone`) | **Confirmed defective — sibling not named in the finding.** Reachable: the worker's `agent_gone` guard reads the agent at `orchestration.py:78`, the engine re-reads it at `turn_engine.py:1713`, and a soft-delete landing between the two reaches the engine's branch. Same literal, same missing `"release"`. Must be fixed in the same change. |

Trigger-*producing* literals were swept as well and are **cleared** — they emit a value rather than
classify one: `app/api/v1/messages.py:350-356` (`"mention"`), `app/api/v1/observations.py:241`
(`"release"`), `app/api/v1/observations.py:212` and `app/workers/tasks/orchestration.py:30`
(`"every_n_messages"`), `app/workers/tasks/orchestration.py:262` (`"silence_minutes"`). Their
stringly-typed nature is a fragility, recorded as FU-4.

`frontend/src` has no equivalent predicate: the only trigger-reason consumer is the reason→i18n map
at `frontend/src/slices/conversation/constants/agentErrors.ts:9-10`, which already handles both
`agent_gone` and `not_bound`. **No frontend change is required for F-21.**

## 7. Fix Design

### Part A — F-5: reconcile the roster against liveness, and converge the flag

**A1. Liveness-reconciling roster read and leave (`presence.py`).** Add one private helper that,
given a room key, reads the roster members and drops any whose `ws:presence:{room}:{user}:conns` key
does not exist — `SREM`ing them from both the roster and `ws:user:{user}:rooms`, mirroring exactly
what `scrub_stale_presence:181-187` already does — and returns the live set. Use it in:

- `list_room` (`presence.py:149-151`): return the reconciled set instead of raw `SMEMBERS`.
- `leave` (`presence.py:137-147`): after the departing member's own `SREM`, derive `roster_size`
  from the reconciled set instead of the Lua `SCARD` at `:143`. The multi-tab early return at
  `:140-141` stays untouched, so the reconcile runs only on a genuine last-connection leave.

**Why this corrects rather than masks.** The symptom is an agent waking into an empty room; the
proximate cause is a downstream guard being fed a false liveness answer; the false answer originates
in a set that R15.05b (`REQUIREMENTS.md:753`) *defines* as "users who currently have an open
WebSocket connection". Making the set mean what the requirement says it means removes the defect for
every consumer at once (§6 siblings), rather than adding a third guard downstream of a set that
still lies. It also restores the module's own stated contract of "at most one TTL window of UI lag"
(`presence.py:16-21`), which is currently false by up to 24 hours.

**Race safety.** Reconciliation cannot evict a genuinely joining user: `join` writes the conns key
(`presence.py:92`) strictly before the roster entry (`:95`), so any member visible in the roster
already has a live conns key by construction. The only user an `EXISTS` check can find missing is one
whose conns SET has actually expired or been deleted.

**Cost.** One `EXISTS` per roster member per reconciled read, pipelined. Room rosters are small, the
`leave` path reconciles only on last-connection close, and `list_room`'s callers are a 30-second cron
(`wakeup_service.py:235`), the per-message every_n gate (`:109`), and an explicit REST snapshot
(`chatrooms.py:648`).

**A2. Level-triggered convergence (`wakeup_service.py:229-237`).** When the (now trustworthy)
roster re-check finds no live members for a non-observer binding, pause the flag —
`wakeup_state.set_silence_active(agent_id, room_id, False)` — before returning `False`. The edge
remains the fast path; the 30-second sweep becomes the convergence path, so no single lost edge can
leave a timer armed for 7 days. The write happens at most once per lost edge, not once per sweep:
the code only reaches `:234` after `is_silence_active` returned True at `:212`, and the pause makes
the next evaluation return at `:212-213`. Observer bindings are untouched — they are exempt from the
presence gate by design (`wakeup_service.py:196-199`, O-2/R28.04).

A1 alone fixes the reachable defect; A2 is what makes the fix robust to the F-1 interaction in §6,
where the nightly reconciler stops being able to see the affected rooms at all.

**Deliberately out of scope**: changing the scrub cadence or removing `_SET_TTL_SECONDS`
(`presence.py:36`). With A1 and A2 in place the nightly sweep is no longer load-bearing for this
defect; tuning it is FU-2.

### Part B — F-21: name the predicate once

**B1.** Add `EXPLICIT_TRIGGERS: Final = frozenset({"mention", "release"})` to
`backend/contexts/orchestration/domain/models.py`, next to the wake-up trigger vocabulary and beside
`autostop_limit_for` (`:117-121`), which exists for the identical anti-divergence reason.

**B2.** Replace the literals at `turn_engine.py:1717` and `:1725` with membership tests against the
imported constant, and replace both worker tuples (`orchestration.py:80`, `:113`) with the same
constant so no copy of the set remains. Update the comments at `orchestration.py:83-87` and
`:106-107` to point at the constant rather than at each other.

**Why this corrects rather than masks.** Adding `or trigger == "release"` at `:1725` would fix the
reported instance and leave the duplication — the actual cause — intact, guaranteeing the same bug
when the next explicit trigger kind is added. A single named set makes the parity structural and
makes the §8 table-driven test a permanent guard rather than a one-off assertion.

### Data repair

**None required, and none attempted, for either defect.**

- F-5 state is entirely Redis and volatile. Ghost roster entries self-heal on the first reconciled
  read or leave after deploy (and, worst case, expire at `_SET_TTL_SECONDS`). Silence flags already
  armed converge at the next 30-second sweep via A2, or expire on their own 7-day TTL
  (`wakeup_state.py:23`). No migration, no backfill script, no Redis surgery.
- Historic `wakeup.fired` audit rows (`orchestration.py:143-160`) for turns that fired into empty
  rooms are **left as written**. They are an accurate record of what the system did; rewriting audit
  history to match what it should have done is not acceptable. Provider spend already incurred is not
  recoverable and this dossier does not claim otherwise.
- F-21 has no persisted state. A note already pushed to `pending_notify`
  (`observations.py:224-235`) for an agent unbound before its wake is unaffected by this change; its
  fate is FU-3.

## 8. Regression Test Plan

Failing tests first. Every test below fails against current `main` for the stated reason; /build
writes them before touching any production file.

**T-1 — `backend/tests/unit/test_presence.py::test_last_leave_reports_empty_roster_despite_a_ghost_member`**
Reuse the fixture shape at `test_presence.py:161-176`: join `stale` and `live`, delete
`ws:presence:{room}:{stale}:conns`, then `leave(room, live, c2)`.
Asserts `(left, roster_size) == (True, 0)`.
*Fails today*: `_ROSTER_LEAVE_LUA` (`presence.py:71`, called at `:143`) `SCARD`s the ghost-inflated
set and returns `(True, 1)`, which is exactly why `chatroom.py:141` never fires the transition.
The existing `_FakeRedis` already implements `exists` (`test_presence.py:47-48`), so no harness
change is needed.

**T-2 — `backend/tests/unit/test_presence.py::test_list_room_omits_and_evicts_a_member_with_no_live_connection`**
Same fixture. Asserts `set(await p.list_room(room)) == {live}`, and that `stale` is gone from both
`ws:presence:{room}` and `ws:user:{stale}:rooms` in the fake store.
*Fails today*: `list_room` (`presence.py:149-151`) is a bare `SMEMBERS` and returns both users; the
reverse index is never touched on read.

**T-3 — `backend/tests/unit/test_presence.py::test_multi_tab_leave_does_not_reconcile`**
Second tab of one user closes while the first is live: asserts the early return
`(False, -1)` (`presence.py:140-141`) is preserved and no `EXISTS` reconciliation ran.
*Fails today*: no — this one **passes today** and is included as a guard that A1 does not move the
reconcile onto the hot multi-tab path or break the `-1` sentinel `chatroom.py:136-142` relies on.
Named explicitly so it is not mistaken for a new failing test.

**T-4 — `backend/tests/unit/test_wakeup_service.py::test_empty_roster_pauses_the_silence_flag`**
Extend the existing harness (`test_wakeup_service.py:37-59`) with a recorder for
`wakeup_state.set_silence_active`. With `room_members=[]` and
`_stub_stale_but_ready_silence_state`, assert `evaluate_silence_trigger` returns `False` **and**
recorded exactly one `(agent_id, room_id, False)` call.
*Fails today*: `wakeup_service.py:234-237` returns `False` without writing the flag, so the recorder
is empty and the timer stays armed for the flag's full 7-day TTL.

**T-5 — `backend/tests/unit/test_wakeup_service.py::test_observer_roster_check_and_pause_are_skipped`**
`is_observer=True` with `room_members=[]`: asserts the trigger still fires and `set_silence_active`
was never called.
*Fails today*: no — a guard that A2 does not regress the O-2/R28.04 observer exemption
(`wakeup_service.py:196-199`, `:234`).

**T-6 — `backend/tests/unit/test_no_response_notices.py::test_not_bound_emits_on_release`**
`_wire_locked(agent=_locked_agent(), bound=False)`, `_run_locked(trigger="release")`.
Asserts `result.reason == "not_bound"` and `emitted[0][1]["error"] == "not_bound"`.
*Fails today*: `turn_engine.py:1725` tests `trigger == "mention"`, so `emitted == []` and the
subscript raises `IndexError`.

**T-7 — `backend/tests/unit/test_no_response_notices.py::test_agent_gone_emits_on_release`**
Same harness with `agent=None`, `trigger="release"`; asserts the `agent_gone` notice is emitted.
*Fails today*: `turn_engine.py:1717`, the sibling identified in §6 and not named in the finding.

**T-8 — `backend/tests/unit/test_no_response_notices.py::test_explicit_triggers_all_emit_and_autonomous_stay_silent`**
Table-driven over the shared `EXPLICIT_TRIGGERS` constant: for every member, assert `not_bound` and
`agent_gone` both emit; for `"every_n_messages"` and `"silence_minutes"`, assert `emitted == []`.
*Fails today*: `"release"` is a member of the intended set (`orchestration.py:80`) and the engine is
silent for it. This is the systemic guard — it fails automatically if a future trigger kind is added
to the set and the engine is not updated.

**T-9 — regression guards that must stay green, unmodified**:
`test_no_response_notices.py:136-141` and `:152-157` (autonomous triggers stay silent — proves the
predicate widened to exactly the explicit set, not to all triggers);
`test_agent_trigger_wiring.py:348-364` and `:466-481` (worker-side release parity — proves B2's
constant substitution changed no worker behaviour);
`test_presence.py:105-127`, `:130-144`, `:147-157`, `:161-176` (existing presence semantics);
`test_retention_deep.py:667-740` (the scrub's `emptied_rooms` hook is unchanged).

## 9. Risks and Rollback

- **Over-eager ghost eviction.** After A1, a live connection whose conns key lapses is evicted from
  the roster. Today `_CONN_TTL_SECONDS = 150` is safe only because the 120 s idle reaper forces an
  inbound frame per window (`presence.py:31-35`). **The socket-lifecycle dossier must keep its
  keepalive interval strictly below 150 s or raise this constant in the same change** (§6). If a
  keepalive is introduced with a longer interval, live users would silently vanish from rosters —
  the inverse of the current defect and more visible.
- **Extra Redis round-trips.** One `EXISTS` per roster member on reconciled reads. Bounded by room
  size, pipelined, and on the `leave` path gated behind the last-connection check
  (`presence.py:140-141`). The `/presence` endpoint (`chatrooms.py:648`) is the highest-frequency
  caller and is already one round-trip per reconnect.
- **Extra Redis write on the silence path.** A2 writes `set_silence_active(..., False)` at most once
  per lost edge (§7), not once per 30-second sweep.
- **Behaviour change for consumers that relied on ghosts.** None found: §6 enumerates all three
  `list_room` consumers and in every case the ghost is the defect, not a dependency.
- **B2 blast radius.** Substituting the shared constant into `orchestration.py:80` and `:113`
  changes no worker behaviour — the constant has the same members as the tuples it replaces — and
  T-9's worker tests pin that.
- **Rollback.** Both parts are additive, self-contained, and touch no schema, no migration and no
  persisted data. Reverting the commit restores prior behaviour immediately; any Redis state written
  in the interim (a paused `silence_active` flag, an evicted ghost) is volatile and correct under the
  old code as well. No rollback script is required.

## 10. Acceptance Criteria

- [ ] **AC-1**: The regression tests from §8 (T-1, T-2, T-4, T-6, T-7, T-8) fail before the fix and
  pass after; T-3, T-5 and every test named in T-9 pass both before and after.
- [ ] **AC-2**: `PresenceTracker.leave` returns `roster_size == 0` when the only remaining roster
  members are entries with no live conns key, so `chatroom.py:141` fires
  `_notify_presence(has_live_users=False)` for the last real leaver.
- [ ] **AC-3**: `PresenceTracker.list_room` returns only members with a live conns key, and evicts
  the others from both `ws:presence:{room}` and `ws:user:{user}:rooms` as it goes.
- [ ] **AC-4**: The multi-tab early return `(False, -1)` (`presence.py:140-141`) is preserved, and
  no reconciliation runs on a non-last-connection leave.
- [ ] **AC-5**: `evaluate_silence_trigger` pauses `silence_active` when the reconciled roster is
  empty for a non-observer binding, so an armed timer converges without the nightly scrub.
- [ ] **AC-6**: Observer bindings still bypass both the roster re-check and the new pause
  (O-2/R28.04).
- [ ] **AC-7**: A single `EXPLICIT_TRIGGERS` constant exists in
  `contexts/orchestration/domain/models.py`, and a repo-wide sweep for `trigger ==` /
  `trigger in (` / `trigger not in` over `backend/` (excluding `.venv`) returns **no** site testing a
  trigger kind against a literal — all four current sites read the constant.
- [ ] **AC-8**: A release wake to an unbound agent emits `agent.finished{error: "not_bound"}` on the
  room channel; to a deleted agent reaching the engine's own guard, `agent_gone`.
- [ ] **AC-9**: Autonomous triggers (`every_n_messages`, `silence_minutes`) remain silent for both
  `not_bound` and `agent_gone`.
- [ ] **AC-10**: No data-repair script, migration, or audit-row rewrite is introduced (§7).
- [ ] **AC-11**: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`. No frontend change is expected; if none is made, `frontend/` gates are not required.
- [ ] **AC-12**: The `_CONN_TTL_SECONDS` constraint from §6/§9 is recorded as a comment at
  `presence.py:31-35` naming the keepalive interval it depends on, so the socket-lifecycle dossier
  cannot break it silently.

## 11. SRS Delta

None. Both fixes restore documented behaviour: R15.05b (`REQUIREMENTS.md:753`) already defines roster
membership as "currently has an open WebSocket connection" and mandates the pause on an empty live-user
set; R28.07 (`REQUIREMENTS.md:2066`) already states that a release wake behaves like a mention.
`presence.py:16-21`'s "at most one TTL window of UI lag" contract is likewise restored rather than
amended.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — Reconciliation and `scrub_stale_presence` evict roster members without publishing
  `presence.left` (`presence.py:186`, contrast `chatroom.py:137-140`), so other members' rails heal
  only on the next `resyncPresence` (`useChatroomSocket.ts:107,354`). Decide whether an eviction
  should announce itself, and from which layer.
- **FU-2** — The presence reconciler runs once nightly (`retention.py:751`, `main.py:318`) and a room
  whose roster key has expired (`presence.py:36`) is invisible to it entirely
  (`presence.py:173-177,190-191`). Now that reads self-heal, revisit: either a minutes-cadence
  reconciler or dropping the roster key's TTL so rooms cannot vanish from it.
- **FU-3** — A note pushed to `pending_notify` (`observations.py:224-235`) for an agent that is
  unbound before its wake runs is never retracted; it drains into that agent's next turn, possibly in
  another room. Decide retract-or-scope semantics for released observations. Called out as
  "lingers as misrouted until its TTL" in `findings.md:566`.
- **FU-4** — Trigger kinds remain stringly typed at every producing site
  (`messages.py:354`, `observations.py:212,241`, `orchestration.py:30,262`). A `StrEnum` in the same
  domain module as `EXPLICIT_TRIGGERS` would make the vocabulary checkable; deferred so this bugfix
  does not become a refactor.
- **FU-5** — `wakeup_service.py:109-112`'s every_n empty-room gate silently benefits from this fix
  but has no test of its own for the ghost case; a characterization test there would be cheap
  hardening.
</content>
