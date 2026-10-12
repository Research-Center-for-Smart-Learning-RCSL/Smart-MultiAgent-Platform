---
type: feature
status: draft
created: 2026-10-12
requirements: [R9.09, R9.10, R13.33, R28.05]
depends_on: []
---

# Show room agents how time passes between messages

## 1. Summary

A room agent sees its conversation as an ordered list of `Label: body` lines with no time
information at all, and its system prompt carries no current time. Five messages sent in the
same ten seconds and five messages spread over three minutes look identical, and "the
discussion has stalled for two minutes" cannot be judged. Facilitation prompts are written
around exactly those judgments. This feature adds two things to every room and observer turn:
a gap notice in the transcript wherever a minute or more passed between consecutive messages,
and a system line giving the current time and how long ago the last room message was sent.

Born from `docs/audits/2026-10-12-crat-ta-multi-speaker-facilitation/findings.md`, F-5.

## 2. Goals and Non-goals

**Goals**
- Wherever at least 60 seconds separate two consecutive messages in a room turn's history, the
  model sees a notice stating how long passed.
- Every room and observer turn's system prompt states the current time (UTC) and the time since
  the last room message.
- The added text is counted in the history and request budgets, so compaction decisions stay
  correct.
- Nothing a participant writes can produce or imitate a time notice.

**Non-goals**
- Per-message timestamps or ages (Q-1).
- Time zones: the platform has no time zone setting on users, orgs, projects or rooms, and this
  feature does not add one (Q-3).
- Headless turns (`run_input_turn`, A2A and approver turns): they load no history
  (`turn_engine.py:1275,1393`).
- Timing inside compaction summaries: the summariser renders `[{role}] {content}` and drops
  time (`contexts/agents/application/runtime/summariser.py:26-27`); unchanged here (FU-1).
- Prompt caching: none exists today (§4); §10 records what this choice means if it is added.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which time representation? | Gap notices between messages at least 60 s apart, plus a current-time line with the age of the last message. | User's choice. The judgments facilitation prompts need are "did these arrive together" and "how long has the room been quiet"; gaps and the last-message age answer both. Per-message ages would add a prefix to every one of up to 500 rows (`transcript.py:65`). |
| Q-2 | All agents, or opt-in per agent? | All room and observer turns. | User's choice. The cost is a few tokens per gap, and every room agent's pacing judgment benefits. Existing agents see new notices; §10 covers it. |
| Q-3 | Which clock? | UTC, labelled as UTC. | No time zone setting exists anywhere (§4). The relative values carry the information the prompts need; the absolute time is there for orientation. |
| Q-4 | How are gaps rendered? | As a `[Room notice]` user turn, e.g. `[Room notice] 3 minutes passed with no messages.` | Reuses the existing notice convention, which is unforgeable because labels drop square brackets and bodies are defanged (`turn_engine.py:366-371,439,489`). |
| Q-5 | Dependencies on the sibling dossiers? | None. | `2026-10-12-stale-reply-recheck` edits the posting path (`turn_engine.py:3207-3223`) and `2026-10-12-wakeup-human-count-and-trigger-toggle` edits wake-up evaluation; this edits history loading and request assembly (`transcript.py:145-176`, `turn_engine.py:1023-1046,2859-2977`). Whoever builds second rebases. If the re-check lands first, its new-message rendering should insert gap notices through the same helper (§6). |

## 4. Current State

- **The time is loaded and then dropped.** `load_model_history` (`transcript.py:179-224`) reads
  messages through `MessageRepository.list` (`contexts/conversation/infrastructure/repositories/message_repo.py:97-110`),
  whose rows carry `created_at` into the domain `Message`
  (`contexts/conversation/domain/models.py:193-203`; column `TIMESTAMP(timezone=True)` with a
  `clock_timestamp()` default, `contexts/conversation/infrastructure/tables.py:270`).
  `_to_history` (`transcript.py:145-176`) builds `HistoryMessage` (`transcript.py:87-105`)
  without it.
- **Rendering.** `_provider_message` (`turn_engine.py:3796-3854`) renders a notice as
  `[Room notice] {body}`, the running agent's own rows unprefixed, and others as
  `{label}: {body}`. The participant note (`turn_engine.py:376-418`) explains these
  conventions and is shown when labels or notices are present (`turn_engine.py:506-516`).
- **System prompt.** Blocks are built in a fixed order ending with the participant note
  (`turn_engine.py:1023-1046`), measured and rendered in two passes
  (`turn_engine.py:1062-1107`).
- **Budget.** `HistoryMessage.token_count` estimates the body only
  (`transcript.py:166,173`, `shared_kernel/tokens.py:16-33`); it already excludes label
  prefixes and the notice marker. The history projection feeds compaction
  (`turn_engine.py:3905,3936,4020`; `contexts/agents/application/context.py:213`).
- **No prompt caching.** No adapter sets cache control; the Anthropic adapter sends `system`
  as a plain string (`contexts/keys/infrastructure/adapters/anthropic.py:147-148`).
- **No time zone setting** on any entity. Existing model-facing timestamps are UTC ISO strings
  on activity rows (`activities/application/activity_context_provider.py:345-354`) and observer
  memory (`turn_engine.py:3510-3511,4625-4637`).

## 5. Design

### Options considered

**Option A — gap notices plus a current-time line.** A notice between rows separated by at least
60 s, and one system line with the current UTC time and the last message's age.

**Option B — relative age on every row.** `[2 min ago] Label: body`. Most direct, but it adds a
prefix to every row, changes every row on every turn, and puts the time into a position where
participant labels are rendered.

**Option C — absolute timestamp on every row.** Stable across turns, which a future prompt cache
would prefer, but the model has to subtract timestamps to judge a gap, and the cost per row is
the same as B.

### Decision

Option A. It answers the two questions facilitation prompts ask with the least text, keeps the
time out of participant-authored positions, and its notices sit in an already-hardened channel.
Given up: a precise per-message time, and stability for a future prompt cache (the current-time
line changes every turn).

## 6. Detailed Changes

- **Backend, agents runtime, transcript** (`transcript.py`): `HistoryMessage.created_at:
  datetime | None = None`, filled in `_to_history`. Optional because test fakes build rows
  without it (`backend/tests/unit/test_turn_context_budget.py:159,420`); a row without it never
  produces a gap.
- **Backend, agents runtime, gap notices**: a pure helper beside `_provider_message`,
  `_gap_notice(previous, current) -> dict | None`, returning a `[Room notice]` user turn when both
  rows have `created_at` and the gap is at least 60 s. Format: whole minutes below one hour
  ("3 minutes passed with no messages."), whole hours and minutes above. Applied wherever
  history rows are turned into provider messages in `_assemble_request`
  (`turn_engine.py:2859-2977`), between consecutive rows, never before the first row. Compaction
  summaries live in the system prompt, not in the row list, so no gap is computed across them.
- **Backend, agents runtime, budget**: `_assemble_history`'s projection adds a fixed per-notice
  estimate for each gap the loaded rows would produce, so the compact-mode decision
  (`turn_engine.py:3912-3919`) accounts for them. The estimate is a constant upper bound on the
  rendered notice measured with `estimate_tokens`.
- **Backend, agents runtime, system block**: a new MEASURED block `time`, placed immediately
  before the participant note (`turn_engine.py:1023-1046`): `Current time: 2026-10-12 14:03 UTC.
  The last room message was sent 2 minutes ago.` When the room has no message the second
  sentence is omitted. The block's measure is a fixed worst-case string, like
  `_PARTICIPANT_NOTE_MEASURE` (`turn_engine.py:436`).
- **Backend, participant note**: one sentence added to `_PARTICIPANT_LABEL_NOTE`
  (`turn_engine.py:376-387`) saying that time notices are written by the platform. Because gap
  notices are notices, a room whose history has a gap now shows the participant note; that is
  intended.
- **Observer turns** use the same assembly path (`turn_engine.py:2602-2607,2694`) and get both.
- **API contract**: none. `gen:api`: not required.
- **Frontend**: none.
- **Migration**: none.
- **Deploy/config**: none.

## 7. NFR Checklist

- [ ] i18n: N/A, model-facing text only; it follows the existing English notice convention.
- [ ] Audit log: N/A, no new domain event.
- [ ] Tenant isolation: N/A, no new endpoint or data access; `created_at` is already loaded.
- [ ] Error handling UX: N/A, no user-facing surface. A row without `created_at` yields no gap.
- [ ] Performance: no new query; a linear pass over at most 500 rows (`transcript.py:65`).
  Token cost: one short notice per gap of 60 s or more, plus one system line per turn.

## 8. Security Considerations

This changes what the model is shown (check-security's LLM/agent dimension).

- **Forged time notices.** A participant who could write "[Room notice] 20 minutes passed"
  could push a facilitator into stall handling. Gap notices use the notice marker, which no
  participant can produce: labels drop square brackets (`turn_engine.py:439`) and bodies are
  defanged (`turn_engine.py:489`). A test asserts a body imitating a time notice reaches the
  provider defanged.
- **No participant data** enters the notices or the time line: both are computed from server
  timestamps only.
- **Disclosure.** The current time and message ages reveal nothing a room member cannot see in
  the room already.

## 9. Quality Notes

- **Existing debt**: `token_count` already under-counts label prefixes and notice markers
  (`transcript.py:166,173`); not fixed here, but the new notices are counted explicitly rather
  than added to that gap.
- **Patterns to follow**: notice construction and defanging in `_provider_message`
  (`turn_engine.py:3823-3835`); MEASURED system blocks with worst-case measures
  (`turn_engine.py:436,1062-1107`).
- **Reuse inventory**: `_ROOM_NOTICE_MARKER` (`turn_engine.py:369`), `estimate_tokens`
  (`shared_kernel/tokens.py:16-33`), `_SystemBlocks`.

## 10. Risks and Rollback

- **Existing agents see new text.** Prompts that never mention time may comment on gaps. Low
  risk: the notices are factual and short. The CRAT pack's prompts become able to use them
  (FU-2).
- **Pinned rendering tests.** Tests that build rows without `created_at` are unaffected; tests
  that pin the system text (`backend/tests/unit/test_turn_system_blocks.py:181,189`) and the
  participant note (`backend/tests/unit/test_turn_participant_note.py:44,58`) change by design
  and are updated with the new block and sentence.
- **Future prompt caching.** The time line changes every turn; placed after the stable blocks,
  it invalidates only the tail of a cached prefix. Recorded for whoever adds caching.
- **Rollback**: revert; nothing persisted.

## 11. Acceptance Criteria

- [ ] AC-1: two consecutive history rows 3 min 20 s apart render with
  `[Room notice] 3 minutes passed with no messages.` between them; rows 59 s apart render with
  nothing between them.
- [ ] AC-2: a gap of 1 h 5 min renders in hours and minutes.
- [ ] AC-3: rows without `created_at` never produce a notice, and existing rendering tests that
  build such rows pass unchanged.
- [ ] AC-4: every room and observer turn's system prompt carries the time block with the current
  UTC time and the last message's age; with no messages, only the current time.
- [ ] AC-5: the history projection used for the compact-mode decision includes the gap notices'
  estimate, and the time block is measured.
- [ ] AC-6: a participant body imitating a time notice reaches the provider defanged.
- [ ] AC-7: headless turns are unchanged.
- [ ] AC-8: backend `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass.

## 12. Test Plan

- Unit, `backend/tests/unit/test_agent_runtime_transcript.py`: `created_at` reaches
  `HistoryMessage`.
- Unit, new `backend/tests/unit/test_turn_time_context.py`: AC-1, AC-2, AC-3, AC-6 on the gap
  helper and on assembled requests built with `turn_engine_fakes.py`; AC-4 on the rendered
  system text with a frozen clock; AC-7 on `run_input_turn`.
- Unit, `backend/tests/unit/test_turn_context_budget.py`: AC-5.
- Unit, `test_turn_system_blocks.py` and `test_turn_participant_note.py`: updated expectations.
- Manual via the `run` skill: a room with a three-minute pause; inspect the provider request in
  the fake provider's log.

## 13. SRS Delta

Add after [R13.34] (`REQUIREMENTS.md:727`):

> - **[R13.73]** **Time in the agent transcript.** In a room or observer turn, wherever at least
>   60 seconds separate two consecutive messages in the agent's history, the history carries a
>   platform notice stating how long passed (in whole minutes, or hours and minutes from one
>   hour). The turn's system prompt states the current time in UTC and how long ago the last room
>   message was sent. Both are computed from server timestamps and use the room-notice channel's
>   protections, so no participant can produce or imitate them. Headless turns are unaffected.

`docs/traceability.csv` is regenerated with `python scripts/traceability.py`.

## 14. Open Questions

None blocking.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

- **FU-1** — compaction summaries lose all timing; a summary could state the time range it
  covers.
- **FU-2** — once this lands, the CRAT pack's stall rules can refer to elapsed time instead of
  counting silence wakes (`2026-10-12-crat-pack-prompt-platform-alignment` §7.2).
