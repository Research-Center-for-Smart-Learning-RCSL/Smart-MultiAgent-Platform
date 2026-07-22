---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R15.01, R15.02, R15.03, R15.04, R15.05b, R15.06, R15.07, R15.08, R15.09, R28.12]
depends_on: []
---

# Wake-up trigger state initialization and config bounds

## 1. Summary

Five confirmed defects in the wake-up subsystem. Two of them make a headline feature silently
dead: the `silence_minutes` trigger never fires for any binding created after the room's
presence edge (F-3), and an `autostop_rounds` of `0` permanently suppresses the same trigger
while the worker gate reads the same value as `100` (F-21 / config-runtime F-24, and per FU-5
the identical hole exists on the observer arm). Two more silently discard designer intent: the
first agent self-modification erases designer-set `soft_bounds` (F-12), and `refresh_every_hours`
is parsed, editable in the UI, and never read, so every agent is snapped back hourly regardless
of configuration (F-14). The fifth is a non-atomic `INCR`-then-`EXPIRE` that can leave wake-up
counter keys immortal (F-38). User-visible impact: agents that were configured to wake never
wake, agents that were bounded escape their bounds for up to an hour, and self-tuning windows
collapse from days to 60 minutes. Every one of these lands on the user's own provider key.

**These five findings do not share a root cause.** They form two co-located clusters plus one
independent site:

- **Cluster A (config lifecycle, `contexts/orchestration/domain/models.py`)** F-21 and F-12 are
  both defects in the same class: `from_dict` at `backend/contexts/orchestration/domain/models.py:150-171`
  applies an upper clamp only, and `to_dict` at `:173-193` is not a lossless round-trip of what
  `from_dict` accepted. F-14 is the missing consumer of a field that same `to_dict` faithfully
  emits (`:192`). Related by file and by lifecycle, not by one cause.
- **Cluster B (Redis wake-up state, `contexts/orchestration/infrastructure/wakeup_state.py`)**
  F-3 and F-38 are both defects in how that module's keys are written: F-3 is an edge-triggered
  key that is never written on the path that needs it, F-38 is a durability gap on two other
  keys. Same module, different causes.

They are bundled here because they are one surface, one test file, and one reviewable change,
not because a single edit fixes them.

## 2. Observed vs Expected

**F-3 (major, silence trigger dead for late bindings)**

- **Observed** `wakeup:silence_active:{agent}:{room}` is written only by
  `backend/contexts/orchestration/application/wakeup_service.py:258`, reached only from
  `backend/app/api/ws/chatroom.py:128` (`roster_size == 1`) and `:142` (`roster_size == 0`),
  plus `backend/app/workers/tasks/retention.py:701` which only ever passes
  `has_live_users=False`. `evaluate_presence_change`
  (`backend/contexts/conversation/application/triggers.py:128-131`) snapshots the room's
  bindings at that instant and returns early when the room has no agents. A binding created
  after the 0 to 1 edge therefore has no flag, and `evaluate_silence_trigger`
  (`wakeup_service.py:212`) hard-returns `False` for non-observers when the flag is absent,
  with no lazy set. The same binding also has no `wakeup:silence_ts` key, since the only other
  writer is `on_message_created` (`wakeup_service.py:97`), which iterates the agents bound at
  message time; so `:216-217` would return `False` even if the flag were repaired.
- **Expected** `docs/implement/G-orchestration.md:81` states the silence timer "starts only when
  `ws:presence:{room_id}` is non-empty (live user/guest present); pauses when the set becomes
  empty" (R15.02, R15.05b). The authoritative signal is the roster, not a cached edge flag.

**F-21 / config-runtime F-24 / FU-5 (minor, zero autostop)**

- **Observed** `backend/contexts/orchestration/domain/models.py:159` is
  `min(int(sm.get("autostop_rounds", 5)), AUTOSTOP_HARD_CAP)`, an upper clamp only, and `:161-163`
  is the same for `observer_autostop_rounds`. `wakeup_config` is typed `BoundedConfig`
  (`backend/app/api/v1/agents.py:89,123`, `backend/shared_kernel/validation.py:90`), size-bounded
  only, so `0` and negatives persist. `wakeup_service.py:224-227` then evaluates
  `0 >= 0` and returns `False` on every 30 s sweep forever, while
  `backend/app/workers/tasks/orchestration.py:111-113` layers a zero-fallback on top
  (`effective_limit = autostop_limit if autostop_limit > 0 else sm.autostop_max_default`) and
  reads the same field as 100. `reset_autostop` (`wakeup_state.py:172-177`) only zeroes the
  count, so `0 >= 0` still holds after a user speaks.
- **Expected** R15.04: "`autostop_rounds` default and hard cap: default 5, hard cap 100."
  R28.12: `observer_autostop_rounds` "default 50, hard cap 100." `models.py:95-96` states the
  caps are applied at parse time "regardless of how the JSONB was written (designer UI, direct
  DB edit, migration, etc.)", which is only half true today.

**F-12 (major, soft bounds erased)**

- **Observed** `models.py:173-193` emits exactly `triggers`, `allow_self_open`,
  `refresh_every_hours`; `soft_bounds` is dropped. `wakeup_service.py:329-337` builds
  `d = fresh_cfg.to_dict()` from scratch and `backend/contexts/agents/application/agent_service.py:609-610`
  performs a whole-column JSONB replace with no merge. `_parse_soft_bounds`
  (`wakeup_service.py:446-456`) then returns empty bounds and `_clamp_n` (`:434-438`) falls back
  to `N_MIN = 1`.
- **Expected** R15.08: "Platform Admin can also set soft per-agent bounds at creation time;
  self-modification must respect these." `docs/implement/G-orchestration.md:98` repeats it.
  Respecting a bound the second time requires the bound to still exist after the first write.

**F-14 (major, `refresh_every_hours` unused)**

- **Observed** `refresh_every_hours` is parsed at `models.py:170`, emitted at `:192`, and edited
  at `frontend/src/shared/ui/SWakeupEditor.vue:119-122`. `backend/app/workers/main.py:322`
  registers `cron(wakeup_refresh, minute=0)`; `backend/app/workers/tasks/orchestration.py:296-299`
  iterates every agent holding a snapshot and calls `refresh_wakeup_config` unconditionally;
  `wakeup_service.py:377-428` never reads the field and keeps no last-refresh timestamp.
- **Expected** R15.09 (`REQUIREMENTS.md:763`): "The Agent Designer can configure a
  `refresh_every_hours` value. Every T hours, the wake-up configuration is reset to the Agent
  Designer's initial values." `docs/implement/G-orchestration.md:108` restates it.

**F-38 (minor, counter TTL loss)**

- **Observed** `wakeup_state.py:88-89` and `:167-168` issue `INCR` then `EXPIRE` as two separate
  awaits, with no pipeline and no `MULTI`, against the TTL promise stated at `:23`
  ("7 days, stale keys auto-expire").
- **Expected** `wakeup_state.py:23`. No requirement-level source; the intent source is the
  module's own documented contract. In-repo precedent for the correct pattern:
  `backend/contexts/conversation/infrastructure/presence.py:98,117-119`.

**FU-1 (frontend default divergence, folded in, see Q-3)**

- **Observed** `frontend/src/shared/types/workflow.ts:69-82` sets `autostop_rounds: 3` where the
  backend default is 5 (`models.py:110,159`), `every_n_messages.n: 5` where the backend default is
  3 (`models.py:103,154`), and `silence_minutes.t_minutes: 30` where the backend default is 2
  (`models.py:109,158`); `observer_autostop_rounds` is absent from the client shape entirely.
  `normalizeNestedTriggers` (`workflow.ts:89-97`) spreads these defaults over any partial config,
  so an omitted field is displayed and then persisted at the client's value.
- **Expected** the comment at `workflow.ts:61-68` states these client defaults mirror the backend
  so "a value this module invents client-side never disagrees with what an omitted field would
  resolve to server-side." Three of them disagree.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What is the correct lower clamp for each numeric wake-up field? | `every_n_messages.n` and `silence_minutes.t_minutes` clamp to their documented hard floors (1 and 1). `autostop_rounds` and `observer_autostop_rounds` fall back to their documented defaults (5 and 50). `refresh_every_hours` clamps to 1. | R15.07 documents explicit floors for n and t_minutes, and `_clamp_n` / `_clamp_t` (`wakeup_service.py:434-444`) already clamp to `N_MIN`/`T_MINUTES_MIN` on the self-modification path, so the parse layer should agree with the path next to it. R15.04 and R28.12 document only a default and a cap for the autostop fields, no floor; for those, the least astonishing valid substitute for an invalid input is the value an omitted field already resolves to. Clamping autostop to 1 would silently make agents far more restrictive than the current worker behavior (100). |
| Q-2 | Where does the last-refresh timestamp for R15.09 live? | **OPEN, needs user decision.** Recommended: a new nullable `wakeup_last_refreshed_at timestamptz` column on `agents` (migration 0062, latest is `0061_graphrag_owner_index_live_only.py`). Alternative: a Redis key `wakeup:last_refresh:{agent_id}`, no migration. | Durability decides it. A Redis-only clock silently reverts to today's buggy hourly behavior after a flush, which is exactly the defect being fixed. The column costs a migration and a repository/facade field. This is the only part of the dossier with schema impact, so it is a user call. Deriving the clock from `agent.wakeup_refreshed` audit rows was rejected: `refresh_wakeup_config` early-returns without auditing when nothing drifted (`wakeup_service.py:390-392`), and a per-agent audit query on an hourly sweep is a poor hot path. |
| Q-3 | Adopt FU-1 (frontend defaults) into this dossier, and which fields? | **PARTIALLY OPEN, needs user decision.** Recommended: fix `autostop_rounds` 3 to 5, `every_n_messages.n` 5 to 3, and add `observer_autostop_rounds: 50`, all of which are unambiguous mirroring bugs. `silence_minutes.t_minutes` 30 vs backend 2 is flagged for the user: 30 may be a deliberate UX default. | FU-1 explicitly routes to "the agent-config surface", which is this dossier, and the three recommended fields are mechanical corrections to a stated invariant. `t_minutes: 2` as a shown default would make an enabled silence trigger fire after two minutes, which is a product decision, not a bug fix. If the user prefers 30, the comment at `workflow.ts:61-68` must be narrowed instead of the value changed. |
| Q-4 | Fix F-3 by lazily setting the `silence_active` flag, or by removing the flag and reading the roster? | Remove the flag. Delete `set_silence_active` / `is_silence_active` (`wakeup_state.py:133-152`) and the write at `wakeup_service.py:258`; rely on the live-roster read already performed unconditionally at `wakeup_service.py:234-237`. | `docs/implement/G-orchestration.md:81` names `ws:presence:{room_id}` as the signal. The flag is a cache of that set which the code itself already distrusts: the comment at `wakeup_service.py:229-233` says the flag "can go stale" and adds an unconditional roster re-check for that reason. Lazily filling the cache patches the fill path and leaves the staleness class alive; deleting the cache removes it. The flag has exactly one reader (`wakeup_service.py:212`), so the removal is contained. |
| Q-5 | Q-4 invalidates an existing test. Rewrite it? | Yes. `backend/tests/unit/test_wakeup_service.py:119-130` (`test_normal_silence_still_respects_paused_flag`) asserts no fire when the flag is `False` and the roster is non-empty. Under the fix that combination fires. Rewrite it as `test_normal_silence_follows_the_live_roster_not_the_cached_flag`. | That test encodes the flag mechanism, not R15.05b. R15.05b pauses the timer when "the live-user set becomes empty"; a non-empty roster is by definition not empty. Flag `False` with a non-empty roster has no legitimate producer: `on_presence_changed(False)` fires only at roster 0 (`chatroom.py:141-142`) and the retention scrub only for emptied rooms (`retention.py:698-701`), so that state is precisely the F-3 staleness being removed. The empty-roster suppression tests at `:62-71` and `:84-92` are unchanged and stay as the guard against over-fixing. |
| Q-6 | Fold the identical F-38 pattern in `contexts/knowledge` into scope? | Yes. `backend/contexts/knowledge/application/graphrag_triggers.py:63-69` is the same `INCR` then bare `EXPIRE` on a counter with no delete path. | §6's rule: a fix that patches one instance of a systemic mistake is half a fix. The change is two lines and mechanical. The scope line is drawn at "counter with no reliable delete path"; see §6 for the sites cleared under that line. |
| Q-7 | Data repair for `soft_bounds` already erased in production? | No backfill migration. Recovery is the existing hourly refresh. A one-time operator query identifies the unrecoverable minority. | See §7, "Data repair position". |
| Q-8 | Should `to_dict` also preserve designer keys the domain model does not know about? | Yes, by overlay rather than by modelling. `soft_bounds` becomes a first-class field on `WakeupConfig`; additionally `_build_new_dict` starts from the stored dict and overlays the serialized config, so any other free-form key a designer wrote survives. | `wakeup_config` is `BoundedConfig` (`app/api/v1/agents.py:89,123`), that is, free-form. Modelling `soft_bounds` alone fixes the finding but leaves the same silent-drop mechanism armed for the next key anyone adds. The overlay makes the write additive rather than replacing. |

## 4. Reproduction

**Preconditions** a project with one agent A and one chat room R; the user is a project member;
Redis and the Arq worker running with the `evaluate_silence` (30 s) and `wakeup_refresh` (hourly)
crons registered (`backend/app/workers/main.py:322`).

**F-3** (100% reproducible, the most common setup order)

1. Create room R. Open it as Alice. The 0 to 1 presence edge fires (`app/api/ws/chatroom.py:127-128`)
   while R has no agents, so `evaluate_presence_change` returns early at
   `contexts/conversation/application/triggers.py:130-131`.
2. Bind agent A to R with `silence_minutes: {enabled: true, t_minutes: 2}`.
3. Stay connected and send nothing for 10 minutes.
4. Observe: no wake-up. `redis-cli GET wakeup:silence_active:{A}:{R}` is nil and
   `GET wakeup:silence_ts:{A}:{R}` is nil. Every 30 s sweep returns `False` at
   `wakeup_service.py:212`.
5. Drop every Alice connection (roster to 0), rejoin, wait 2 minutes: the trigger now works.
   A second user joining does not help, since `roster_size == 1` is required.

**F-21 / FU-5** (API only; the editor clamps to >= 1 at `SWakeupEditor.vue:105-109`)

1. `PATCH /api/agents/{A}` with
   `{"wakeup_config": {"triggers": {"silence_minutes": {"enabled": true, "t_minutes": 5, "autostop_rounds": 0}}}}`.
   Returns 200.
2. Have Alice join R (so F-3 does not mask this), send a message, then go quiet past 5 minutes.
3. Observe: no silence wake-up, ever, at any autostop count including 0.
4. Repeat with `observer_autostop_rounds: 0` and an observer-role binding: same result on the
   observer arm.
5. In the same state, an `every_n_messages` wake-up for A passes the worker gate, because
   `orchestration.py:111-113` resolves the same field to 100.

**F-12**

1. `PATCH /api/agents/{A}` with `{"wakeup_config": {..., "soft_bounds": {"n_min": 5, "n_max": 10}}}`.
2. Have A call `update_wakeup(every_n_messages=1)` during a turn. It is correctly clamped to 5
   and `agent.wakeup_clamped` is audited.
3. `GET /api/agents/{A}`: `wakeup_config.soft_bounds` is gone.
4. Have A call `update_wakeup(every_n_messages=1)` again in the same hour. It lands at 1, with no
   clamp audit. A now wakes on every message until the next hourly refresh.

**F-14**

1. Set `refresh_every_hours: 24` on A.
2. At 14:05 have A call `update_wakeup(silence_minutes=30)`.
3. At 15:00 the `wakeup_refresh` cron runs. `GET /api/agents/{A}` shows the authored value
   restored and an `agent.wakeup_refreshed` audit row exists. The self-modification survived
   55 minutes, not 24 hours.

**F-38** (not deterministically reproducible; a two-round-trip window)

Kill the Redis connection between the `INCR` at `wakeup_state.py:88` and the `EXPIRE` at `:89`.
The hypothesis for nondeterminism is stated in the finding itself: the window is one round trip.
Closest deterministic attempt is the unit test in §8, which asserts the two commands are issued
in one pipeline rather than attempting to hit the window.

## 5. Root Cause Analysis

**F-3, root cause: the silence gate reads an edge-triggered cache of room presence instead of
room presence, and nothing initializes that cache (or the silence clock) at binding creation.**

Causal chain:

1. `app/api/ws/chatroom.py:127-128` fires the presence transition only on `roster_size == 1`,
   correctly (the comment at `:122-126` explains the idempotency reasoning; that part is right).
2. `contexts/conversation/application/triggers.py:128-131` resolves the affected agents from the
   bindings that exist at that instant, and returns early when there are none.
3. `wakeup_service.py:257-260` therefore writes both `silence_active` and `silence_ts` only for
   agents already bound.
4. A binding created afterwards has neither key. There is no other producer:
   `set_silence_active` has exactly one non-test caller (`:258`) and one other invocation path
   (`retention.py:701`) that only ever passes `False`; `touch_silence_timestamp` is otherwise
   written only by `on_message_created` (`wakeup_service.py:97`, for agents bound at message
   time) and by the post-fire debounce (`orchestration.py:266`).
5. `wakeup_service.py:212` hard-returns `False` on the missing flag. **This is the earliest link
   whose correction prevents the symptom.**

Aggravating factor, not the root cause but load-bearing for the fix: link 4 leaves the silence
*timestamp* missing too, so `:216-217` is a second dead arm. Repairing only the flag leaves the
trigger dead. Any fix must seed both, which is why the design in §7 addresses `:212` and `:216`
together.

**F-21 / F-24 / FU-5, root cause: `WakeupConfig.from_dict` applies an upper clamp only, so the
parse layer does not produce a valid config, and one of the two consumers compensates while the
other does not.**

1. `models.py:159,161-163` clamp above and not below.
2. `app/api/v1/agents.py:89,123` accepts `wakeup_config` as free-form `BoundedConfig`, so `0` and
   negatives reach the parser.
3. `wakeup_service.py:224-227` compares `count >= 0`, always true.
4. `orchestration.py:111-113` layers a zero-fallback at one call site only.

Carrying forward the corrections recorded in config-runtime F-24: the shared helper
`autostop_limit_for` (`models.py:117-121`) *is* called by both sites (`wakeup_service.py:224`,
`orchestration.py:101`), and its "single source of truth" docstring refers to *which cap field*
is selected for a role, which does not diverge. The divergence is the zero-fallback layered on at
one site. `docs/implement/N-conversation-a2a-fixes.md:189-203,228` scopes that FIX-01 fallback
explicitly to the `wakeup_agent` guard, so the evaluator is arguably spec-compliant as written.
The root cause is therefore upstream of both, at `models.py:159`, and neither call site is at
fault. Per FU-5 the identical hole exists on `observer_autostop_rounds` (`models.py:161-163`),
because both fields flow through `autostop_limit_for`.

**F-12, root cause: `WakeupConfig.to_dict()` is used as a whole-column replacement payload but
is not a lossless round-trip of what `from_dict` accepts.**

1. `models.py:173-193` emits three keys; `soft_bounds` and any other designer key are dropped.
2. `wakeup_service.py:330-331` builds the new column value from `to_dict()` alone.
3. `agent_service.py:609-610` replaces the whole JSONB column with no merge.
4. `_parse_soft_bounds` (`:446-456`) then finds nothing and `_clamp_n` (`:434-438`) falls back to
   `N_MIN = 1`.
   Link 1 is the earliest correctable link: if `to_dict` round-trips, the replace at link 3 is
   harmless.

**F-14, root cause: no per-agent refresh clock exists, so the sweep cannot honor a per-agent
interval.** `orchestration.py:296-299` calls `refresh_wakeup_config` for every candidate on every
hourly tick, and `wakeup_service.py:377-428` neither reads `refresh_every_hours` nor records when
it last refreshed. The missing state is the root cause; the unconditional call is the symptom.

**F-38, root cause: `INCR` and `EXPIRE` are issued as two independent round trips.**
`wakeup_state.py:88-89` and `:167-168`. There is no deeper link.

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- F-3: every agent bound to a room after a user joined it, which is the most common setup order
  (create room, join, add agent). Silent: no error, no audit, no UI signal. Observer bindings
  bypass the flag (`wakeup_service.py:212`, `is_observer` guard) but share the missing-timestamp
  arm, so an observer in a room with no messages and no presence edge is equally dead.
- F-21 / FU-5: any agent whose `autostop_rounds` or `observer_autostop_rounds` is 0 or negative.
  Reachable only by bypassing the UI. Fails closed (a trigger stops firing rather than storming),
  hence minor.
- F-12: any agent with designer soft bounds that self-modifies. Bounded by the hourly refresh,
  which restores from `wakeup_authored_snapshot` including `soft_bounds`
  (`agent_service.py:614-615`), so the escape lasts at most one sweep interval. Within that
  window it is a direct cost lever on the user's own provider key. Note the interaction with
  F-14: fixing F-14 (a 24 h window instead of 1 h) *extends* the F-12 escape window by 24x if
  F-12 is not fixed in the same change. They must ship together.
- F-14: R15.06 self-modification is capped at one hour for every agent regardless of
  configuration. Conservative direction (over-resetting), and `wakeup_service.py:390-392`
  early-returns for unmodified agents, so no audit churn in the common case.
- F-38: keys that lose their TTL are immortal only if never incremented again, since any later
  increment re-issues `EXPIRE`. That is exactly the abandoned-room case. Counter arithmetic is
  safe: `INCR` is atomic and `wakeup_service.py:105` consumes its return value.
- FU-1: every agent whose stored `wakeup_config` omits a field, which is the common case
  (`workflow.ts:61-63` says "most agents persist a partial (or empty) wakeup_config"). The
  displayed value is silently persisted on the next save.

**Sibling suspects**

Missing lower bounds elsewhere in the same parser, all in `WakeupConfig.from_dict`:

- **CONFIRMED** `models.py:154`, `n=int(enm.get("n", 3))`, no bounds at all. `n = 0` is accepted;
  `wakeup_service.py:105` guards with `n > 0` so there is no `ZeroDivisionError`, but the
  `every_n_messages` trigger is then permanently and silently disabled. Same shape as F-21, same
  fail-closed direction. `n = 5000` is also accepted, violating R15.07's documented `n ∈ [1, 1000]`.
- **CONFIRMED and fails open** `models.py:158`, `t_minutes=int(sm.get("t_minutes", 2))`, no bounds.
  `t_minutes = 0` makes `elapsed_minutes < 0` never true at `wakeup_service.py:220`, so the
  silence trigger fires on every sweep that clears the other gates, throttled only by the post-fire
  debounce at `orchestration.py:264-266` (one wake per 30 s sweep). This is the only member of the
  family whose failure direction is a cost storm rather than silence, and it violates R15.07's
  `t_minutes ∈ [1, 1440]`. Not raised in any audit; found while tracing F-21. In scope.
- **CONFIRMED** `models.py:170`, `refresh_every_hours=int(raw.get("refresh_every_hours", 24))`,
  no bounds. Harmless today because nothing reads it (F-14), but the F-14 fix makes `0` mean
  "refresh every sweep". Must be clamped in the same change or F-14's fix reintroduces F-14.
- **CLEARED** `models.py:160`, `autostop_max_default`, also unclamped. Its only backend reader is
  the zero-fallback at `orchestration.py:111`, which this dossier removes; after that it is
  backend-dead and read only by the editor as a UI upper bound
  (`SWakeupEditor.vue:107,112`). Clamped anyway for consistency, but it carries no runtime risk.

Non-atomic Redis TTL writes (the F-38 pattern). Scope line: a counter with no reliable delete
path, where TTL loss means an immortal key.

- **CONFIRMED, in scope** `backend/contexts/knowledge/application/graphrag_triggers.py:63-69`,
  `RedisGraphRagMessageCounter.increment`: `await r.incr(key)` then `await r.expire(key, _COUNTER_TTL)`,
  byte-for-byte the same shape as `wakeup_state.py:88-89`. Not covered by
  `docs/tasks/2026-07-14-graphrag-silence-trigger/spec.md`, which discusses the counter's dispatch
  point and not its TTL. See Q-6.
- **CONFIRMED, out of scope, see FU-2** `backend/contexts/orchestration/application/a2a_consumer.py:359-363`,
  `HSET` then bare `EXPIRE` on the retry key. Same class, materially narrower: the key is deleted
  on all four terminal paths (`:301,316,328,341`), so an orphan requires the envelope never to be
  revisited.
- **CLEARED** `backend/shared_kernel/realtime/connection.py:150,165` and
  `backend/shared_kernel/auth/ratelimit.py:110`, `backend/contexts/skills/application/bundle_jobs.py:317`,
  `backend/contexts/workflow/application/executors/wait_for_event.py:82`,
  `backend/contexts/keys/application/threshold_worker.py:147`. Each is a bare `EXPIRE`, but on a
  key that is either re-`SET` on the same path (so the TTL is re-armed by the write itself) or
  explicitly deleted on completion. None is an `INCR`-only counter. Out of this dossier's area
  regardless; recorded so the sweep is auditable.
- **CLEARED** every `pipe.expire(...)` site (`presence.py:98,117-119`, `redis_buckets.py:108`,
  `tokens.py:130,133,212,214`, `search_rate_limiter.py:27`, `run_engine.py:759`,
  `session_store.py:105`, `egress.py:95`, `a2a_rendezvous.py:88`, `pending_notify.py:39,85`,
  `lockouts.py:40,42`). These already use a pipeline and are the in-repo precedent the fix follows.
- **CLEARED** `wakeup_state.py:120` (`touch_silence_timestamp`) and `:63` (`claim_gated_notice`):
  both use `SET` with `ex=` in a single command, already atomic.

Whole-column JSONB replaces that could drop sibling keys (the F-12 pattern):

- **CONFIRMED, same site, same fix** `wakeup_service.py:394` (`refresh_wakeup_config`) also
  performs a whole-column replace, but from `wakeup_authored_snapshot`, which is the complete
  human dict (`agent_service.py:614-615`). It is the *recovery* path for F-12, not another
  instance of it. No change needed beyond Q-2's clock.
- **CLEARED** `agent_service.py:616-617`, `workflow_capabilities`, is also a whole-column replace,
  but nothing derives a partial dict from a lossy serializer before writing it; the API supplies
  the full value.

## 7. Fix Design

Five changes. Each corrects the earliest link named in §5 rather than the observable symptom.

**C1, F-3: delete the presence cache and seed the silence clock lazily**
(`backend/contexts/orchestration/application/wakeup_service.py`,
`backend/contexts/orchestration/infrastructure/wakeup_state.py`)

- Remove the flag gate at `wakeup_service.py:212`.
- At `wakeup_service.py:215-217`, when `get_silence_timestamp` returns `None`, call
  `touch_silence_timestamp` and return `False`. The clock starts on first evaluation instead of
  waiting for a presence edge that may never come again.
- Delete `set_silence_active` and `is_silence_active` (`wakeup_state.py:133-152`) and their
  `__all__` entries, delete the write at `wakeup_service.py:258`, and update the key list in the
  module docstring (`wakeup_state.py:10`).
- Keep `on_presence_changed`'s `touch_silence_timestamp` on join (`wakeup_service.py:259-260`).
  That is what preserves R15.05b's pause semantics: silence elapses while a room is empty, firing
  is blocked by the roster check, and a rejoin resets the clock so the agent does not fire the
  instant a user returns.

Why this corrects rather than masks: the requirement (R15.05b) and the implement doc
(`G-orchestration.md:81`) both name `ws:presence:{room_id}` as the signal. The flag was a cache of
that set which the surrounding code already distrusted (`wakeup_service.py:229-233`). Removing the
cache means there is no fill path left to forget. Lazily setting the flag, the rejected
alternative, would repair this instance while leaving every other way the cache can go stale in
place, and would still not fix the missing-timestamp arm.

Cost: no additional Redis reads in the steady state. The unconditional roster read stays exactly
where it is (`:234-237`); the flag `GET` is removed; one extra `SET` is issued per binding on its
first-ever evaluation. Verified contained: `is_silence_active` has exactly one non-test reader
(`wakeup_service.py:212`), and `PresenceTracker.list_room`
(`backend/contexts/conversation/infrastructure/presence.py:149-151`) is a single `SMEMBERS`.

No R15.05 regression: `allow_self_open` is read only on the `every_n_messages` path
(`wakeup_service.py:108`); the silence path never consulted it before or after.

**C2, F-21 / FU-5 and the confirmed parser siblings: make `from_dict` enforce every documented
bound** (`backend/contexts/orchestration/domain/models.py:150-171`)

Introduce a private clamp helper and apply it to every numeric field, per Q-1:

| Field | Invalid input resolves to | Upper bound | Source |
|---|---|---|---|
| `every_n_messages.n` | 1 (`N_MIN`) | 1000 (`N_MAX`) | R15.07 |
| `silence_minutes.t_minutes` | 1 (`T_MINUTES_MIN`) | 1440 (`T_MINUTES_MAX`) | R15.07 |
| `silence_minutes.autostop_rounds` | 5 (default) | 100 (`AUTOSTOP_HARD_CAP`) | R15.04 |
| `silence_minutes.observer_autostop_rounds` | 50 (default) | 100 (`AUTOSTOP_HARD_CAP`) | R28.12 |
| `silence_minutes.autostop_max_default` | 100 (default) | 100 | consistency only |
| `refresh_every_hours` | 24 (default) | leave unbounded above | R15.09 |

`N_MIN`, `N_MAX`, `T_MINUTES_MIN`, `T_MINUTES_MAX` already exist at `models.py:207-210` and are
imported by `wakeup_service.py:33-40`; C2 makes the parse layer and the self-modification clamp
agree on them instead of only the latter enforcing them.

Then delete the now-dead zero-fallback at `app/workers/tasks/orchestration.py:108-112`, so
`effective_limit` becomes `autostop_limit`. This is what makes the "single source of truth"
docstring at `models.py:118-120` true rather than aspirational, and it removes the divergence in
both directions and on both role arms at once (FU-5), because both arms flow through
`autostop_limit_for` and both are clamped at the same place.

Why this corrects rather than masks: the alternative, patching `wakeup_service.py:224-227` to
mirror the worker's fallback, would add a second compensating branch for an input the parse layer
should never have produced, and would have to be repeated for the observer arm and for every
future consumer. `models.py:95-96` already claims the parse layer is where caps are enforced
"regardless of how the JSONB was written"; C2 makes the code match the claim.

**C3, F-12: make `to_dict` lossless and the config write additive**
(`backend/contexts/orchestration/domain/models.py`,
`backend/contexts/orchestration/application/wakeup_service.py`)

- Add `soft_bounds: WakeupSoftBounds | None = None` to `WakeupConfig` (`models.py:136-140`),
  parse it in `from_dict` (reusing the shape `_parse_soft_bounds` already reads,
  `wakeup_service.py:446-456`), and emit it in `to_dict` (`:173-193`) when present.
  `_parse_soft_bounds` then becomes a thin read of `cfg.soft_bounds` or is deleted outright,
  removing the second parser for the same JSON.
- In `_build_new_dict` (`wakeup_service.py:329-337`), overlay rather than replace:
  start from `dict(base_agent.wakeup_config or {})`, merge the serialized config over it, then
  apply the clamped `n` / `t_minutes`. Per Q-8 this preserves any other free-form designer key,
  not only `soft_bounds`.

Why this corrects rather than masks: the symptom is "bounds vanish after one self-modification",
and the tempting patch is to re-inject `soft_bounds` in `update_wakeup`. That leaves
`to_dict()` still lying about being a serialization of the config, so the next caller that writes
`to_dict()` into the column reintroduces the bug. Fixing the round trip fixes it for every caller.

**Data repair position (explicit).** No backfill migration. Agents whose `soft_bounds` were
already erased are self-repairing: `refresh_wakeup_config` (`wakeup_service.py:377-428`) writes
`wakeup_authored_snapshot` back over `wakeup_config`, and that snapshot is the full human dict
including `soft_bounds` (`agent_service.py:614-615`). The next hourly sweep after deploy restores
them for every agent that has a snapshot, which is every agent created through
`agent_service.py:446` or human-edited since. The unrecoverable set is agents whose
`wakeup_authored_snapshot` is `NULL` (the column is nullable, `agents/infrastructure/tables.py:62`)
and whose bounds were erased before deploy; for those the original values do not exist anywhere and
no repair is possible. Ship a one-time operator query, not a migration, to size that set:
`SELECT id, project_id FROM agents WHERE deleted_at IS NULL AND wakeup_authored_snapshot IS NULL AND wakeup_config ? 'triggers'`.
Note the ordering constraint: C3 must land with C4, otherwise C4's longer refresh interval widens
the window in which erased bounds go unrepaired (see §6).

**C4, F-14: give the refresh a per-agent clock**
(`backend/contexts/orchestration/application/wakeup_service.py`,
`backend/app/workers/tasks/orchestration.py`, plus the storage chosen in Q-2)

- `refresh_wakeup_config` reads `cfg.refresh_every_hours` (clamped by C2) and returns `False`
  without writing when `now - last_refreshed_at < timedelta(hours=T)`.
- Record `last_refreshed_at` on every actual reset, next to the `agent.wakeup_refreshed` audit
  emit (`wakeup_service.py:416-427`).
- A `NULL` / missing clock means "never refreshed": refresh immediately, then record. This makes
  the first post-deploy sweep behave exactly as today, so the change is a no-op on the first tick
  and only lengthens intervals afterwards.
- The hourly cron (`app/workers/main.py:322`) and the candidate query
  (`agents/infrastructure/repositories.py:283-289`) are unchanged; the interval decision moves
  into the service, which is the layer that owns the R15.09 rule.

Why this corrects rather than masks: the symptom is "resets too often", and the shortcut is to
lengthen the cron. That would apply one interval to every agent, which is the same defect with a
different constant. The requirement is per-agent, so the state must be per-agent.

**C5, F-38 and Q-6: pipeline the counter writes**
(`backend/contexts/orchestration/infrastructure/wakeup_state.py`,
`backend/contexts/knowledge/application/graphrag_triggers.py`)

- `increment_message_count` (`:81-90`) and `increment_autostop` (`:160-169`) issue `INCR` and
  `EXPIRE` through one `pipeline(transaction=True)` and read the count from the results, following
  `presence.py:98,117-119`.
- Same change at `graphrag_triggers.py:63-69`.
- Delete the dead `reset_message_count` (`wakeup_state.py:93-97`) and its `__all__` entry (`:196`),
  which the finding identified as having no callers. Confirmed: the only repo-wide references are
  the definition and the export.

No data repair for F-38. Keys that already lost their TTL are indistinguishable from live keys.
They are 7-day-TTL counters in a Redis instance, they are re-armed by any subsequent increment,
and the operator remedy if any accumulate is `SCAN` plus `EXPIRE`, which does not warrant a
scripted migration.

**C6, FU-1 (contingent on Q-3)** (`frontend/src/shared/types/workflow.ts:69-82`)

Set `autostop_rounds: 5`, `every_n_messages.n: 3`, add `observer_autostop_rounds: 50` to the
`WakeupTriggerConfig` shape (`workflow.ts:50-59`) and to `DEFAULT_WAKEUP`. `t_minutes` pending the
user's answer. This also closes FU-4's data half: with `observer_autostop_rounds` in the client
shape, `normalizeNestedTriggers` (`:89-97`) stops silently omitting it. Adding an editor control
for it remains out of scope (FU-4 is a reachability gap, see §13).

## 8. Regression Test Plan

/build implements these tests before touching any fix. Every one fails against current code for
the stated reason.

**T-1 (the failing test, write this first)**
`backend/tests/unit/test_wakeup_service.py::test_silence_seeds_its_clock_for_a_binding_created_after_the_join_edge`

Build the service with `_make_service(agent=..., room_members=[uuid4()])`
(`test_wakeup_service.py:37-42`). Stub `get_silence_timestamp` to return `None` and record calls
to `touch_silence_timestamp`; stub `get_autostop_count` to 0. Assert: (a) the first
`evaluate_silence_trigger` returns `False`, (b) `touch_silence_timestamp` was called exactly once
with `(agent_id, room_id)`, and (c) after re-stubbing `get_silence_timestamp` to
`now - 10 minutes`, a second call returns `True`.

Fails today on (b) and (c): with no `silence_active` key the first call short-circuits at
`wakeup_service.py:212` and never reaches the timestamp read, so nothing is seeded and the second
call returns `False` as well. This is the exact F-3 scenario in §4.

**T-2** `backend/tests/unit/test_wakeup_service.py::test_normal_silence_follows_the_live_roster_not_the_cached_flag`

Replaces `test_normal_silence_still_respects_paused_flag` (`:119-130`) per Q-5. Roster
`[uuid4()]`, silence timestamp 10 minutes old, autostop 0, and no `silence_active` key at all.
Assert `True`. Fails today: `wakeup_service.py:212` returns `False` on the missing flag.

**T-3, guard against over-fixing** the existing
`test_allow_self_open_true_does_not_fire_into_empty_room` (`:62-71`) and
`test_allow_self_open_false_still_does_not_fire_into_empty_room` (`:84-92`) must keep passing with
their `is_silence_active` stubs removed. They pin that an empty roster still suppresses, which is
the only presence gate left after C1. Passes today and must pass after.

**T-4** `backend/tests/unit/test_wakeup_service.py::test_wakeup_config_clamps_every_numeric_field`

`WakeupConfig.from_dict({"triggers": {"every_n_messages": {"n": 0}, "silence_minutes": {"t_minutes": 0, "autostop_rounds": 0, "observer_autostop_rounds": 0}}, "refresh_every_hours": 0})`
must yield `n == 1`, `t_minutes == 1`, `autostop_rounds == 5`, `observer_autostop_rounds == 50`,
`refresh_every_hours == 24`. Negatives (`-3`) yield the same. Upper bounds still hold: `n=5000`
yields 1000, `t_minutes=99999` yields 1440. `to_dict()` round-trips all of them.

Fails today on every lower-bound assertion: `models.py:154,158` apply no bounds at all and
`:159,161-163,170` apply `min()` only. Extends the existing
`test_wakeup_config_parses_observer_autostop_rounds` (`:147-154`), which covers only the upper cap.

**T-5** `backend/tests/unit/test_agent_trigger_wiring.py::test_wakeup_agent_zero_autostop_uses_the_parsed_default`

Using the existing `_wire` helper (`:214`) and `_agent(autostop_rounds=0)` (`:304-309`) with
`autostop_count=5` and the default `NORMAL` role, assert `wakeup_agent(...) == "skipped:autostop"`.
Fails today: `orchestration.py:111-113` resolves 0 to `autostop_max_default` (100), so the turn
runs and the task returns the turn status.

**T-6, the observer arm (FU-5)**
`backend/tests/unit/test_agent_trigger_wiring.py::test_wakeup_agent_zero_observer_autostop_uses_the_observer_default`

Same helper with `observer_autostop_rounds: 0` in the config and `role=ChatroomAgentRole.OBSERVER`
(the pattern at `:395-430`), `autostop_count=50`: assert `"skipped:autostop"`. And its evaluator
counterpart in `test_wakeup_service.py`: with `observer_autostop_rounds: 0` and
`autostop_count=0`, `evaluate_silence_trigger(..., is_observer=True)` must return `True`, not
`False`. Fails today in both directions: the worker reads 100 and the evaluator reads 0. This is
the AC that a fix scoped to one arm cannot pass.

**T-7** `backend/tests/unit/test_wakeup_self_modification.py` (new file; no test currently
exercises `update_wakeup` at the service layer, only the tool wrapper at
`backend/tests/unit/test_agent_runtime_tools.py:38-62`)

Fake `AgentsFacade` capturing the `AgentDraft` handed to `patch_agent`. Agent config carries
`soft_bounds: {n_min: 5, n_max: 10}` plus an unrecognized key `"designer_note": "x"`. Call
`update_wakeup(every_n_messages=1)`. Assert the captured `draft.wakeup_config` still contains both
`soft_bounds` (unchanged) and `designer_note`, and that
`triggers.every_n_messages.n == 5`. Then feed the captured dict back as the agent's config and
call `update_wakeup(every_n_messages=1)` again: assert it clamps to 5 again, not 1.

Fails today on the first assertion: `_build_new_dict` (`wakeup_service.py:329-337`) returns
`to_dict()`, which emits only the three keys at `models.py:174-192`.

**T-8** `backend/tests/unit/test_wakeup_refresh_interval.py` (new file; nothing currently covers
`refresh_wakeup_config`)

Agent with `refresh_every_hours: 24`, a drifted `wakeup_config`, a non-null
`wakeup_authored_snapshot`, and a last-refresh clock at `now - 1h`: assert
`refresh_wakeup_config` returns `False` and `patch_agent` was never called. With the clock at
`now - 25h`: assert `True` and one `patch_agent` call. With the clock unset: assert `True`
(first-run behavior).

Fails today on the first case: `wakeup_service.py:377-428` never reads `refresh_every_hours` and
patches whenever `current != authored` (`:390-394`).

**T-9** `backend/tests/unit/test_wakeup_state_ttl.py` (new file; the audit's coverage note records
that no test touches `wakeup_state`)

Fake Redis recording command order and exposing a pipeline object. Assert
`increment_message_count` and `increment_autostop` each issue `INCR` and `EXPIRE` inside one
pipeline with a single `execute()`, that no bare `expire` is issued outside it, and that the
returned count is the `INCR` result. Same assertions for
`RedisGraphRagMessageCounter.increment` (Q-6). Also assert `reset_message_count` no longer exists
on the module.

Fails today: `wakeup_state.py:88-89`, `:167-168` and `graphrag_triggers.py:67-68` call `r.incr`
and `r.expire` directly on the client, so the fake records two separate commands and no pipeline.

**T-10, contingent on Q-3** `frontend/src/shared/types/__tests__/workflow.test.ts` (new file,
following the `__tests__/*.test.ts` convention used in `frontend/src/shared/composables/__tests__/`)

Assert `defaultWakeupConfig()` returns `autostop_rounds: 5`, `every_n_messages.n: 3`, and
`observer_autostop_rounds: 50`, with a comment naming `models.py:103,110,115` as the mirrored
source. Assert `normalizeNestedTriggers` on `{silence_minutes: {enabled: true}}` preserves
`observer_autostop_rounds`. Fails today: `workflow.ts:71,75` hold 5 and 3 transposed and the field
does not exist in the client shape.

## 9. Risks and Rollback

- **C1 changes observable trigger behavior.** Bindings that were dead now fire. In a deployment
  where users had worked around F-3 by leaving silence triggers enabled and assuming nothing
  happens, those agents will start waking, spending on the user's own key. This is the requirement
  being restored, not a regression, but it should be called out in the release note. The empty-room
  suppression (`wakeup_service.py:234-237`) is untouched, so no agent fires into an empty room.
- **C1 rewrites an existing test's premise** (Q-5). Reviewers seeing `test_normal_silence_still_respects_paused_flag`
  deleted must be pointed at Q-5's argument, not at the diff alone. Record it in §12.
- **C2 changes stored-config interpretation without changing stored data.** An agent persisting
  `autostop_rounds: 0` goes from "never wakes on silence, `every_n` capped at 100" to "wakes on
  silence, capped at 5". That is a behavior change for existing rows, in the restrictive direction
  for `every_n` and the permissive direction for silence. It is confined to configs that are
  invalid under R15.04. The count of affected agents should be measured before deploy:
  `SELECT count(*) FROM agents WHERE deleted_at IS NULL AND (wakeup_config #>> '{triggers,silence_minutes,autostop_rounds}')::int <= 0`.
- **C4 lengthens the self-modification window** from 1 h to the configured T (default 24 h). Any
  clamp defect in `update_wakeup` therefore persists 24x longer, which is precisely why C3 must
  ship with it (see §6). Do not land C4 without C3.
- **C4 with the Q-2 column option adds migration 0062.** Forward-compatible by construction:
  nullable, defaulted `NULL`, and old code ignores it. Rollback is a column drop or simply leaving
  it unused.
- **C5 is behavior-neutral** apart from durability. Low risk; the only failure mode is a
  mis-written pipeline returning results in an unexpected order, which T-9 pins.
- **C6 changes what the editor displays for existing agents** with omitted fields. No stored value
  changes until the user saves, at which point the persisted value matches what the backend would
  have inferred, which is the intended behavior.

**Rollback** each of C1 to C6 is independently revertable, with two ordering constraints: C3 must
not be reverted while C4 is live (bounds-escape window), and C2 must not be reverted while the
`orchestration.py` zero-fallback removal is live (that would restore the `0 >= 0` permanent
suppression with no compensating fallback anywhere). Revert C2 and the fallback removal together
or neither.

**Merge adjacency, not a dependency** `docs/tasks/2026-07-22-turn-idempotency-and-locking/`
edits the same `wakeup_agent` function region (`app/workers/tasks/orchestration.py:72-181`) and
its registration in `app/workers/main.py`. C2 deletes lines 108-112 inside that region. Expect a
textual conflict if both land in the same window; there is no logical coupling. This is why
`depends_on` is `[]`: nothing in that dossier must land first for this one to be correct, and
nothing in this one gates it.

## 10. Acceptance Criteria

- [ ] **AC-1** T-1 fails before the fix and passes after: a binding created after the room's
      presence edge seeds its silence clock on first evaluation and fires on the next sweep past T.
- [ ] **AC-2** T-2 passes: with a non-empty roster and no `silence_active` key, a normal binding
      fires. T-3 still passes: with an empty roster it does not, for both `allow_self_open` values.
- [ ] **AC-3** `set_silence_active` and `is_silence_active` no longer exist in
      `wakeup_state.py`, and a repo-wide grep for either name returns no hits outside git history.
- [ ] **AC-4** T-4 passes: `from_dict` clamps `n`, `t_minutes`, `autostop_rounds`,
      `observer_autostop_rounds`, `autostop_max_default` and `refresh_every_hours` on both sides,
      per the Q-1 table.
- [ ] **AC-5** T-5 passes and the zero-fallback at `app/workers/tasks/orchestration.py:108-112` is
      deleted, so `wakeup_service.py:224-227` and `orchestration.py:101-113` resolve the identical
      limit for identical input.
- [ ] **AC-6 (FU-5, both arms)** T-6 passes: `observer_autostop_rounds: 0` behaves identically to
      `autostop_rounds: 0` in both the worker gate and the domain evaluator. A fix that repairs
      only the non-observer arm fails this AC.
- [ ] **AC-7** T-7 passes: after a self-modification, the persisted `wakeup_config` still contains
      `soft_bounds` and any other designer-written key, and a second identical self-modification is
      clamped identically and emits a second `agent.wakeup_clamped` audit row.
- [ ] **AC-8** T-8 passes: `refresh_wakeup_config` is a no-op inside the configured window and
      resets outside it, with a never-refreshed agent refreshing immediately.
- [ ] **AC-9** T-9 passes: both `wakeup_state` counters and the GraphRAG message counter issue
      their `INCR` and `EXPIRE` in a single pipeline, and `reset_message_count` is gone.
- [ ] **AC-10 (contingent on Q-3)** T-10 passes: `DEFAULT_WAKEUP` mirrors the backend defaults for
      every field the comment at `workflow.ts:61-68` claims it mirrors, or that comment is narrowed
      to name the fields that deliberately differ.
- [ ] **AC-11** No data-repair migration is added. The operator query from §7 is recorded in the
      deviation log with its result, so the size of the unrecoverable `soft_bounds` set is known
      rather than assumed.
- [ ] **AC-12** Definition of Done: `pytest -q`, `ruff check . && ruff format --check .`,
      `mypy .` in `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` in
      `frontend/` if C6 lands. `mypy` strict applies to `contexts.orchestration.domain`, so C2 and
      C3 must type-check under strict mode.

## 11. SRS Delta

Two corrections, both small.

- **R15.04 and R28.12** document a default and a hard cap but no floor, which is what left
  `models.py:159` ambiguous enough to ship without one. Amend both to state the resolution rule
  explicitly, matching Q-1: "Values of 0 or below are invalid and resolve to the default (5 for
  `autostop_rounds`, 50 for `observer_autostop_rounds`); there is no 'unlimited' setting." Without
  this, the reading a designer takes ("0 means no autostop") remains a defensible reading of the SRS.
- **R15.09** says "Every T hours, the wake-up configuration is reset" without saying what T is
  measured from. Amend to name the origin: "measured from the last applied reset for that agent;
  an agent that has never been reset is eligible immediately." This is the rule C4 implements and
  it should not live only in code.

No change to R15.02, R15.05b, R15.06, R15.07 or R15.08. Those are correct as written; the code
diverged from them.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** `backend/contexts/orchestration/application/a2a_consumer.py:359-363` carries the F-38
  pattern (`HSET` then bare `EXPIRE`) on the retry key. Cleared from scope in §6 because all four
  terminal paths delete the key (`:301,316,328,341`), so TTL loss orphans a key only when the
  envelope is never revisited. Worth pipelining next time that file is opened.
- **FU-2** `observer_autostop_rounds` has no editor control (`SWakeupEditor.vue:106,286` exposes
  `autostop_rounds` only), recorded as FU-4 in the agent-to-user audit. C6 fixes the data half
  (the field stops being dropped by the client default shape); the control itself is a UI change
  and belongs to a frontend dossier.
- **FU-3** `wakeup_config` is free-form `BoundedConfig` at the API boundary
  (`app/api/v1/agents.py:89,123`). C2 makes the domain parser total, so no invalid value can reach
  a consumer, but the API still returns 200 for structurally nonsense configs and silently
  normalizes them. A typed Pydantic model for `wakeup_config` would reject at the boundary and
  tell the designer their `autostop_rounds: 0` was refused rather than rewritten. Larger change,
  affects the generated client, out of scope here.
- **FU-4** `evaluate_silence` (`app/workers/tasks/orchestration.py:232-244`) loads every live
  binding into memory in 500-row pages every 30 s and evaluates each with at least one agent read
  and one to three Redis round trips. Cleared as correct, flagged as fragile at scale. Not a
  defect today.
- **FU-5** The `agent.wakeup_clamped` audit (`wakeup_service.py:357-369`) fires only on the
  self-modification path. After C2, the parse layer will also clamp designer-written values, and
  it does so silently: a designer who writes `t_minutes: 0` gets 1 with no audit row and no API
  signal. Consider an audit or a response warning on parse-time clamping; deliberately excluded
  here because emitting audit from a frozen domain dataclass would break the layer boundary and
  the fix should not carry a design change.
</content>
