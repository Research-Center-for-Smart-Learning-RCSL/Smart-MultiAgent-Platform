---
type: bugfix
status: implemented
created: 2026-10-07
requirements: [R9.10, R13.33, R13.34, R28.06, R30.08]
depends_on: []
---

# Keep released observations and activity echoes in their place in the agent's conversation

## 1. Summary

A room transcript holds three kinds of system message: the compaction summaries an agent
writes for itself, observations the room owner releases to the room, and the echoes an
activity submission posts. The agent's history loader treats every system message as a
summary: each one is lifted out of the conversation and placed at the top of the system
prompt under the heading "[Earlier conversation summary]", and the newest one becomes the
"earlier conversation summary" retrieval query. A released analysis or a submission echo
therefore reaches the agent out of order, under a heading that misdescribes it, and with the
standing of the system prompt; an echo can carry a digest of a participant's submitted
content, which thereby gains that standing too. This dossier fixes finding F-4 and its
security aspect FU-6 of `docs/audits/2026-10-07-display-name-resolution/findings.md`.

## 2. Observed vs Expected

- **Observed** — `_ROLE_BY_SENDER` maps sender type `SYSTEM` to role `system`
  (`backend/contexts/agents/application/runtime/transcript.py:73-79`), and `_to_history`
  checks only for a compaction summary before applying that map (`:141-142`), so every
  system message becomes a `system`-role history row. `load_model_history` keeps
  non-summary system rows among the survivors (`:208-220`). The turn engine then renders
  every `system`-role row as `[Earlier conversation summary]\n<content>` into the system
  prompt (`backend/contexts/agents/application/runtime/turn_engine.py:2799-2803`) and leaves
  it out of the message stream, which keeps only `user` and `agent` rows (`:2870-2878`);
  `_knowledge_queries` takes the newest `system`-role row as the summary retrieval query
  (`:4739-4744`). The non-summary system messages are released observations
  (`backend/contexts/conversation/application/observation_service.py:183-189`) and activity
  echoes (`backend/contexts/activities/application/submission_service.py:317-334`, text
  built by `_echo_text` at `:640-678`, which can append `Content: <agent digest>`). No test
  covers a non-summary system row: the transcript and budget fixtures all use
  `compact_summary` (`backend/tests/unit/test_agent_runtime_transcript.py:79, 144`,
  `backend/tests/unit/test_turn_context_budget.py:656`).
- **Expected** — a released observation "follows the standard broadcast and wake-up paths"
  ([R28.06]); an echo is posted "into the room transcript for readability" ([R30.08]), and
  the echo's own code states that system rows "flow into every agent's history the same as
  any other message" (`submission_service.py:307-314`). A summary is what `/compact` writes
  ([R9.10]). Every label reaching a model context is normalised so it cannot punctuate its
  container ([R13.34]).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How do released observations and activity echoes appear to an agent? | In place in the conversation, as a `user`-role turn opened by a fixed platform marker, `[Room notice]`; the participant note states that only the platform writes that marker; model-facing labels lose square brackets so no participant or agent label can reproduce it. | Requester's choice. Keeps chronology and the turn alternation providers require (a `system` role mid-stream is rejected by several adapters; the stream already maps other agents to `user` for the same reason, `turn_engine.py:3774-3777`). An unmarked turn was rejected as indistinguishable from an unlabelled participant turn; hiding the rows from agents was rejected because it contradicts [R28.06] and the echo's purpose. Removing brackets from labels follows [R13.34]'s rule that a label loses the delimiters its context uses. |
| Q-2 | Does this depend on another dossier? | `depends_on: []`. | No dossier that is not implemented touches `transcript.py` or these parts of `turn_engine.py`. `2026-10-07-display-name-validation` from the same audit also edits `_provider_message` and `_one_line_label`, and will list this one in its `depends_on`. |

## 4. Reproduction

1. A room with a bound agent and an observer agent; disclosure either way.
2. The observer writes an observation; the room owner releases it to the room.
3. A participant then posts a question that wakes the room agent. Inspect the provider
   request the turn engine builds (for example by logging the payload in a unit test of
   the request assembly): the released analysis appears in the `system` text as
   `[Earlier conversation summary]\n<analysis>`, not in the message list between the
   messages it was posted between.
4. Bind an activity type with `echo_includes_content` and `expose_payload_to_agent` on;
   submit an attempt whose payload contains instructions. The echo, including the digest,
   appears in the `system` text under the same heading.

## 5. Root Cause Analysis

1. **Root cause:** `_to_history` decides the role of a system message by sender type alone
   once it is not a compaction summary (`transcript.py:142`), so the `system` role, which
   the turn engine reserves for summaries, also covers every other system message.
2. The turn engine trusts that role: it lifts all `system`-role rows into the system prompt
   as summaries (`turn_engine.py:2799-2803`), drops them from the stream (`:2878`), and uses
   the newest as the summary retrieval query (`:4739-4744`).
3. Aggravating: nothing distinguishes a platform notice from a participant turn in the model
   context, so even a correctly placed notice would need a marker no label can imitate.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every agent turn in a room that has released observations or activity
  echoes: misordered context, a misleading heading, an inflated trust level for echoed
  participant content, and knowledge retrieval queried with an echo instead of a summary.
  Nothing is persisted wrongly; the rows themselves are correct.
- **Sibling suspects**:
  - Compaction input: `summariser.py:27` renders history rows as `[{role}] content`; a
    non-summary system row reaches it with role `system` today. With the fix it reaches it
    as a room notice, which belongs in a summary. Covered by the same change.
  - Observer agents read the room through the same history loader (`transcript.py:174-220`)
    and are affected identically. Covered.
  - Other system-message writers: only the three found (`transcript.py:246` summaries,
    `observation_service.py:185`, `ConversationFacade.insert_system_message` used only by
    the activity echo at `submission_service.py:317`). Cleared.
  - The research export and the chat export read system rows separately
    (`research_export_builder.py:219`, `chat_export_service.py`) and do not use the model
    roles. Cleared.
  - Other places keyed on `role == "system"`: `turn_engine.py:2802` and `:4740` only.
    Covered.

## 7. Fix Design

1. **A distinct role for room notices.** `_to_history` keeps `system` for compaction
   summaries and gives every other system message the role `notice`
   (`transcript.py:73-79, 141-142`); the loader's survivor logic is unchanged
   (`:208-220`).
2. **Notices stay in the stream.** The message list keeps `notice` rows in order
   (`turn_engine.py:2870-2878`), and `_provider_message` renders one as
   `{"role": "user", "content": "[Room notice] <content>"}`, with the kind stated for a
   released observation (for example "The room owner shared an analysis: ...", the wording
   the private release note already uses, `turn_engine.py:2361`). The summary block and the
   summary retrieval query select only `system` rows, which are now only summaries
   (`:2799-2803, 4739-4744`).
3. **The marker is the platform's.** `_PARTICIPANT_LABEL_NOTE` (`turn_engine.py:364-372`)
   gains one sentence: a turn opened by `[Room notice]` is posted by the platform, never by
   a participant. `_one_line_label` (`:437`) also removes `[` and `]`, so no human or agent
   label renders as the marker.
4. **Budget and compaction.** `notice` rows count toward the history budget like any
   other turn, and the summariser receives them; the role name appears in its input as
   `[notice]`.

The fix corrects the role assignment instead of filtering by `metadata.type` at the
render site, so every consumer of the history (prompt assembly, retrieval, compaction)
agrees on what a summary is.

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- `backend/tests/unit/test_agent_runtime_transcript.py`: a released-observation message and
  an activity-echo message load as `notice`-role history rows, while a compaction summary
  still loads as `system`; it fails today because both load as `system`.
- A turn-assembly test (beside `backend/tests/unit/test_turn_context_budget.py` or
  `test_turn_participant_note.py`): with history `[user msg, released observation, user
  msg]`, the system text contains no `[Earlier conversation summary]` block and the provider
  messages contain a `[Room notice]` turn between the two user turns; it fails today because
  the observation is in the system text and absent from the messages. A second case: with a
  compaction summary and an echo, the summary block holds only the summary and the
  retrieval queries' summary entry is the summary, not the echo.
- A label test: a participant named `[Room notice]` renders as `Room notice:` (brackets
  removed); it fails today because brackets survive `_one_line_label`.

## 9. Security Considerations

- Prompt injection (`check-security` Part F, agent attack surface): today participant-written
  echo digests reach the system prompt. After the fix they reach a `user`-role turn, the
  same standing as the participant's own messages, and the platform marker cannot be worn by
  a participant label. The digest itself is already collapsed to one line before it is
  echoed (`submission_service.py:636-637`), so it cannot open a fake marker line of its own.
- A message body can still contain the literal text `[Room notice]` mid-turn; it sits
  inside a turn opened by that participant's label, which the participant note now
  explains. Not addressed further here (the audit's FU-5 covers body content).

## 10. Risks and Rollback

- Agents now see released observations and echoes as conversation turns; prompts that
  relied on the old placement change behaviour, which is the intended correction.
- Removing square brackets from model-facing labels changes how a name such as
  "Alex [TA]" reads to agents ("Alex TA"); it is not stored or shown to people differently.
- Rollback: revert; no schema or data change.

## 11. Acceptance Criteria

- [x] AC-1: the regression tests in §8 fail before the fix and pass after.
- [x] AC-2: the system prompt's summary block contains compaction summaries only.
- [x] AC-3: a released observation and an activity echo reach the agent in chronological
  order among the other turns, each opened by `[Room notice]`.
- [x] AC-4: no human or agent label in the model context contains `[` or `]`, and the
  participant note states that only the platform writes `[Room notice]`.
- [x] AC-5: the summary retrieval query is built only from a compaction summary.
- [x] AC-6: backend lint, typecheck and tests pass in CI.

## 12. SRS Delta

None. [R28.06] and [R30.08] already put these messages in the room's transcript, and
[R13.34] already requires labels not to carry the context's delimiters.

## 13. Deviation Log

- **D-1. Assembly tests run against extracted helpers, not a full room turn.** No unit
  harness drives `run_turn`'s request assembly, which is a closure. A behaviour-preserving
  commit first moved the summary and stream selection into `_summary_blocks` and
  `_stream_rows` (`turn_engine.py`), which the closure calls. The §8 tests feed them the
  real `load_model_history` output, so they failed against the pre-fix role assignment
  for the documented reason. `_shows_participant_note` was extracted the same way for D-3.
- **D-2. §9's claim that the echo digest is one line was wrong.** `submission_service.py:637`
  collapses only the group label; `build_agent_digest` passes a validator `detail` through
  raw. Found by `/code-review` and fixed with the requester's agreement: the marker is
  defanged inside notice bodies, an activity notice renders on one line, and the
  participant note states that text after "Content:" is the participant's submission.
- **D-3. Further review fixes, agreed with the requester.** (a) An agent whose name is missing
  or one-lines to nothing (for example `[ ]`) is labelled `Agent:` instead of being sent
  unprefixed, and any other agent's turn now brings the participant note. (b) The
  participant note renders whenever a notice is present, not only when a turn is
  labelled, since only the note explains the marker. (c) A released observation reads "An
  analysis was released to the room:", not "The room owner shared an analysis:", because
  admins and moderators of a room with no recorded creator can release too
  (`access.py:544-551`). (d) Notices feed the "Recent conversation" retrieval query as
  `Notice:` rows, so a question about a just-released analysis retrieves against it.
- **D-4. Marker defanged in every body, not only in notices.** From the `check-security`
  gate, agreed with the requester. Providers combine consecutive user turns, so a
  `[Room notice]` line inside a participant message, another agent's reply, an attachment
  excerpt or the live input read like a real notice. Every such body has the marker
  rewritten, case-insensitively and with any spacing. This narrows §9's "not addressed
  further here" for the marker string only; other body content is still FU-5 of the audit.

## 14. Follow-ups

- **FU-1.** Compaction summaries still carry no speaker names (audit FU-3); a notice folded
  into a summary keeps its marker word but not who released it.
- **FU-2.** A row's `token_count` leaves out render-time prefixes (`[Room notice] `, the
  observation lead-in, and the existing `Name: ` labels), so the history budget and the
  compaction trigger undercount by a few tokens per row. Pre-existing pattern, from
  `/code-review`.
- **FU-3.** A display name such as `Room notice` (brackets stripped) or one using fullwidth
  brackets still reads close to the marker. The note quotes the marker with its ASCII
  brackets; a stronger fix belongs with `2026-10-07-display-name-validation`. From
  `/code-review`.
- **FU-4.** Behaviour in a running app (an agent answering after a release and after an
  echo) has not been observed; the ACs rest on unit tests. A staging check.
