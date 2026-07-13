---
type: feature
status: approved
created: 2026-07-13
requirements: [R14.01, R14.03, R30.01, R30.05, R30.10]
depends_on: [2026-07-13-activities-platform-core]
---

# Activities Reactive Rules — `activity_event` workflow trigger

## 1. Summary

Let the existing `workflow` engine react to structured activity submissions. On each
submission/validation, the `activities` core emits `workflow_signal("activity", payload)`
post-commit; the payload carries a lightweight **rolling aggregate** (e.g. same-error count
in the last N s, latency) so stateless SEL rules can gate on it. A new `activity_event`
trigger type wakes a dormant workflow, and an optional `activity_in_room` wait kind resumes a
parked node. This is the deterministic automation layer that turns "student is stuck" into an
action (wake an agent, notify the teacher) — **not** via the observer (whose output is
severed from automation by design, `turn_engine.py:1087-1117`), but via a config-authored
SEL rule. MVP for the NSTC undergraduate project keeps rules off (teacher-in-the-loop
release); the professor's auto-loop turns rules on. All creativity/impasse logic is a
project-authored SEL rule + workflow, not platform code.

## 2. Goals and Non-goals

**Goals**
- `activities` core emits `enqueue("workflow_signal", "activity", payload)` post-commit on
  submission and on validation completion, best-effort (mirrors the message-signal path,
  `messages.py:379-380`).
- The payload carries a precomputed rolling aggregate so SEL can compare a number
  (`trigger.rolling.same_error_count >= 3`) without itself aggregating.
- New `TriggerType.ACTIVITY_EVENT` + a `matches_activity` matcher + a new `"activity"`
  dispatch branch in `workflow_signal`.
- New `WaitEventType.ACTIVITY_IN_ROOM` so a parked `wait_for_event` node can resume on an
  activity (executor needs no change — it is event-kind-agnostic).
- Schema + frontend `TriggerType` union updated so the trigger is author-selectable.

**Non-goals**
- Any creativity/impasse rule content — that is a project-authored SEL expression + workflow.
- The rolling-aggregate *storage* being a durable analytics store — it is a bounded,
  best-effort signal computed at emit time (Redis window or a cheap query), not a source of
  truth. The `ActivitySubmission` remains authoritative.
- Edge-guard SEL evaluation — edge guards are **not** evaluated at runtime (verified, §4);
  rules must branch via a `condition` node. Out of scope to fix here.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where is the rolling aggregate computed | At emit time in `activities`, written into the signal payload as numbers | Keeps SEL stateless; SEL has no list-aggregate function and `_safe_cmp` only compares `int/float` (`evaluator.py:449-460`) |
| Q-2 | Trigger-only, or also a wait kind | Both: `activity_event` trigger (start dormant workflow) + `activity_in_room` wait (resume parked node) | The a2a precedent ships both (`workflow_signals.py:165-180`); parity avoids a follow-up |
| Q-3 | How does a count threshold like "3 in 60s" gate | Precompute the count into `payload.rolling`, gate with a `condition` node SEL expr | Edge guards don't run (§4); the matcher can only see one event's class/room |

## 4. Current State (verified)

- **Trigger enum**: `TriggerType` (`contexts/workflow/domain/models.py:35-41`) = `manual`,
  `cron`, `message_received`, `a2a_event`, `wakeup_signal`, `dry_run`; exported at `:240`.
  Stored as a plain `str` on `WorkflowRun.trigger_type` (`models.py:101`) — no run migration.
- **Signal fan-out**: `workflow_signal(ctx, source, payload)`
  (`app/workers/tasks/workflow_signals.py:110`) branches on `source ∈ {message, a2a, wakeup}`
  (`:143-190`); an unknown source silently no-ops (`:192`). `_enqueue_triggers` (`:136-141`)
  → `run_triggered_workflow` (`:288-310`); `_enqueue_resume` (`:131-134`) →
  `workflow_event_resume` (`:225`). Emit sites: user send `messages.py:379-380`, agent send
  `turn_engine.py:1244-1245`, a2a `a2a_handler.py:215-216`, wakeup `orchestration.py:169`.
- **Matchers** (pure, `contexts/workflow/application/event_dispatch.py`): `matches_message`
  (`:61-67`), `matches_a2a_trigger` (`:78-83`), `find_triggered_workflows` (`:164-191`, filters
  `node.type=="trigger"` + `config.trigger_type==...`), `find_matching_waits` (`:105-135`, reads
  Redis `wf:wait:by_event:{event_type}`). `__all__` at `:194-202`.
- **SEL**: `condition` executor builds scope with `__trigger__=ctx.trigger_payload`
  (`executors/condition.py:30-38`); SEL surface syntax is `trigger.*` / `ctx.*`
  (`sel/evaluator.py:79-84`); dotted-path resolve `:89-101`; numeric `>=` via `_safe_cmp`
  requires **both** operands numeric (`:449-460`). No sum/count-over-array function. Trigger
  payload rehydrated into `RunContext.trigger_payload` on every step (`run_engine.py:181, 270,
  311, 359`; field `models.py:183`).
- **Critical**: `EdgeSpec.guard` is defined (`models.py:155`) and documented
  (`workflow.schema.json:170`) but **never evaluated** — `_follow_edges` filters edges only by
  `from_port` (`run_engine.py:701-729`). Branching must use a `condition` node.
- **Wait kinds**: `WaitEventType` (`models.py:74-78`) = `message_in_room`, `a2a_message`,
  `timer`, `variable_matches`; schema `workflow.schema.json:330-365`. `wait_for_event` executor
  (`executors/wait_for_event.py:39-101`) is **event-kind-agnostic** (stores `dict(config)` as
  the match, indexes by `event_type`) — a new kind needs no executor change.
- **Schema + frontend**: trigger enum `docs/workflow.schema.json:183` + per-type `allOf`
  config blocks `:185-231`; wait enum `:334` + blocks `:337-365`; frontend union
  `frontend/src/slices/workflow/types/index.ts:20-25` (node config is untyped, `:50`).
  Linter known keys `linter.py:64`.

## 5. Design

**Two emit points, different content (critical timing).** The core emits on both submit and
validation completion, but the payloads differ and error-based rules must key off the right
one:
- **Submit emit** — carries `validation_status`, `session_id`, `attempt_no`, but for an
  `mcp`/`webhook` type `is_valid`/`error_class` are still unknown (`pending`). Useful only for
  volume/latency rules (e.g. "N attempts in M s").
- **Validation-completion emit** (`record_validation`) — carries the final
  `is_valid`/`error_class`. **This is the emit an impasse ("3 same-type errors") rule reacts
  to**, because `error_class` only exists here. For `in_process` (synchronous) types the two
  emits coincide at submit time (error_class is known immediately), so the same rule works
  whether the validator is sync or async — the rule author does not special-case it.

**Rolling aggregate.** Computed on the **completion** emit (where `error_class` exists):
`rolling = {same_error_count, window_seconds, latency_ms}` where `same_error_count` =
submissions in the last `window_seconds` in this session with the same non-null `error_class`.
Bounded indexed query (core indexes `(session_id)`, `(chatroom_id, created_at)`); best-effort,
failure degrades to `rolling: {}`, never blocks. Numbers only (SEL `_safe_cmp` requires both
operands numeric). On the submit emit for an async type, `rolling` is omitted/`{}` (no
`error_class` yet).

**Payload shape** (`workflow_signal("activity", payload)`):
```
{ chatroom_id, activity_type_key, session_id, subject_user_id, attempt_no,
  validation_status, is_valid, error_class,
  rolling: { same_error_count, window_seconds, latency_ms } }   # populated on completion emit
```
The matchable label is `activity_type_key` — the `ActivityType.key` the core already models.
(There is **no** separate `activity_class` field; if a project wants a coarser grouping it puts
a tag in `validator_config` and surfaces it into the payload for an SEL condition to read — the
*matcher* stays on `activity_type_key`, which exists.)

**Dispatch.** Add `elif source == "activity"` in `workflow_signal` (after `:182`): build a
trigger predicate and a wait predicate closing over `chatroom_id`/`activity_type_key`, call
`find_triggered_workflows(db, "activity_event", pred)` + `_enqueue_triggers(...)` and
`find_matching_waits(redis, "activity_in_room", pred)` + `_enqueue_resume(...)`.

**Matcher.** `matches_activity(match, *, chatroom_id, activity_type_key)` in `event_dispatch.py`
(shape of `matches_a2a_trigger`): exact `chatroom_id` + optional `activity_type_key`/allowed-list
filter; add to `__all__`. The count threshold is **not** in the matcher — it is an SEL
`condition` node expr `int(trigger.rolling.same_error_count) >= 3` in the authored workflow.

## 6. Detailed Changes

- **`activities` core** (`SubmissionService`): call `enqueue("workflow_signal", "activity",
  payload)` best-effort at both emit points (§5) — submit (volume/latency only) and
  `record_validation` (with `error_class` + `rolling`, the impasse-relevant one). For
  `in_process` types the two coincide at submit. (This is the FU-1 the core spec left unwired.)
- **`workflow/domain/models.py`**: `TriggerType.ACTIVITY_EVENT = "activity_event"` (`:41`);
  `WaitEventType.ACTIVITY_IN_ROOM = "activity_in_room"` (`:78`).
- **`workflow/application/event_dispatch.py`**: `matches_activity(...)` + `__all__` entry.
- **`app/workers/tasks/workflow_signals.py`**: new `"activity"` branch (after `:182`).
- **`docs/workflow.schema.json`**: add `activity_event` to trigger enum (`:183`) + a config
  `allOf` block (`chatroom_id`, optional `activity_type_key`/allowed-list); add
  `activity_in_room` to wait enum (`:334`) + config block (`chatroom_id`, optional
  `activity_type_key`).
- **`frontend/src/slices/workflow/types/index.ts`**: extend `TriggerType` union (`:20-25`).
- **`linter.py`**: no new special-case needed (activity_event has no cron-like handling);
  confirm the one-trigger-per-workflow rule still holds.

## 7. NFR Checklist

- [x] i18n — frontend adds a trigger label in `slices/workflow/locales/*`.
- [x] Audit — the signal itself is not audited (transient); the resulting workflow run is
  audited by the existing workflow machinery.
- [x] Tenant isolation — the matcher filters by `chatroom_id`; a workflow only fires for its
  own room's activities (workflows are project-scoped).
- [x] Error handling — unknown source no-ops; emit is best-effort (never blocks a submission).
- [x] Performance — rolling aggregate is one bounded indexed query; fan-out reuses the
  existing O(workflows) trigger scan (`event_dispatch.py:177`).

## 8. Security Considerations

- **No new outbound surface.** Automation runs inside the existing workflow engine and its
  existing capability gating; a reactive rule cannot do anything a manually-triggered workflow
  cannot.
- **Signal payload is server-computed.** `is_valid`/`error_class`/`rolling` come from the
  authoritative `ActivitySubmission` (core), never from the client — a student cannot forge a
  signal that trips a rule.
- **SEL is sandboxed.** Rules evaluate through the existing whitelisted SEL evaluator
  (`evaluator.py:42-61`); no new evaluation surface.

## 9. Quality Notes

- **Existing debt (do not imitate):** `EdgeSpec.guard` is dead config (defined, documented,
  never evaluated — `run_engine.py:701-729`). Do not build rules on edge guards; use a
  `condition` node. Recorded here so the implementer and rule authors know.
- **Patterns to follow:** the a2a source end-to-end (`workflow_signals.py:165-180` +
  `matches_a2a_trigger` + `a2a_handler.py:210-216`) is the exact template.
- **Reuse inventory:** `find_triggered_workflows` / `find_matching_waits` unchanged;
  `_enqueue_triggers` / `_enqueue_resume`; `enqueue` (`shared_kernel/queue.py:21`); the SEL
  `condition` node.

## 10. Risks and Rollback

- **Rolling aggregate accuracy** under bursty submissions — bounded and best-effort; a rule
  author sets a conservative window. Not authoritative.
- **Rule misfire / storm** — a badly authored rule could wake an agent repeatedly; mitigated
  by the workflow engine's existing run budgets/watchdog (`workflow_watchdog.py`) and a
  frequency-cap SEL expr the author writes.
- **Rollback**: removing the enum value + branch disables the feature; existing workflows
  without `activity_event` triggers are unaffected. Schema is additive.

## 11. Acceptance Criteria

- [ ] AC-1: Validation completion emits `workflow_signal("activity", payload)` post-commit with
  a numeric `rolling.same_error_count` grouped by `error_class`; the submit-time emit for an
  async type carries `validation_status=pending` and no `error_class`/`rolling`. Emit failure
  does not fail the submission or the validation write-back.
- [ ] AC-2: A dormant workflow with an `activity_event` trigger matching the room/`activity_type_key`
  is enqueued via `run_triggered_workflow`; a non-matching room/key is not.
- [ ] AC-3: A `condition` node with `int(trigger.rolling.same_error_count) >= 3` routes to the
  "impasse" port when the count is 3 and to `default` when it is 2 (SEL numeric compare).
- [ ] AC-4: A parked `wait_for_event` node of kind `activity_in_room` resumes when a matching
  activity signal arrives; a non-matching one leaves it parked.
- [ ] AC-5: `activity_event` appears in `workflow.schema.json`, the frontend `TriggerType`
  union, and passes the linter's one-trigger-per-workflow rule.
- [ ] AC-6: A signal for tenant A's room never triggers tenant B's workflow.

## 12. Test Plan

- Unit (`test_event_dispatch.py` additions): `matches_activity` accept/reject; SEL
  `trigger.rolling.same_error_count >= 3` truthy/falsy incl. string-count coercion (AC-3).
- Unit (`test_workflow_signals.py`): `"activity"` branch fans out to triggers + waits (AC-2,4).
- Unit (`activities` core): rolling-aggregate query shape + numeric types + best-effort emit
  (AC-1).
- Integration: end-to-end submit → rule fires → agent wake (AC-2); tenant isolation (AC-6).
- Manual: author a demo "3 errors in 60s → post a hint" workflow and drive it with submissions.

## 13. SRS Delta

Append to chapter **§30** (after `[R30.11]`), continuing the numbering:

```
- **[R30.12]** The activities context emits a best-effort `activity` workflow signal at submission and at validation completion; the completion signal carries the final outcome (`is_valid`/`error_class`) and a server-computed rolling aggregate (numeric counts/latency over a bounded recent window keyed by error class). Emission never blocks or fails the submission or validation, and all signal fields derive from the authoritative record, not the client.
- **[R30.13]** The workflow engine supports an `activity_event` trigger type and an `activity_in_room` wait kind. Rules that gate on an aggregate threshold must branch via a condition node evaluating an SEL expression over the trigger payload (edge guards are not evaluated at runtime).
- **[R30.14]** Reactive automation for activities runs through the workflow engine, not the observer. Impasse detection and any pedagogical rule are project-authored SEL expressions and workflows; the platform ships no domain rule.
```

## 14. Open Questions

None blocking.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

None. (The `EdgeSpec.guard` dead-config cleanup is a pre-existing workflow issue, out of this
dossier's scope; noted in §9 for awareness, not owned here.)
