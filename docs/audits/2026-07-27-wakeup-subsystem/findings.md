---
type: audit
status: closed
created: 2026-07-27
requirements: [R15.01, R15.02, R15.03, R15.04, R15.05b, R15.06, R15.07, R15.08, R15.09, R28.04, R28.12]
---

# Audit: orchestration wake-up subsystem

## 1. Scope

- **Area** — the whole wake-up subsystem, not only the surface touched by
  `docs/tasks/2026-07-22-wakeup-trigger-state-and-bounds/spec.md`:
  - config lifecycle: `contexts/orchestration/domain/models.py`, `contexts/agents/application/agent_service.py`
    (`wakeup_config` / `wakeup_authored_snapshot` / `wakeup_last_refreshed_at` writes),
    `alembic/versions/0067_wakeup_last_refreshed_at.py`;
  - evaluation: `contexts/orchestration/application/wakeup_service.py`;
  - Redis state: `contexts/orchestration/infrastructure/wakeup_state.py`,
    `contexts/knowledge/application/graphrag_triggers.py` (the counter folded in by Q-6);
  - dispatch: `app/workers/tasks/orchestration.py` (`wakeup_agent`, `evaluate_silence`,
    `wakeup_refresh`), `app/api/v1/messages.py` wake-up dispatch,
    `contexts/conversation/application/triggers.py`, `app/api/ws/chatroom.py`,
    `app/workers/tasks/retention.py` presence scrub;
  - client: `frontend/src/shared/types/workflow.ts`, `SWakeupEditor.vue`, and the three views
    that persist a wake-up config (`AgentDetailView.vue`, `AgentOrchestrationView.vue`,
    `ChatroomSettingsView.vue` via `useChatroomBindings.ts`).
- **Intent sources** — `REQUIREMENTS.md` R15.01–R15.09, R28.04, R28.12;
  `docs/implement/G-orchestration.md`; `docs/implement/N-conversation-a2a-fixes.md`; and the
  approved dossier above (its §7 Fix Design and §10 acceptance criteria served as the primary
  intent source for the verification half of this audit).
- **Depth** — thorough. Two passes: (a) a per-AC verification pass against the dossier's
  twelve acceptance criteria, (b) a defect sweep over the same area with the state/lifecycle,
  boundary-input, error-path, cross-layer-consistency and dead-code lenses. Every candidate
  was re-read against the code with the explicit goal of refuting it; §4 records the ones that
  did not survive.

### Verification result for the approved dossier

All twelve acceptance criteria are implemented and covered by passing tests. Evidence:

| AC | Verdict | Evidence |
|---|---|---|
| AC-1 | met | `wakeup_service.py:210-213` seeds the clock when `get_silence_timestamp` is `None`; `tests/unit/test_wakeup_service.py:103-131` |
| AC-2 | met | `wakeup_service.py:227-230` roster-only gate; `test_wakeup_service.py:134-139`, `:52-81` |
| AC-3 | met | repo-wide grep for `set_silence_active` / `is_silence_active` hits only `docs/` history; `wakeup_state.py:158-166` `__all__` is clean |
| AC-4 | met | `models.py:117-124` (`_clamp` / `_default_below_one`) applied at `:193,197,202,207,212,223`; `test_wakeup_service.py:160-200` |
| AC-5 | met | zero-fallback gone; `orchestration.py:102-113` uses `autostop_limit_for` directly |
| AC-6 | met | both arms clamp through `_default_below_one` at `models.py:202-216`; observer evaluator test at `test_wakeup_service.py:142+` |
| AC-7 | met | `models.py:168,178-188,251-261` round-trips `soft_bounds`; `wakeup_service.py:322-330` overlays via `_overlay_config` (`:475-483`); `tests/unit/test_wakeup_self_modification.py:17,59` |
| AC-8 | met | `wakeup_service.py:379-386,450-460`; `tests/unit/test_wakeup_refresh_interval.py` (4 tests, incl. the D-2 overflow case) |
| AC-9 | met | `wakeup_state.py:83-87,135-139` and `graphrag_triggers.py:66-71` pipeline; `reset_message_count` absent; `tests/unit/test_wakeup_state_ttl.py` |
| AC-10 | met | `workflow.ts:68-82` defaults corrected, `observer_autostop_rounds: 50` present in shape (`:54`) and defaults (`:76`); comment narrowed at `:62-67`; `frontend/src/shared/types/__tests__/workflow.test.ts` |
| AC-11 | met | no data-repair migration; D-1 records the operator query and its result |
| AC-12 | met | 48 wake-up unit tests pass locally (`pytest tests/unit/test_wakeup_*.py tests/unit/test_agent_trigger_wiring.py tests/unit/test_message_wakeup_dispatch.py -q`) |

Migration `0067_wakeup_last_refreshed_at.py` is the current head and is nullable-additive as
designed. `wakeup_last_refreshed_at` is not reachable from the API — `app/api/v1/agents.py:219`
and `:317` build `AgentDraft` field by field and never set it — so a client cannot freeze its
own refresh clock.

The findings below are residual defects the dossier did not address, not failures of it.

## 2. Coverage

Read in full: `wakeup_service.py`, `wakeup_state.py`, `orchestration/domain/models.py`,
`orchestration/tasks` wake-up functions, `conversation/application/triggers.py`,
`workflow.ts`, and the five wake-up unit-test files. Read in part (targeted regions only):
`agent_service.py` (create/patch paths, `:560-790`), `app/api/ws/chatroom.py` (presence
notify), `retention.py` (presence scrub), `messages.py` (wake-up dispatch),
`AgentDetailView.vue`, `AgentOrchestrationView.vue`, `useChatroomBindings.ts`,
`graphrag_triggers.py` (counter only).

Not covered by this audit:

- `SWakeupEditor.vue` beyond its clamp expressions — its rendering, i18n and a11y were not
  reviewed. The absence of an `observer_autostop_rounds` control is a known follow-up (the
  dossier's FU-2), not re-reported here.
- The GraphRAG silence trigger (`RedisGraphRagSilenceClock` and its sweep) beyond the counter
  that Q-6 pulled into scope. It is a separate trigger family with its own dossier.
- `TurnEngine.run_turn` — everything downstream of a fired wake-up. This audit stops at the
  enqueue.
- Integration and wiring tiers were not executed; only the unit tier was run. Redis
  pipeline semantics were verified by reading plus the fake-Redis unit test, not against a
  live server.
- No load or concurrency testing. Race claims below are derived from code reading.

## 3. Findings

## F-1: every UI save path silently strips `soft_bounds`, and the same write destroys the authored snapshot that was the documented recovery path

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/shared/types/workflow.ts:133-149` (`normalizeWakeupConfig`
  returns a fresh object literal carrying only `triggers`, `allow_self_open`,
  `refresh_every_hours`; the `WakeupConfig` interface at `:47-60` has no `soft_bounds` member
  and no index signature at the root), `frontend/src/slices/agents/views/AgentDetailView.vue:388`
  and `:422,431` (the normalized object is cloned straight into the PATCH payload),
  `frontend/src/slices/workflow/views/AgentOrchestrationView.vue:37,46`,
  `frontend/src/slices/conversation/composables/useChatroomBindings.ts:55,163`,
  `backend/contexts/agents/application/agent_service.py:755-761` (a non-system actor's
  `wakeup_config` write also replaces `wakeup_authored_snapshot`).
- **Failure scenario**: a Platform Admin creates agent A with
  `wakeup_config.soft_bounds = {n_min: 5, n_max: 10}` through the API (the only way to set it —
  no editor control exists). Later any project member opens A in the agent detail page,
  changes an unrelated field such as the system prompt, and saves. The payload's
  `wakeup_config` is the normalized object, which has no `soft_bounds`, so `agent_service.py:756`
  writes a config without it and `:761` writes the *same* dict into
  `wakeup_authored_snapshot`. A's designer bounds now exist nowhere. A's next
  `update_wakeup(every_n_messages=1)` lands at `N_MIN = 1` with no clamp audit, and the hourly
  refresh restores a snapshot that no longer contains the bounds, so the loss is permanent.
  The same happens from the chatroom settings editor (`useChatroomBindings.ts:163`) and the
  agent-orchestration page (`AgentOrchestrationView.vue:46`).
- **Blast radius**: every agent with designer soft bounds, once any human edits it through any
  of the three UI surfaces. Silent — no error, no audit distinguishing it from a normal
  `agent.edited`. This is the F-12 defect class the dossier closed on the backend, still open
  on the client, and on the one path that also destroys the recovery mechanism the dossier's
  §7 "Data repair position" explicitly relied on ("agents whose `soft_bounds` were already
  erased are self-repairing" — that argument holds only for the system-actor path).
  Root-level keys other than `soft_bounds` are dropped by the same mechanism, which is the
  hole the dossier's Q-8 closed server-side by overlay.
- **Intent source**: R15.08 ("Platform Admin can also set soft per-agent bounds at creation
  time; self-modification must respect these"), `docs/implement/G-orchestration.md:98`, and the
  dossier's Q-8 decision that a `wakeup_config` write must be additive rather than replacing.

## F-2: a non-integer numeric field in one agent's `wakeup_config` silently kills `every_n_messages` for every agent in the room

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/orchestration/domain/models.py:193,197-201,203,208,213,224`
  (every numeric field is coerced with a bare `int(...)`; the clamp helpers at `:117-124`
  receive an already-converted value and cannot defend against the conversion itself),
  `backend/contexts/orchestration/application/wakeup_service.py:88-116` (`from_dict` at `:93`
  runs inside the per-agent loop, so an exception aborts the whole loop),
  `backend/app/api/v1/messages.py:266-271` (the dispatch is wrapped in a best-effort
  `try/except` that swallows it), `backend/app/api/v1/agents.py:89,123` (`wakeup_config` is
  free-form `BoundedConfig`, so the value is accepted with 200).
- **Failure scenario**: an API client PATCHes agent A with
  `{"wakeup_config": {"triggers": {"every_n_messages": {"enabled": true, "n": null}}}}` —
  a shape any serializer that emits explicit nulls for absent optionals produces. The write
  returns 200. Agents A, B and C are bound to room R. On the next user message,
  `on_message_created` iterates the bindings; when it reaches A, `int(None)` raises `TypeError`,
  the loop aborts, and `messages.py:268` swallows it. No agent in R is woken — including B and
  C, whose configs are valid, and including any agent already appended to `wake_list` before A
  was reached. Every subsequent message in R behaves the same way. `"n": "many"` raises
  `ValueError` identically. The room's `every_n_messages` trigger is dead with no error surfaced
  to anyone.
- **Blast radius**: all `every_n_messages` wake-ups in any room containing one misconfigured
  binding, indefinitely, and silently. Iteration order decides which of the healthy agents are
  affected, but the swallowed exception discards the whole `wake_list`, so in practice all of
  them are. `evaluate_silence` is *not* affected — `orchestration.py:285-291` catches per
  binding — so silence triggers keep working, which makes the failure harder to spot.
  `wakeup_agent` (`orchestration.py:96`) raises the same way and fails the arq job.
  Reachability requires a write that bypasses the editor; `normalizeWakeupConfig` never emits
  nulls, so the UI cannot produce it.
- **Intent source**: `models.py:108-109` ("Hard caps applied at parse time so they're enforced
  regardless of how the JSONB was written (designer UI, direct DB edit, migration, etc.)") —
  the dossier's C2 made the parse layer total against out-of-range values but not against
  wrong-typed ones. R15.01 and R15.07.

## F-3: one failed agent in the hourly refresh sweep can discard every other refresh in the same sweep

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: `backend/app/workers/tasks/orchestration.py:309-319` (the per-agent
  `except Exception` logs but never rolls back, and the single `await db.commit()` sits after
  the loop), versus `backend/app/workers/tasks/orchestration.py:285-291`, where the silence
  sweep explicitly rolls back per pair with the comment "clear any aborted transaction so
  subsequent reads on this session succeed".
- **Failure scenario**: the sweep refreshes 400 agents, each producing an uncommitted
  `agents` UPDATE plus an `agent.wakeup_refreshed` audit row. Agent 401 hits a database-level
  error (serialization failure, constraint violation, connection blip). `:317` logs
  "wakeup refresh failed" for that one agent and continues; the session is now in a failed
  transaction, so agents 402+ also raise and log, and `:319`'s `commit()` raises out of the
  task. All 400 successful refreshes are rolled back, while the log reports only per-agent
  failures. On the next hourly tick the same agents are refreshed again, so the end state
  self-heals — the loss is one sweep's work plus a misleading log, not permanent drift.
- **Blast radius**: one sweep of R15.09 refreshes across the whole deployment. Marked
  *plausible* rather than confirmed because `AgentVersionMismatch` (the expected failure on
  this path) is raised by the repository without poisoning the session, so reproducing the
  scenario requires a genuine DB-level error, which was not traced end to end.
- **Intent source**: R15.09, and the in-repo precedent set for the same sweep shape at
  `orchestration.py:285-291`.

## F-4: the retention presence scrub still drives a hook that has become a no-op, and its comment claims a protection it no longer provides

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/app/workers/tasks/retention.py:715-728` (the docstring states the call
  exists so "a self-opening silence agent doesn't fire into a room whose last member dropped
  uncleanly", and `:728` is the only caller that passes `has_live_users=False`),
  `backend/contexts/orchestration/application/wakeup_service.py:239-252` (`on_presence_changed`
  now only acts when `has_live_users` is true, so the false branch does nothing),
  `backend/contexts/conversation/application/triggers.py:120-138` (the hook still lists every
  binding in the room first).
- **Failure scenario**: not a behavior defect — the protection the comment describes is now
  delivered by the unconditional roster read at `wakeup_service.py:227-230`, which the C1 fix
  made the authoritative signal. The defect is that each emptied room still pays a
  `ChatroomAgentRepository.list` query and a facade round trip per scrub to reach a loop body
  that cannot do anything, and the comment tells the next reader that removing it would
  reintroduce a bug it no longer prevents.
- **Blast radius**: wasted queries proportional to rooms emptied by the scrub, plus a stale
  intent record that will mislead the next change to this path. No user-visible symptom.
- **Intent source**: R15.05b and the dossier's C1 rationale ("the requirement and the implement
  doc both name `ws:presence:{room_id}` as the signal").

## 4. Refuted Candidates

- *"C1 removed the pause half of R15.05b — nothing stops the silence clock while a room is
  empty."* Refuted. Elapsed silence is never consumed while the room is empty because the
  roster gate at `wakeup_service.py:227-230` blocks firing, and a rejoin resets the clock via
  `on_presence_changed` → `touch_silence_timestamp` (`:250-252`), so the agent does not fire the
  instant a user returns. `test_wakeup_service.py:52-81` pins the suppression for both
  `allow_self_open` values.
- *"The lazy seed at `wakeup_service.py:210-213` writes a Redis key on every sweep for an
  idle binding."* Refuted. The seed runs only when `get_silence_timestamp` returns `None`;
  after the first evaluation the key exists for 7 days and is re-armed by the post-fire
  debounce (`orchestration.py:283`) and by every message (`wakeup_service.py:97`).
- *"`refresh_every_hours` is left unbounded above, so an extreme value overflows the interval
  arithmetic."* Refuted — this was found and fixed during the build. `_inside_refresh_window`
  (`wakeup_service.py:450-460`) compares elapsed seconds against `interval_hours * 3600` instead
  of constructing a `timedelta`, and `test_wakeup_refresh_interval.py:59-67` pins it with a
  24,000,000,000-hour config. Recorded as D-2 in the dossier.
- *"`refresh_wakeup_config` records the clock even when it decides not to reset, so an agent
  that never drifts eventually stops being eligible."* Refuted. The clock is written only on the
  `AgentDraft` that carries the actual reset (`wakeup_service.py:396-399,427-430`); the
  no-drift path returns at `:394` without touching it.
- *"`observer_autostop_rounds` is dropped on save because the editor has no control for it."*
  Refuted. `normalizeNestedTriggers` (`workflow.ts:89-97`) spreads the stored trigger object
  over the defaults, so a stored value survives a save; only a *missing* value is filled, and
  C6 made that fill match the backend default of 50.
- *"A human PATCH that omits `wakeup_config` clears the authored snapshot."* Refuted.
  `agent_service.py:755` guards the whole block on `draft.wakeup_config is not None`, and the
  API builds the draft from present fields only.

## 5. Hand-off

Triaged 2026-07-27: all four findings selected for fixing, grouped into three dossiers. F-3 and F-4
share a dossier because both are minor worker-sweep hygiene defects on one surface and one review;
F-1 and F-2 are separate because they share neither a mechanism nor a rollback.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | fix | `docs/tasks/2026-07-27-wakeup-config-key-preservation/` |
| F-2 | fix | `docs/tasks/2026-07-27-wakeup-config-type-validation/` |
| F-3 | fix | `docs/tasks/2026-07-27-wakeup-sweep-failure-isolation/` |
| F-4 | fix | `docs/tasks/2026-07-27-wakeup-sweep-failure-isolation/` |

**Side effect of this triage, recorded here because it is not a finding.** The dependency scan run
while writing the F-3/F-4 dossier found that `docs/tasks/2026-07-22-presence-transition-and-release-wakeup/`
(draft) had gone stale against the landed `2026-07-22-wakeup-trigger-state-and-bounds`: its Part A2
paused a presence flag that no longer exists, and several of its citations pointed at deleted code.
It was re-baselined on 2026-07-27 rather than left for `/build` to discover — A2 withdrawn, A1
retained and re-argued as now load-bearing, T-4/T-5 and AC-5 marked withdrawn rather than deleted.
See that dossier's Q-6.

## 6. Out-of-scope Observations

- **FU-1 (route to `check-quality`)** — `WakeupService` now owns evaluation, self-modification,
  refresh scheduling and the notification side effect, and imports the Redis adapter module
  directly (`wakeup_service.py:41`). The dossier already records the port/adapter half as its
  own FU-8; the size of the class is the separate half.
- **FU-2 (route to `check-quality`)** — the frontend `WakeupConfig` type is a closed shape used
  as both the editor's view model and the PATCH payload. F-1 is a direct consequence of using
  one type for both roles; the structural fix (a passthrough for unmodelled keys, or separate
  read/write types) is a design change, not a bugfix.
- **FU-3 (already recorded in the task dossier, not re-opened here)** — the dossier's own FU-3
  (typed Pydantic model for `wakeup_config` at the API boundary), FU-5 (no audit on parse-time
  clamping), FU-6 (`normalizeWakeupConfig` accepts non-object input), FU-7 (`soft_bounds` values
  are unvalidated) and FU-10 (unbounded refresh candidate query) all remain open and were
  re-confirmed as still present. F-2 overlaps FU-3's area but is a distinct defect: FU-3
  describes silent *normalization*, F-2 is an unhandled *exception*.
