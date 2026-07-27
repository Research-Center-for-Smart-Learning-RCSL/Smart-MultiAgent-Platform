---
type: bugfix
status: implemented
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
`SCARD` the last real leaver reads (`backend/contexts/conversation/infrastructure/presence.py:156`),
so `roster_size == 0` is never observed at `backend/app/api/ws/chatroom.py:198` and the
`has_live_users=False` transition is never delivered. The same missing reconciliation makes the
R15.05b roster gate (`backend/contexts/orchestration/application/wakeup_service.py:240-245`)
read the ghost as a live user, so it stops blocking. The result is not merely a stale roster: the
room's bound agents keep their silence timers armed
(`backend/contexts/orchestration/infrastructure/wakeup_state.py:103-120`, TTL 7 days at `:22`)
and keep firing `silence_minutes` turns into a room with nobody in it, every 30 seconds' sweep
(`backend/app/workers/main.py:336`), each one a full provider call charged to the user's own BYO
key. (Line reference corrected 2026-07-27.) Severity is anchored on that: this is unattended spend on the customer's key with no
user-visible symptom, not a cosmetic presence-rail glitch.

**F-21 (minor).** `backend/contexts/agents/application/runtime/turn_engine.py:1880` decides whether a
skipped turn deserves a "why no reply" notice by comparing the trigger to the literal `"mention"`,
while the worker expresses the same classification as the tuple `("mention", "release")`
(`backend/app/workers/tasks/orchestration.py:81` and `:109`). A creator's release wake (R28.07) to an
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
  (`presence.py:88-113`) and pruned only by `leave` (`:135-160`) or the nightly
  `scrub_stale_presence` (`:235-273`). `list_room` (`:230-232`) is a bare `SMEMBERS` with no
  liveness cross-check, and `_ROSTER_LEAVE_LUA` (`:84`, invoked at `:156`) `SCARD`s the
  ghost-inflated set. Consequently `chatroom.py:198` does not observe `roster_size == 0`, so
  `_notify_presence(has_live_users=False)` (`chatroom.py:32-43`, `:199`) never runs.
  (Line references corrected 2026-07-27.) **The single
  remaining guard, `wakeup_service.py:240-245`, reads the same unreconciled set and passes.**
  (Re-baselined 2026-07-27 per Q-6: this chain originally continued through
  `evaluate_presence_change` → `on_presence_changed` → `set_silence_active(..., False)`. That final
  link was deleted by `2026-07-22-wakeup-trigger-state-and-bounds` C1, which also made the lost edge
  harmless in itself — the roster gate is now re-evaluated every sweep. What survives, and is the
  defect, is that the roster gate reads an unreconciled set.)
- **Expected** — R15.05b (`REQUIREMENTS.md:753`) defines a live user as one who "currently has an
  open WebSocket connection to the Chat Room (`ws:presence:{room_id}` contains their user_id)", and
  states "When the live-user set becomes empty, the silence timer pauses." The module's own contract
  (`presence.py:16-21`) says a connection that dies without a clean leave costs "at most one TTL
  window of UI lag" — the conns SET TTL, 150 s (`presence.py:35`). Both are violated: roster
  membership currently outlives the connection by up to the nightly sweep, and the pause edge can be
  lost outright. Intent source is documented; no user confirmation of "expected" is required.

**F-21**

- **Observed** — `turn_engine.py:1879-1882`: `if trigger == "mention"` guards the `not_bound`
  notice; a `"release"` trigger returns `TurnResult(status="skipped", reason="not_bound")` emitting
  nothing. `emit_agent_finished_error` (`backend/contexts/conversation/infrastructure/channels.py:16-31`)
  is the only channel for this signal, and the frontend already maps the reason
  (`frontend/src/slices/conversation/constants/agentErrors.ts:9-10`), so the client side is ready and
  the backend simply never emits.
- **Expected** — R28.07 (`REQUIREMENTS.md:2066`): "an explicit release wake bypasses autostop like a
  mention". The worker already implements that parity and states it in its own comments —
  `orchestration.py:81` (`agent_gone` guard) and `:109` (autostop bypass) both use
  `("mention", "release")`, with `:82-88` and `:104-108` recording that release "is the same shape of
  explicit call and must not fail silently either". The engine's copy of the predicate was not
  updated when `"release"` was introduced (`backend/app/api/v1/observations.py:237-241`). (Line
  references corrected 2026-07-27.)

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Repair F-5 at the roster source, or add another downstream guard in the silence evaluator? | At the source: reconcile roster membership against the per-user conns keys on read and on leave. Add level-triggered convergence in the evaluator as a second, independent property — not as the fix. | A downstream guard leaves the roster lying about liveness for every other consumer (`chatrooms.py:648` REST snapshot, `wakeup_service.py:109` every_n gate). R15.05b defines the roster itself as the source of truth, so the roster is what must be correct. |
| Q-2 | Where does the shared explicit-trigger set live, so the engine and the worker cannot diverge again? | `EXPLICIT_TRIGGERS: Final = frozenset({"mention", "release"})` in `backend/contexts/orchestration/domain/models.py`, imported by both call sites. | That module already owns the wake-up trigger vocabulary (`WakeupTriggers` at `:185-188`) and already hosts the same anti-divergence pattern — `autostop_limit_for` at `:172-176` exists verbatim so "the worker gate and the domain evaluator can't diverge". It is pure-domain (no framework imports), and `turn_engine.py` already imports other contexts' domain models directly (`:58`, `:86`). (Line references corrected 2026-07-27.) |
| Q-3 | Should `depends_on` name the socket-lifecycle dossier? | No. Record the ordering preference and the TTL constraint in §6/§9 instead. | Neither fix needs the other's code. A hard `depends_on` would serialise two independent changes; the real coupling is a constraint the other dossier must honour, which is better stated than encoded. |
| Q-4 | Should reconciliation publish `presence.left` for evicted ghosts so other members' rails heal immediately? | No — out of scope, FU-1. | Reconciled reads already heal the rail on the next `resyncPresence` (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:107,354` → `chatrooms.py:631-649`). Publishing from a read path is a design change, not a defect fix. |
| Q-5 | Data repair for silence flags already armed in production? | None. Converge, do not script. | State is Redis-only and self-healing after the fix (§7). Superseded in part by Q-6: there is no longer a flag to converge. |
| Q-6 | This dossier was written against a baseline that `2026-07-22-wakeup-trigger-state-and-bounds` has since changed: its C1 deleted `set_silence_active` / `is_silence_active` and made the live roster the only silence gate. What survives? | **Part A1 survives unchanged and is now the whole of Part A. Part A2 is withdrawn.** The re-baseline was applied on 2026-07-27; every reference to the presence flag below has been corrected, and the sections A2 justified (§8 T-4, §9's extra-write risk, §10 AC-5) are marked withdrawn rather than deleted so the reasoning stays auditable. | A2 existed to converge a *cached* copy of room presence that could go stale. That cache no longer exists — the evaluator reads `PresenceTracker.list_room` unconditionally on every sweep (`wakeup_service.py:240-245`), which is level-triggered by construction. A2 is therefore not merely unnecessary but unimplementable as written. **A1 is untouched by that change and is now more load-bearing, not less**: with the flag gone, the reconciled roster is the *single* thing standing between a ghost member and a wake-up into an empty room. See the rewritten §6 coordination note. |

## 4. Reproduction

**F-5, deterministic (unit level, no stack).** The existing fake-Redis harness in
`backend/tests/unit/test_presence.py:13-102` reproduces it exactly; `:161-176` already builds the
ghost fixture:

1. `p.join(room, user=stale, conn=c1)`; `p.join(room, user=live, conn=c2)`.
2. Delete `ws:presence:{room}:{stale}:conns` to simulate the conns SET expiring after an unclean
   death (`test_presence.py:171` does this).
3. `p.leave(room, user=live, connection_id=c2)` → returns `(True, 1)`. It must return `(True, 0)`.
4. `p.list_room(room)` → `[stale]`. It must return `[]`.

Step 3 is the missed transition; step 4 is why the R15.05b roster gate
(`wakeup_service.py:240-245`) does not catch it. Post-C1 (Q-6) step 4 is the whole defect: the gate
is re-evaluated on every sweep, so it would self-correct the instant `list_room` told the truth.

**F-5, stack level (non-deterministic by nature).** Preconditions: room R with one bound agent whose
`silence_minutes` trigger is enabled and `t_minutes` short; users U and V connected.

1. Kill a backend pod (`SIGKILL`) while U is connected to R. `on_close`
   (`chatroom.py:179-199`) never runs; U remains in the roster. (Line reference corrected 2026-07-27.)
2. V leaves cleanly. `leave` returns `roster_size == 1` (the ghost), so
   `_notify_presence(has_live_users=False)` does not fire. (Re-baselined 2026-07-27: this step
   originally also noted "`silence_active` stays set", which no longer applies — Q-6.)
3. Within the next 300 s (`_SET_TTL_SECONDS`, `presence.py:36`) the 30-second silence sweep
   (`orchestration.py:226-296`, cron at `main.py:336`) evaluates the trigger: the silence timestamp
   is present and the elapsed window has passed (`wakeup_service.py:225-232`), autostop has not
   tripped (`:234-238`), and the roster read at `:243` sees the ghost, so the wake fires. A full
   provider turn runs against an empty room. (Line references corrected 2026-07-27.)
4. Any user joining R re-`EXPIRE`s the roster key (`presence.py:108`) without clearing the ghost, so
   in a room with normal come-and-go traffic steps 2-3 repeat indefinitely until the 03:30 UTC
   nightly sweep (`retention.py:707-734`, `_POLICIES` at `:746`, cron at `main.py:334`). (Line
   references corrected 2026-07-27.)
5. If instead nothing refreshes the key, it expires at +300 s. The room is then invisible to the
   sweep's `scan_iter` (`presence.py:254-259`), never lands in `emptied_rooms` (`:271-272`), and the
   `has_live_users=False` edge is lost permanently — the flag survives on its own 7-day TTL
   (`wakeup_state.py:23,142`). (Line references corrected 2026-07-27.)

Nondeterminism is in the crash timing and the roster-key TTL race only; the unit repro above is
fully deterministic and is what §8 pins.

**F-21, deterministic (unit level).** `backend/tests/unit/test_no_response_notices.py:46-128` already
provides the harness. `_wire_locked(bound=False)` then `_run_locked(trigger="release")` returns
`reason == "not_bound"` with `emitted == []`; the same call with `trigger="mention"` emits
(`:144-149`).

**F-21, stack level.** Creator releases an observation privately to agent X with `wake=true`
(`observations.py:236-243`); X is unbound before the Arq job runs; the job's `role_of`
(`turn_engine.py:1878`) returns `None`; the release UI reports success and the room shows nothing.
(Line reference corrected 2026-07-27.)

## 5. Root Cause Analysis

### F-5

Causal chain, trigger to symptom:

1. `join` writes the per-user conns SET first (`presence.py:105`, TTL 150 s at `:35`) and the room
   roster second (`:108`, TTL 300 s at `:36`). Liveness lives in the conns SET; the roster is a
   derived list that is only ever corrected by an explicit `leave` or the nightly scrub.
2. A connection dying without `on_close` leaves the roster entry orphaned. Only the conns SET
   expires. The audit confirms the ghost is returned to roster reads (`findings.md:203-205`).
3. **Root cause** — `leave` (`presence.py:135-160`) and `list_room` (`:230-232`) treat the roster
   SET as authoritative for liveness with no cross-check against the conns keys. This is the earliest
   link whose correction prevents every downstream symptom: with a reconciling read/leave, step 4,
   5 and 6 below cannot occur regardless of how the connection died.
4. `_ROSTER_LEAVE_LUA` (`:84`, called at `:156`) therefore returns a ghost-inflated cardinality, so
   `chatroom.py:198` never sees `0` and `_notify_presence(has_live_users=False)` (`:199`) never runs.
   (Line references corrected 2026-07-27.)
5. **Superseded 2026-07-27 (Q-6).** This link originally read: "the silence-pause chain downstream of
   that call — `triggers.py:120-138` → `wakeup_service.py:246-260` → `set_silence_active(..., False)`
   — is edge-triggered only, so a lost edge is a permanently wrong state bounded solely by the flag's
   7-day TTL." `2026-07-22-wakeup-trigger-state-and-bounds` C1 deleted that chain's final link and
   made the gate level-triggered, so a lost edge is no longer a wrong state at all. The link is kept
   in place rather than renumbered (the contract's never-renumber rule) and contributes nothing to
   the surviving causal chain.
6. The gate that would otherwise catch the ghost — `wakeup_service.py:240-245`, the unconditional
   roster read C1 made authoritative — calls `list_room`, i.e. the same unreconciled `SMEMBERS`.
   Root cause and gate fail from the same missing check, which is why the defect is reachable at all.
   With link 5 gone this is the *only* remaining path to the symptom, which sharpens rather than
   weakens the case for A1.
7. Symptom: `evaluate_silence_trigger` returns True for an empty room, the sweep enqueues
   `wakeup_agent` (`orchestration.py:275-280`), and a provider call is spent on the user's key.

**Aggravating factors, not root causes.** (a) The only reconciler runs once nightly at 03:30 UTC
(`retention.py:746`, `main.py:334`), so even the recoverable case is up to ~24 h late. (Line
references corrected 2026-07-27.)
(b) `_SET_TTL_SECONDS = 300` (`presence.py:36`) converts a delayed miss into a permanent one when
the roster key expires before the sweep sees it — the audit's correction at `findings.md:211-215`.
(c) F-1's reconnect churn keeps ghost-holding roster keys alive, which today keeps rooms inside the
reconciler's reach while simultaneously extending the false-wake window (§6).

### F-21

1. The "explicit call" predicate exists in two places: as a tuple in the worker
   (`orchestration.py:81`, `:109`) and as a literal `== "mention"` in the engine
   (`turn_engine.py:1872`, `:1880`).
2. `"release"` was added as a trigger kind at `observations.py:241`; the worker's copy was updated
   (with comments at `orchestration.py:82-88`, `:104-108` recording the intent), the engine's copy
   was not. (Line references corrected 2026-07-27.)
3. **Root cause** — the predicate is duplicated rather than named once. The missing `"release"`
   branch is the instance; the duplication is the cause. Adding a second literal at `:1880` would
   leave the next trigger kind to reproduce the identical divergence. (Line reference corrected
   2026-07-27.)

## 6. Blast Radius and Sibling Suspects

### Blast radius — F-5

- **Provider spend on the user's own key**, the primary impact: every `silence_minutes` wake that
  passes the ghost-inflated roster check runs a complete turn (`orchestration.py:139`,
  `turn_engine._run_locked`) into an empty room. In a room with ordinary come-and-go traffic the
  ghost is refreshed by every join (`presence.py:108`) and every heartbeat (`:131`), so the condition
  persists until the 03:30 sweep. (Line references corrected 2026-07-27.)
- **Every roster consumer reads the ghost**: the REST presence snapshot
  (`chatrooms.py:631-649`, which the frontend calls on every reconnect via `resyncPresence`,
  `useChatroomSocket.ts:107,354`), and the `every_n_messages` empty-room gate
  (`wakeup_service.py:109-121`) — the latter meaning a gated wake-up is *not* gated and the owner
  notification at `:118` is not sent. (Line references corrected 2026-07-27.)
- **Persisted data**: none. All affected state is Redis (roster SETs and silence timestamps; the
  `silence_active` flag this section originally also listed was deleted by
  `2026-07-22-wakeup-trigger-state-and-bounds` C1 — see Q-6). The durable residue is the audit trail of `wakeup.fired` rows
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
`presence.py:108` and `:131`, both of which `EXPIRE` the roster key:

- Ghost-holding roster keys are continuously refreshed, so the room stays visible to the nightly
  reconciler's `scan_iter` (`presence.py:254-259`), which SREMs the ghost from the roster.
- The churn does **not** clear the ghost between sweeps (nothing SREMs the roster except `leave` and
  the scrub), so the false-wake window is extended for as long as the churn continues.

Once F-1 is fixed and sockets are long-lived, nothing refreshes the roster key after the last real
user departs. The key expires at +300 s (`presence.py:36`) and the room drops out of `scan_iter`
entirely (`presence.py:271-272`), so the nightly scrub never reaches it. The window in which a ghost
can be read as live shortens to that 300 s, but within it nothing repairs the roster at all.
(Line references corrected 2026-07-27.)

**Re-baselined 2026-07-27 (Q-6).** This paragraph originally continued: "the `has_live_users=False`
edge is lost permanently rather than delivered late... the wrong state becomes durable and unrepaired
for up to the flag's 7-day TTL". That consequence no longer follows.
`2026-07-22-wakeup-trigger-state-and-bounds` C1 deleted the cached `silence_active` flag, so there is
no durable wrong state for a lost edge to leave behind — the evaluator re-reads the roster on every
30 s sweep (`wakeup_service.py:240-245`). What remains true, and is the entire justification for
Part A1, is narrower and sharper: **the roster itself is the only remaining liveness authority**, so
a ghost in it is now read directly as "a live user is present" with nothing downstream to catch it.
Before C1 a stale flag and a ghost roster were two independent ways to reach the same wrong answer;
after C1 there is one way, and A1 closes it. Note also that
`2026-07-27-wakeup-sweep-failure-isolation` removes the `retention.py` `emptied_rooms` →
`evaluate_presence_change` hook entirely, on the grounds that it has become a no-op; that removal is
sequenced *after* this dossier and does not affect A1, which never depended on it.

**Ordering constraint.** Preference, not a blocker: land this dossier **before or together with** the
socket dossier. Fixing F-1 first removes the accidental repair path described above while F-5 is
still open. There is no code overlap — this fix touches `presence.py`, `wakeup_service.py` and
`turn_engine.py`; the socket dossier touches `connection.py`, `ws-manager.ts` and (for F-18)
`chatroom.py:179-199`, whose `on_close` this fix leaves unchanged. (Line reference corrected
2026-07-27.)

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

- `wakeup_service.py:109-121` (`every_n_messages` empty-room gate) — **confirmed affected by the same
  root cause**. Same `list_room` call, same ghost. Fixed by the same change; no separate work.
  (Line reference corrected 2026-07-27; the gate itself is unchanged.)
- `wakeup_service.py:240-245` (silence roster gate) — **confirmed affected**, as analysed in §5
  step 6. Post-C1 this is no longer a "backstop" behind a flag but the primary and only gate, which
  raises rather than lowers A1's importance (Q-6).
- `chatrooms.py:638-641` (`GET /{id}/presence`) — **confirmed affected**; returns ghost user_ids to the
  presence rail. Fixed by the same change.
- `scrub_stale_presence` (`presence.py:235-273`) — **cleared.** It already performs exactly the
  cross-check this fix generalises (`:262-268`), and its `emptied_rooms` contract is correct,
  including the mixed live/stale case pinned at `test_presence.py:161-176`. Its only defect is
  cadence and reachability, recorded as FU-2.
- `ws:user:{user}:rooms` reverse index (`presence.py:50-51`, written at `:110-111`) — **cleared as a
  defect source**; it has no liveness-sensitive reader. It is written back by the scrub (`:268`) and
  must be kept consistent by the new reconciliation, which the fix does (§7).
- `presence.heartbeat` (`presence.py:115-133`) — **cleared.** Re-asserting membership on every frame
  is correct for a live connection; it cannot resurrect a dead one because a dead connection sends no
  frames. (Line references corrected 2026-07-27.)

**F-21 siblings — systemic sweep.** The systemic form is "a trigger kind tested against a literal
rather than the shared tuple". Repo-wide sweep over `backend/` (excluding `.venv`) for
`trigger ==`, `trigger !=`, `trigger in (`, `trigger not in` returns exactly four sites:

| Site | Form | Verdict |
|---|---|---|
| `app/workers/tasks/orchestration.py:81` | `trigger in ("mention", "release")` | **Cleared** — correct set, and the reference the finding cites as the intended parity. |
| `app/workers/tasks/orchestration.py:109` | `trigger not in ("mention", "release")` | **Cleared** — same set, autostop bypass. |
| `contexts/agents/application/runtime/turn_engine.py:1880` | `trigger == "mention"` (`not_bound`) | **Confirmed defective** — F-21 as filed. |
| `contexts/agents/application/runtime/turn_engine.py:1872` | `trigger == "mention"` (`agent_gone`) | **Confirmed defective — sibling not named in the finding.** Reachable: the worker's `agent_gone` guard reads the agent at `orchestration.py:79`, the engine re-reads it at `turn_engine.py:1868`, and a soft-delete landing between the two reaches the engine's branch. Same literal, same missing `"release"`. Must be fixed in the same change. (Line references corrected 2026-07-27.) |

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
what `scrub_stale_presence:262-268` already does — and returns the live set. Use it in:

- `list_room` (`presence.py:230-232`): return the reconciled set instead of raw `SMEMBERS`.
- `leave` (`presence.py:135-160`): after the departing member's own `SREM`, derive `roster_size`
  from the reconciled set instead of the Lua `SCARD` at `:156`. The multi-tab early return at
  `:153-154` stays untouched, so the reconcile runs only on a genuine last-connection leave.
  (Line references corrected 2026-07-27.)

**Why this corrects rather than masks.** The symptom is an agent waking into an empty room; the
proximate cause is a downstream guard being fed a false liveness answer; the false answer originates
in a set that R15.05b (`REQUIREMENTS.md:753`) *defines* as "users who currently have an open
WebSocket connection". Making the set mean what the requirement says it means removes the defect for
every consumer at once (§6 siblings), rather than adding a third guard downstream of a set that
still lies. It also restores the module's own stated contract of "at most one TTL window of UI lag"
(`presence.py:16-21`), which is currently false by up to 24 hours.

**Race safety.** Reconciliation cannot evict a genuinely joining user: `join` writes the conns key
(`presence.py:105`) strictly before the roster entry (`:108`), so any member visible in the roster
already has a live conns key by construction. (Line references corrected 2026-07-27.) The only user
an `EXISTS` check can find missing is one
whose conns SET has actually expired or been deleted.

**Cost.** One `EXISTS` per roster member per reconciled read, pipelined. Room rosters are small, the
`leave` path reconciles only on last-connection close, and `list_room`'s callers are a 30-second cron
(`wakeup_service.py:243`), the per-message every_n gate (`:116`), and an explicit REST snapshot
(`chatrooms.py:638-641`). (Line references corrected 2026-07-27.)

**A2 — WITHDRAWN 2026-07-27 (Q-6).** As originally written: "Level-triggered convergence
(`wakeup_service.py:229-237`) — when the roster re-check finds no live members for a non-observer
binding, pause the flag via `wakeup_state.set_silence_active(agent_id, room_id, False)` before
returning `False`, so no single lost edge can leave a timer armed for 7 days."

`2026-07-22-wakeup-trigger-state-and-bounds` C1 delivered this property by a different and better
route: it deleted the cached flag outright, so `evaluate_silence_trigger` re-reads the live roster on
every sweep (`wakeup_service.py:240-245`) and is level-triggered by construction. `set_silence_active`
and `is_silence_active` no longer exist — a repo-wide grep returns hits only in `docs/`. There is
nothing left to pause and nothing left to converge.

**A1 is therefore the entirety of Part A, and it is not weakened by the withdrawal.** A2 was the
belt to A1's braces; with the flag gone, A1 is load-bearing on its own (Q-6). /build must not read
this withdrawal as "Part A got smaller" and skip it.

Observer bindings remain exempt from the roster gate by design
(`wakeup_service.py:211-213,242`, O-2/R28.04) — unchanged by C1 and unchanged here.

**Deliberately out of scope**: changing the scrub cadence or removing `_SET_TTL_SECONDS`
(`presence.py:36`). With A1 and A2 in place the nightly sweep is no longer load-bearing for this
defect; tuning it is FU-2.

### Part B — F-21: name the predicate once

**B1.** Add `EXPLICIT_TRIGGERS: Final = frozenset({"mention", "release"})` to
`backend/contexts/orchestration/domain/models.py`, next to the wake-up trigger vocabulary and beside
`autostop_limit_for` (`:172-176`), which exists for the identical anti-divergence reason. (Line
reference corrected 2026-07-27.)

**B2.** Replace the literals at `turn_engine.py:1872` and `:1880` with membership tests against the
imported constant, and replace both worker tuples (`orchestration.py:81`, `:109`) with the same
constant so no copy of the set remains. Update the comments at `orchestration.py:82-88` and
`:104-108` to point at the constant rather than at each other. (Line references corrected
2026-07-27.)

**Why this corrects rather than masks.** Adding `or trigger == "release"` at `:1880` would fix the
reported instance and leave the duplication — the actual cause — intact, guaranteeing the same bug
when the next explicit trigger kind is added. A single named set makes the parity structural and
makes the §8 table-driven test a permanent guard rather than a one-off assertion.

### Data repair

**None required, and none attempted, for either defect.**

- F-5 state is entirely Redis and volatile. Ghost roster entries self-heal on the first reconciled
  read or leave after deploy (and, worst case, expire at `_SET_TTL_SECONDS`). No migration, no
  backfill script, no Redis surgery. (Re-baselined 2026-07-27: this bullet originally also promised
  that "silence flags already armed converge at the next 30-second sweep via A2". There are no
  silence flags — see Q-6 — and the silence *timestamp* that replaced them needs no repair, since
  `evaluate_silence_trigger` seeds it lazily and the roster gate blocks firing while a room is
  empty.)
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
*Fails today*: `_ROSTER_LEAVE_LUA` (`presence.py:84`, called at `:156`) `SCARD`s the ghost-inflated
set and returns `(True, 1)`, which is exactly why `chatroom.py:198` never fires the transition.
(Line references corrected 2026-07-27.)
The existing `_FakeRedis` already implements `exists` (`test_presence.py:47-48`), so no harness
change is needed.

**T-2 — `backend/tests/unit/test_presence.py::test_list_room_omits_and_evicts_a_member_with_no_live_connection`**
Same fixture. Asserts `set(await p.list_room(room)) == {live}`, and that `stale` is gone from both
`ws:presence:{room}` and `ws:user:{stale}:rooms` in the fake store.
*Fails today*: `list_room` (`presence.py:230-232`) is a bare `SMEMBERS` and returns both users; the
reverse index is never touched on read. (Line reference corrected 2026-07-27.)

**T-3 — `backend/tests/unit/test_presence.py::test_multi_tab_leave_does_not_reconcile`**
Second tab of one user closes while the first is live: asserts the early return
`(False, -1)` (`presence.py:153-154`) is preserved and no `EXISTS` reconciliation ran.
(Line reference corrected 2026-07-27.)
*Fails today*: no — this one **passes today** and is included as a guard that A1 does not move the
reconcile onto the hot multi-tab path or break the `-1` sentinel `chatroom.py:179-199` relies on.
(Line reference corrected 2026-07-27.)
Named explicitly so it is not mistaken for a new failing test.

**T-4 — WITHDRAWN 2026-07-27 (Q-6).** Originally
`test_wakeup_service.py::test_empty_roster_pauses_the_silence_flag`, asserting that an empty roster
wrote `set_silence_active(..., False)`. That function no longer exists, and the property it tested is
now structural rather than behavioural. Its replacement already exists and is green:
`test_wakeup_service.py:58-67` and `:79-87` (`test_allow_self_open_true_does_not_fire_into_empty_room`
and `test_allow_self_open_false_still_does_not_fire_into_empty_room`) pin that an empty roster
suppresses the trigger on every evaluation, which is what A2 was trying to guarantee. Moved into
T-9's must-stay-green list. (Line references corrected 2026-07-27.)

**T-5 — RESCOPED 2026-07-27 (Q-6).**
`backend/tests/unit/test_wakeup_service.py::test_observer_silence_fires_in_empty_room` already exists
at `:98-106` and asserts exactly the surviving half: `is_observer=True` with `room_members=[]` still
fires. The `set_silence_active`-was-never-called half is withdrawn with A2. No new test; moved into
T-9's must-stay-green list as the guard that A1's reconciliation does not accidentally start gating
observer bindings. (Line reference corrected 2026-07-27.)

**T-6 — `backend/tests/unit/test_no_response_notices.py::test_not_bound_emits_on_release`**
`_wire_locked(agent=_locked_agent(), bound=False)`, `_run_locked(trigger="release")`.
Asserts `result.reason == "not_bound"` and `emitted[0][1]["error"] == "not_bound"`.
*Fails today*: `turn_engine.py:1880` tests `trigger == "mention"`, so `emitted == []` and the
subscript raises `IndexError`.

**T-7 — `backend/tests/unit/test_no_response_notices.py::test_agent_gone_emits_on_release`**
Same harness with `agent=None`, `trigger="release"`; asserts the `agent_gone` notice is emitted.
*Fails today*: `turn_engine.py:1872`, the sibling identified in §6 and not named in the finding.
(Line references corrected 2026-07-27.)

**T-8 — `backend/tests/unit/test_no_response_notices.py::test_explicit_triggers_all_emit_and_autonomous_stay_silent`**
Table-driven over the shared `EXPLICIT_TRIGGERS` constant: for every member, assert `not_bound` and
`agent_gone` both emit; for `"every_n_messages"` and `"silence_minutes"`, assert `emitted == []`.
*Fails today*: `"release"` is a member of the intended set (`orchestration.py:81`) and the engine is
silent for it. This is the systemic guard — it fails automatically if a future trigger kind is added
to the set and the engine is not updated.

**T-9 — regression guards that must stay green, unmodified**:
`test_no_response_notices.py:146-150` and `:162-166` (autonomous triggers stay silent — line
references corrected 2026-07-27; proves the
predicate widened to exactly the explicit set, not to all triggers);
`test_agent_trigger_wiring.py:356-371` and `:507-521` (worker-side release parity — proves B2's
constant substitution changed no worker behaviour);
`test_presence.py:105-127`, `:130-144`, `:147-157`, `:161-176` (existing presence semantics);
`test_retention_deep.py:836-908` (the scrub's `emptied_rooms` hook is unchanged — line reference
corrected 2026-07-27; these three tests are the ones the *sibling* dossier
`2026-07-27-wakeup-sweep-failure-isolation` later deletes when it removes the retention hook
entirely per its Q-3, which is exactly why that dossier `depends_on` this one: they must stay green
through this dossier's build and are superseded, not broken, by the next one);
and, added 2026-07-27 in place of the withdrawn T-4/T-5,
`test_wakeup_service.py:58-67` and `:79-87` (an empty roster suppresses for both `allow_self_open`
values — this is the post-C1 statement of the property A2 was meant to provide) and `:98-106`
(`test_observer_silence_fires_in_empty_room` — A1's reconciliation must not start gating observers).
(Line references corrected 2026-07-27.)

## 9. Risks and Rollback

- **Over-eager ghost eviction.** After A1, a live connection whose conns key lapses is evicted from
  the roster. Today `_CONN_TTL_SECONDS = 150` is safe only because the 120 s idle reaper forces an
  inbound frame per window (`presence.py:31-35`). **The socket-lifecycle dossier must keep its
  keepalive interval strictly below 150 s or raise this constant in the same change** (§6). If a
  keepalive is introduced with a longer interval, live users would silently vanish from rosters —
  the inverse of the current defect and more visible.
- **Extra Redis round-trips.** One `EXISTS` per roster member on reconciled reads. Bounded by room
  size, pipelined, and on the `leave` path gated behind the last-connection check
  (`presence.py:153-154`). The `/presence` endpoint (`chatrooms.py:638-641`) is the highest-frequency
  caller and is already one round-trip per reconnect. (Line references corrected 2026-07-27.)
- **Extra Redis write on the silence path — no longer applicable (Q-6).** This risk belonged to the
  withdrawn A2. With the flag deleted, A1 adds reads only; the silence path issues no new writes.
- **Behaviour change for consumers that relied on ghosts.** None found: §6 enumerates all three
  `list_room` consumers and in every case the ghost is the defect, not a dependency.
- **B2 blast radius.** Substituting the shared constant into `orchestration.py:81` and `:109`
  changes no worker behaviour — the constant has the same members as the tuples it replaces — and
  T-9's worker tests pin that. (Line references corrected 2026-07-27.)
- **Rollback.** Both parts are additive, self-contained, and touch no schema, no migration and no
  persisted data. Reverting the commit restores prior behaviour immediately; any Redis state written
  in the interim (an evicted ghost) is volatile and correct under the
  old code as well. No rollback script is required.

## 10. Acceptance Criteria

- [x] **AC-1**: The regression tests from §8 (T-1, T-2, T-6, T-7, T-8) fail before the fix and
  pass after; T-3 and every test named in T-9 pass both before and after. (Re-baselined 2026-07-27:
  T-4 is withdrawn and T-5 rescoped into T-9 per Q-6.)
- [x] **AC-2**: `PresenceTracker.leave` returns `roster_size == 0` when the only remaining roster
  members are entries with no live conns key, so `chatroom.py:198` fires
  `_notify_presence(has_live_users=False)` for the last real leaver. (Line reference corrected
  2026-07-27.)
- [x] **AC-3**: `PresenceTracker.list_room` returns only members with a live conns key, and evicts
  the others from both `ws:presence:{room}` and `ws:user:{user}:rooms` as it goes.
- [x] **AC-4**: The multi-tab early return `(False, -1)` (`presence.py:153-154`) is preserved, and
  no reconciliation runs on a non-last-connection leave.
- [x] **AC-5 — WITHDRAWN 2026-07-27 (Q-6)**: originally "`evaluate_silence_trigger` pauses
  `silence_active` when the reconciled roster is empty". The flag was deleted by
  `2026-07-22-wakeup-trigger-state-and-bounds` C1, which made the evaluator level-triggered by
  reading the roster on every sweep. Replaced by the standing assertion that
  `set_silence_active` / `is_silence_active` appear nowhere in `backend/` — already true on `main`,
  and this dossier must not reintroduce them.
- [x] **AC-6**: Observer bindings still bypass the roster re-check (O-2/R28.04), verified by
  `test_wakeup_service.py:98-106` staying green. (Line reference corrected 2026-07-27.)
- [x] **AC-7**: A single `EXPLICIT_TRIGGERS` constant exists in
  `contexts/orchestration/domain/models.py`, and a repo-wide sweep for `trigger ==` /
  `trigger in (` / `trigger not in` over `backend/` (excluding `.venv`) returns **no** site testing a
  trigger kind against a literal — all four current sites read the constant.
- [x] **AC-8**: A release wake to an unbound agent emits `agent.finished{error: "not_bound"}` on the
  room channel; to a deleted agent reaching the engine's own guard, `agent_gone`.
- [x] **AC-9**: Autonomous triggers (`every_n_messages`, `silence_minutes`) remain silent for both
  `not_bound` and `agent_gone`.
- [x] **AC-10**: No data-repair script, migration, or audit-row rewrite is introduced (§7).
- [x] **AC-11**: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in
  `backend/`. No frontend change is expected; if none is made, `frontend/` gates are not required.
  (`pytest tests/unit -q` run rather than the bare `-q` from `backend/`: `testpaths` covers
  `tests/integration` and `tests/wiring` too, which require live Postgres/Redis/Vault not present in
  this environment; 6054 passed, 6 skipped for pre-existing, unrelated environment reasons —
  symlink support and path-separator normalization on Windows.)
- [x] **AC-12**: The `_CONN_TTL_SECONDS` constraint from §6/§9 is recorded as a comment at
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

**D-1.** AC-11 names `pytest -q` in `backend/`; the build instead ran
`pytest tests/unit -q`. Reason: `pyproject.toml`'s `testpaths = ["tests"]` and its
`addopts` do not exclude the `integration`/`wiring` markers by default, so a bare
`pytest -q` from `backend/` attempts every test including ones that require a live
Postgres/Redis/Vault stack (per `backend/CLAUDE.md`'s own test split) — none of which
are running in this build environment, and the run hung rather than failing fast. The
unit suite (6054 tests, the ones this fix's regression tests belong to) is a complete,
reliable substitute for this task's verification purposes; it does not substitute for a
pre-deploy run of the full suite against a live stack.

## 13. Follow-ups

- **FU-1** — Reconciliation and `scrub_stale_presence` evict roster members without publishing
  `presence.left` (`presence.py:267`, contrast `chatroom.py:193-197`), so other members' rails heal
  only on the next `resyncPresence` (`useChatroomSocket.ts:107,354`). Decide whether an eviction
  should announce itself, and from which layer.
- **FU-2** — The presence reconciler runs once nightly (`retention.py:746`, `main.py:334`) and a room
  whose roster key has expired (`presence.py:36`) is invisible to it entirely
  (`presence.py:254-259,271-272`). Now that reads self-heal, revisit: either a minutes-cadence
  reconciler or dropping the roster key's TTL so rooms cannot vanish from it. (Line references
  corrected 2026-07-27.)
- **FU-3** — A note pushed to `pending_notify` (`observations.py:224-235`) for an agent that is
  unbound before its wake runs is never retracted; it drains into that agent's next turn, possibly in
  another room. Decide retract-or-scope semantics for released observations. Called out as
  "lingers as misrouted until its TTL" in `findings.md:566`.
- **FU-4** — Trigger kinds remain stringly typed at every producing site
  (`messages.py:354`, `observations.py:212,241`, `orchestration.py:30,262`). A `StrEnum` in the same
  domain module as `EXPLICIT_TRIGGERS` would make the vocabulary checkable; deferred so this bugfix
  does not become a refactor.
- **FU-5** — `wakeup_service.py:109-121`'s every_n empty-room gate silently benefits from this fix
  but has no test of its own for the ghost case; a characterization test there would be cheap
  hardening. (Line reference corrected 2026-07-27.)
- **FU-6** — `presence.py`'s new `_reconcile_roster` (added by C1/A1) and `scrub_stale_presence`
  now contain conceptually duplicated eviction logic ("check each member's conns key, `SREM` the
  stale ones from both the roster and the reverse index"), just at different iteration
  granularities (one room's already-fetched members vs. a `scan_iter` across every room). Flagged
  Info-level by the build's quality audit; worth a look the next time either function is touched,
  not urgent enough to justify a shared abstraction today.
</content>
