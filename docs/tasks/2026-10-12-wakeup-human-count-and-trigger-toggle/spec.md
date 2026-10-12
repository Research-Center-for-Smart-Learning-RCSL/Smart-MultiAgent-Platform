---
type: feature
status: draft
created: 2026-10-12
requirements: [R15.01, R15.05a, R15.06, R15.07, R15.09]
depends_on: []
---

# Human-only message counting and designer-allowed trigger toggles

## 1. Summary

An agent designer who wants "wake me every three student messages" cannot express it: the
`every_n_messages` counter counts every room message, agents' included ([R15.01]), and a firing
that lands on the agent's own message is lost. A facilitator agent that should go quiet in one
phase of a lesson and listen for silence in another cannot do it either: `update_wakeup` may
change only the two numeric fields ([R15.06]), so a trigger the designer shipped disabled stays
disabled. This feature adds an opt-in human-only counting mode, stops losing firings that land
on the agent's own message, and lets an agent enable or disable its own `every_n_messages` and
`silence_minutes` triggers, but only those the designer explicitly allowed.

Born from `docs/audits/2026-10-12-crat-ta-multi-speaker-facilitation/findings.md`, F-2 and the
platform half of F-3.

## 2. Goals and Non-goals

**Goals**
- `every_n_messages` gains a counting mode, `all` (today's behavior, the default) or `human`
  (only user and guest messages count).
- In `all` mode, a firing that lands on the agent's own message fires on the next message
  instead of being dropped.
- `update_wakeup` can set `enabled` on `every_n_messages` and `silence_minutes`, limited to the
  triggers the designer listed in a new `self_toggle` setting, which defaults to none.
- Every toggle is audit-logged; the tool never touches `call_only` and never leaves the agent
  inert.
- The tool's description matches the counting semantics.
- The agent editor exposes both new settings in both locales.

**Non-goals**
- Per-binding (per-room) wake-up state; `update_wakeup` still writes the agent row, and rooms
  that share an agent still share its cadence (audit F-9, FU-6).
- Changing the default counting mode for existing agents.
- Letting the agent edit `autostop_rounds`, `allow_self_open`, `refresh_every_hours`,
  `soft_bounds` or `self_toggle` itself.
- Exempting toggles from the [R15.09] refresh: a refresh restores the designer's values,
  toggles included, as it does for `n` and `t_minutes` today.
- Updating the CRAT pack to use the new settings (that is FU-2 of
  `2026-10-12-crat-pack-prompt-platform-alignment`).
- Reconciling the call-only copy in the editor with [R15.05a] (FU-1).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Change the platform at all, given [R15.01]/[R15.06] make today's behavior intentional? | Yes, as an opt-in feature with an SRS delta. | User's choice. |
| Q-2 | Default counting mode? | `all`; `human` is opt-in. Also fix the lost firing in `all` mode. | User's choice. Existing agents keep their cadence; the lost firing is undocumented behavior with no intent behind it. |
| Q-3 | Who may the agent toggle? | Only triggers listed in a designer-set `self_toggle`, default empty; never `call_only`; never into an inert config; every toggle audited. | User's choice. A prompt-injected message could otherwise switch on autonomous turns paid from the project's key, and `every_n_messages` has no round cap ([R15.03] bounds only silence). |
| Q-4 | Is a released observation a human message in `human` mode? | No. | It is agent-authored; `observations.py:248` passes `sender_is_user=True` only to reset autostop ([R28.06]). |
| Q-5 | Does a toggle survive the [R15.09] refresh? | No; the refresh restores the snapshot, toggles included. | Same contract as the numeric fields; the refresh exists to undo runtime drift. |
| Q-6 | Dependencies on `2026-10-12-stale-reply-recheck` or `2026-10-12-crat-pack-prompt-platform-alignment`? | None. | The re-check dossier edits `tool_registry.py:119-147` (a new frozenset beside `BUILTIN_TOOL_NAMES`) and this one edits `:359-398` (the tool); disjoint regions, whoever builds second rebases. The pack dossier edits no platform code. |

## 4. Current State

- **Model.** `EveryNMessagesTrigger(enabled, n)` and `SilenceMinutesTrigger`
  (`backend/contexts/orchestration/domain/models.py:181-202`); `WakeupConfig.from_dict` keeps
  only keys it knows (`:233-295`), `to_dict` mirrors it (`:297-329`); `is_inert` is true when all
  three `enabled` flags are false (`:331-336`).
- **Counting.** `WakeupService.on_message_created` (`wakeup_service.py:62-150`) increments one
  counter per (agent, room) for every message (`:117`, Redis INCR in `wakeup_state.py:76-87`),
  fires on `count % n == 0` (`:119`), and drops the firing when the triggering message is the
  agent's own (`:120-121`). Human sends pass `sender_is_user=True` for users and guests alike
  (`backend/app/api/v1/messages.py:200,275-277`); agent replies pass `False` with
  `sender_agent_id` (`turn_engine.py:3582-3588`); released observations pass `True`
  (`backend/app/api/v1/observations.py:248`).
- **Self-modification.** `update_wakeup` (`wakeup_service.py:309-411`) writes only `n` and
  `t_minutes` (`:361-364`), as the system actor with `replace_wakeup_config=True` (`:376-384`),
  and audits only clamps (`agent.wakeup_clamped`, `:393-409`). The tool schema has two integers
  (`tool_registry.py:359-372`) and its descriptions say "user messages" (`:364,391-395`), which
  contradicts [R15.01].
- **Refresh.** `refresh_wakeup_config` replaces the whole config with
  `wakeup_authored_snapshot` (`wakeup_service.py:417-499`); the snapshot is written only by
  non-system actors (`agent_service.py:880-912`), so runtime edits never reach it.
- **API.** `_validate_wakeup_config` (`backend/app/api/v1/agents.py:158-225`) checks the known
  trigger keys and passes other keys through unvalidated (`:159-162`).
- **Frontend.** `SWakeupEditor.vue` (`frontend/src/shared/ui/SWakeupEditor.vue:183-290`) edits the
  two trigger cards; types and normalization in `frontend/src/shared/types/workflow.ts:47-164`;
  strings under `app.wakeup.*` in `frontend/src/app/locales/en.json:19-42` and `zh-TW.json:19`.

## 5. Design

### Options considered

**Option A, counting: one counter, mode-gated increment.** Keep one counter and skip the INCR for
non-human messages in `human` mode. Simple, and switching modes at runtime only shifts the next
firing by at most `n - 1` messages.

**Option B, counting: count human messages for everyone and derive.** Two counters per (agent,
room), choose at evaluation. Doubles Redis writes for every agent to serve an opt-in mode.

**Option C, toggles: disable-only.** The agent may turn off triggers the designer enabled and
turn them back on, but never enable one the designer disabled. No new setting, but it cannot
express "ship with silence off and let the agent turn it on in the thinking phase", which is the
case that motivated it.

**Option D, toggles: designer allowlist.** A `self_toggle` list names the triggers the agent may
set either way.

### Decision

Option A for counting and Option D for toggles. A keeps the hot path at one INCR per agent per
message. D is the only toggle model that serves the motivating case while keeping the
authorization decision with a human, and its default (empty) preserves today's [R15.06]
behavior exactly. Given up: a mode switch does not reset the counter, so the first firing after
a switch can come early.

The lost firing in `all` mode is fixed with a per-(agent, room) "due" flag: when `count % n == 0`
lands on the agent's own message, set `wakeup:msg_due:{agent}:{room}`; the next message from any
other sender fires the agent and clears it. This keeps [R15.01]'s count intact. In `human` mode
the agent's own messages never count, so the case cannot arise.

## 6. Detailed Changes

- **Backend, orchestration domain** (`models.py`): `EveryNMessagesTrigger.count: Literal["all",
  "human"] = "all"`; `WakeupConfig.self_toggle: frozenset[Literal["every_n_messages",
  "silence_minutes"]] = frozenset()`; both parsed tolerantly in `from_dict` (unknown values
  resolve to the default, matching `_tolerant_bool`'s posture at `:170-178`) and emitted by
  `to_dict`.
- **Backend, orchestration application** (`wakeup_service.py`):
  - `on_message_created` gains `sender_is_human: bool`. In `human` mode the INCR at `:117` runs
    only when it is true. The due flag (§5) is read and cleared in the same branch.
  - `update_wakeup` gains `every_n_messages_enabled: bool | None` and
    `silence_minutes_enabled: bool | None`. A toggle for a trigger not in `self_toggle`, any
    toggle while `call_only.enabled`, or a toggle that would leave the config inert raises a new
    `WakeupToggleNotAllowed` domain error (`contexts/orchestration/domain/errors.py`, beside the
    existing R15.06 error at `:61`). Allowed toggles are written in `_build_new_dict` (`:357-365`)
    and audited as `agent.wakeup_toggled {trigger, enabled, actor_agent_id}`.
- **Backend, orchestration infrastructure** (`wakeup_state.py`): due-flag get/set/clear with the
  counter's TTL.
- **Backend, orchestration interfaces** (`facade.py`): pass the new parameters through
  `on_message_created` (`:135-142`) and `update_wakeup` (`:176-189`).
- **Backend, conversation** (`contexts/conversation/application/triggers.py:48-81`) and callers:
  `messages.py:275-277` passes `sender_is_human=True`; `turn_engine.py:3582-3588` and
  `observations.py:248` pass `False`.
- **Backend, agents runtime** (`tool_registry.py:359-398`): schema gains two optional booleans;
  descriptions rewritten so `n` is "every N room messages; by default every message counts,
  including agents' own, unless this agent is configured to count only human messages", and the
  toggles say they are refused unless the designer allowed them. `WakeupToggleNotAllowed`
  surfaces as a tool error naming the reason.
- **API contract**: `_validate_wakeup_config` (`agents.py:158-225`) validates `count` and
  `self_toggle`. `wakeup_config` is a free-form dict in the generated client, so `gen:api`
  produces no model change; it is rerun anyway to confirm.
- **Frontend**: `SWakeupEditor.vue` gains a count-mode select in the every-N card and an "agent
  may turn this trigger on or off itself" checkbox in each autonomous card; `workflow.ts`
  `WakeupConfig`, `DEFAULT_WAKEUP` and `normalizeWakeupConfig` carry both fields; new keys under
  `app.wakeup.*` in `en.json` and `zh-TW.json`.
- **Docs**: `docs/implement/G-orchestration.md:18,80,95,215,242-244` updated to the new
  contract.
- **Migration**: none; `wakeup_config` is JSONB and absent keys parse to defaults.
- **Deploy/config**: none.

## 7. NFR Checklist

- [ ] i18n: the select, its two options, the two checkboxes and their help text, in both
  locales.
- [ ] Audit log: `agent.wakeup_toggled` on every toggle; clamps keep `agent.wakeup_clamped`.
- [ ] Tenant isolation: N/A, no new endpoint; the existing agent create/patch authorization
  covers the two new keys.
- [ ] Error handling UX: a refused toggle is a tool error to the model, not a user-facing state;
  the editor shows the checkboxes disabled while call-only mode is on.
- [ ] Performance: at most one extra Redis GET per firing evaluation (the due flag), only in
  `all` mode when a firing lands on an own message; no added INCRs.

## 8. Security Considerations

This touches an agent/LLM tool surface (check-security's LLM/agent dimension).

- **Prompt injection to raise spend**: a room message could instruct the agent to enable its
  triggers. Bounded by `self_toggle` (designer opt-in, default empty) and by the inert refusal;
  the tool cannot edit `self_toggle`, `autostop_rounds` or `call_only`. Residual risk: on an
  agent whose designer allowed `every_n_messages`, an injected instruction can enable it, with no
  round cap. Recorded in the editor's help text for that checkbox.
- **Prompt injection to silence an agent**: disabling both triggers is refused (inert), so an
  agent cannot be talked into permanent autonomous silence.
- **Audit**: every toggle is attributable via `actor_agent_id`.
- **Input validation**: `count` and `self_toggle` validated at the API boundary; the tool
  schema rejects non-boolean toggles before invoke (`tool_registry.py:324-332`).

## 9. Quality Notes

- **Existing debt**: the tool description already contradicts [R15.01]
  (`tool_registry.py:364`); fixed here. The editor's call-only copy contradicts [R15.05a]
  (`SWakeupEditor.vue:38-42`, `en.json:24-25`); not fixed here (FU-1). `update_wakeup` has no
  audit for unclamped numeric changes (`wakeup_service.py:393`); left as is, since toggles get
  their own event.
- **Patterns to follow**: tolerant parsing in `models.py:147-178`; clamp-then-audit flow in
  `update_wakeup`; reserved-name and schema drift tests in
  `backend/tests/unit/test_builtin_tools_wiring.py`.
- **Reuse inventory**: `_tolerant_bool`, `_default_below_one`, `merge_json_config`, the
  `_check_wakeup_*` helpers in `agents.py:114-157`, `wakeup_state`'s TTL helpers, the existing
  `SWakeupEditor` card layout and `normalizeWakeupConfig`.

## 10. Risks and Rollback

- **Early first firing after a mode switch** (§5): accepted.
- **Behavior change in `all` mode**: an agent whose firing used to be dropped now speaks on the
  next message. This is the intended fix; it is the only change visible to existing agents.
- **Rollback**: revert the code; stored `count` and `self_toggle` keys are ignored by the old
  parser (`from_dict` keeps only known keys), and the due flag expires on its TTL.

## 11. Acceptance Criteria

- [ ] AC-1: with `count: "human"` and `n = 3`, two user messages and any number of agent
  messages do not fire the agent; the third user or guest message does.
- [ ] AC-2: with `count: "human"`, a released observation does not advance the counter.
- [ ] AC-3: with `count: "all"` (and with the key absent), the counter behaves as today except
  that a firing landing on the agent's own message fires on the next message from another
  sender.
- [ ] AC-4: `update_wakeup` with `silence_minutes_enabled: true` succeeds and is audited when
  `silence_minutes` is in `self_toggle`, and is refused with a tool error when it is not.
- [ ] AC-5: any toggle is refused while `call_only.enabled`, and a toggle that would leave every
  trigger disabled is refused.
- [ ] AC-6: the tool cannot change `self_toggle`, `count`, `autostop_rounds` or `call_only`.
- [ ] AC-7: an [R15.09] refresh restores toggled triggers to the designer's values.
- [ ] AC-8: the API rejects an invalid `count` or `self_toggle` with a validation error, and
  round-trips valid values.
- [ ] AC-9: the editor shows and saves both settings, in both locales.
- [ ] AC-10: the tool descriptions state the counting semantics and the toggle restriction.
- [ ] AC-11: backend `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`; frontend
  `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` pass.

## 12. Test Plan

- Unit, `backend/tests/unit/test_wakeup_service.py`: AC-1, AC-2, AC-3 against a fake
  `wakeup_state`, including the own-message landing case (no test isolates the cadence today).
- Unit, `backend/tests/unit/test_wakeup_self_modification.py`: AC-4, AC-5, AC-6 and the audit
  event.
- Unit, `backend/tests/unit/test_wakeup_refresh_interval.py`: AC-7.
- Unit, `backend/tests/unit/test_agent_runtime_tools.py`: update the fake facade at `:50` for
  the new kwargs; AC-10 asserts the description text.
- API unit tests for `_validate_wakeup_config`: AC-8.
- Frontend, `frontend/src/shared/types/__tests__/workflow.test.ts` and an `SWakeupEditor`
  component test: AC-9.
- Manual via the `run` skill: one room, `count: "human"`, `n = 2`, a peer agent posting; confirm
  the cadence.

## 13. SRS Delta

Replace [R15.01] (`REQUIREMENTS.md:882`):

> - **[R15.01]** `every_n_messages.n` counts messages in the room, scoped to the room (Q49). By
>   default (`count: "all"`) every message counts, user and agent alike. With
>   `count: "human"` only messages sent by users and guests count; agent replies and released
>   observations do not.
> - **[R15.01a]** In `count: "all"` mode, when the N-th message is the agent's own, the firing
>   is deferred to the next message sent by anyone else rather than dropped.

Add `"count": "all"` to the `every_n_messages` object and `"self_toggle": []` to the top level of
the JSON example at `REQUIREMENTS.md:870-880`.

Replace [R15.06] (`REQUIREMENTS.md:892`):

> - **[R15.06]** An Agent can modify, during a run and via the built-in tool
>   `update_wakeup({every_n_messages?, silence_minutes?, every_n_messages_enabled?,
>   silence_minutes_enabled?})`, only `every_n_messages.n`, `silence_minutes.t_minutes`, and the
>   `enabled` flag of each of those two triggers that the Agent Designer listed in
>   `self_toggle` (default empty).
> - **[R15.06a]** A toggle is refused when the trigger is not in `self_toggle`, when
>   `call_only` is enabled, or when it would leave every trigger disabled. The agent cannot
>   modify `self_toggle`, `count` or `call_only`. Every accepted toggle is audit-logged with the
>   acting agent.

`docs/traceability.csv` is regenerated with `python scripts/traceability.py`.

## 14. Open Questions

None blocking.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

- **FU-1** — the editor describes call-only mode as "responds only when @mentioned"
  (`SWakeupEditor.vue:38-42`, `en.json:24-25`), while [R15.05a] defines it as A2A call or
  instruct only.
- **FU-2** — per-binding wake-up state (audit F-9).
