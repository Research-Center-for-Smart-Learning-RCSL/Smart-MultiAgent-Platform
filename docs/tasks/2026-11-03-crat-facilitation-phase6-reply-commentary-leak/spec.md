---
type: bugfix
status: draft
created: 2026-11-03
requirements: [R13.80, R13.72]
depends_on: []
---

# Classroom facilitation phase 6: an agent reply carries only the model's final answer

## 1. Summary

In two CRAT autonomous runs on staging a teacher reply posted to the room contained the model's
own English planning notes: once after one class-facing sentence, once in place of a reply. Both
turns were silence wakes with no tool round, on the OpenAI provider with `gpt-6-luna` (Responses
API). Students see the notes, both live as the reply streams and in the stored message. This is
phase 4 FU-15 (`docs/tasks/2026-10-27-crat-facilitation-phase4-autonomous-teacher/spec.md`).

## 2. Observed vs Expected

**Observed.** The D-30 run (`frontend/sim/runs/20261101-080511-crat-autonomous/transcript.json`,
message `3ba9cfda`, 2026-11-01 08:17:37 UTC, metadata `trigger: silence_minutes`,
`tool_rounds: 0`) holds a teacher message whose first sentence addresses the class and whose
remainder is English planning about how to write the message. The earlier run
`20261101-025751-crat-autonomous` holds a second message of the same kind at 03:41:06 UTC
(`trigger: silence_minutes`, `tool_rounds: 0`), consisting of planning text only. A scan of every
local simulator transcript (33 runs, all courses) finds no other agent message with a run of
English prose. The staging agent `7b0a16d5` (TA 字詞拼圖教師代理) uses provider OpenAI, model
`gpt-6-luna`, effort at the provider default, read from its settings page on 2026-11-03.

**Expected.** An agent's room reply is the model's final answer to the room and nothing else.
No requirement states this today; the user confirmed it as the expected behaviour (Q-1), and
Section 11 adds it as [R13.80].

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the fix be scoped, given that the raw response of the two turns was never stored? | Filter OpenAI output by message `phase`, and record the output's structure (item types, phases, text lengths, never content) on the turn's audit row. | Removes the one leak path the adapter has, and makes a recurrence diagnosable. A content heuristic in the turn engine (for example, cut English prose from a Chinese reply) was rejected as fragile; diagnosis alone was rejected as leaving a known leak open. |
| Q-2 | When a response carries phased messages but no `final_answer` (truncated or commentary-only), what is the reply? | Empty, so the turn ends as `empty_reply` and posts nothing. | Posting commentary is the defect. A silent turn is already a normal outcome and the next wake retries. Decided by the implementer under the user's standing delegation; recorded here for review. |

## 4. Reproduction

Not reproducible on demand: the platform keeps neither the raw provider response nor any
reasoning (`turn_engine.py:5113-5116`; Section 5), and the model chooses when to emit
commentary. The deterministic reproduction is at the adapter boundary: a Responses object whose
`output` holds a `message` item with `phase: "commentary"` followed by one with
`phase: "final_answer"` is normalised today into a reply containing both texts, and the stream
emits `TokenDelta`s for both.

## 5. Root Cause Analysis

1. An agent turn always streams (`turn_engine.py:5053`, synthesis at `:5226`). For the OpenAI
   provider each `response.output_text.delta` becomes a `TokenDelta`
   (`backend/contexts/keys/infrastructure/adapters/openai.py:424-427`), relayed live to the room
   as `agent.token` (`turn_engine.py:5054-5059`), whatever output item the delta belongs to.
2. At `response.completed` the terminal object is normalised by `_normalise_response`
   (`openai.py:295-331`), which joins the `output_text` parts of **every** `message` item into
   `text` (`:305-310`, `:322`). It does not read the item's `phase`.
3. Newer OpenAI models can return more than one assistant `message` item per response, marked
   `phase: "commentary"` (intermediate) or `phase: "final_answer"`. OpenAI's reference documents
   the field and asks clients to send it back on assistant messages for gpt-5.3-codex and later.
4. The turn engine takes `body["text"]` as the reply (`turn_engine.py:5084`, `:3724`), checks only
   that it is not blank (`:3784`), and posts it unchanged through `MessageService.send_agent`
   (`:3941-3948`; `contexts/conversation/application/message_service.py:200`).

**Root cause:** link 2, the normaliser joining commentary messages into the reply, with link 1
as its streaming twin. It is the only place where text other than the final answer can enter
the reply on this provider: reasoning items are kept apart in `provider_items` (`openai.py:329`)
and never read as text.

**Not proven.** Because nothing stored the two responses, whether they actually carried a
commentary item cannot be confirmed. The alternative is that the model wrote planning text
inside its final answer, which no adapter change can catch. The structure record in Section 7 is
what tells the two apart on a recurrence; see FU-1.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every agent on the OpenAI provider whose model emits phased messages: room
replies, observer turns, A2A turns (`run_input_turn`, `turn_engine.py:2086-2100`), and the
non-streaming consumers that read the same normaliser's `text`, namely the summariser
(`summariser.py:70`), the triple extractors (`knowledge/infrastructure/triple_extractor.py:115`,
`knowmap_triple_extractor.py:104`) and the prompt assistant
(`app/workers/tasks/prompt_assistant.py:153`). Stored messages already posted stay as they are;
there is no data repair (two messages on staging, in simulator rooms).

**Sibling suspects.**
- Anthropic (`anthropic.py:179-197`, `:254-260`): reads only `text` blocks and `text_delta`;
  thinking is never requested. Cleared.
- Gemini (`gemini.py:157-159`, `:271-274`): joins every part with a `text` key and does not check
  `part.thought`. Safe today only because `includeThoughts` is never sent (`:142-144`). Cleared
  but fragile; FU-2.
- OpenAI-compatible (`openai_compat.py:188-192`, `:329-332`, `:358`): reads only `content`, so a
  server that puts a harmony analysis channel or `<think>` block inside `content` leaks it
  verbatim. No staging agent uses this provider. Plausible, out of scope; FU-3.
- OpenAI input replay: assistant messages from earlier turns are sent back as
  `{"role": "assistant", "content": ...}` without `phase` (`openai.py:174-175`); within one turn
  the provider's own items, phase included, are replayed verbatim (`:159-173`). Not a leak path;
  whether to label replayed history as `final_answer` is FU-4.

## 7. Fix Design

- **Adapter, non-streaming and terminal body** (`openai.py:_normalise_response`). Collect the
  `message` items. If none of them carries a `phase`, keep today's behaviour (join all). If any
  does, join only the items whose `phase` is `final_answer`; with none, `text` is empty (Q-2).
  `provider_items` stays the whole `output`, unchanged, so the in-turn replay still sends the
  commentary back with its phase, as OpenAI asks.
- **Adapter, stream** (`openai.py:stream`). Track each message item's phase from
  `response.output_item.added` by item id, and emit a `TokenDelta` for an
  `output_text.delta` only when its item is not marked `commentary`. A delta whose item was never
  announced is emitted, as today. The terminal body still comes from `_normalise_response`, so
  the stored reply and the streamed tokens agree.
- **Structure record.** `_normalise_response` adds `output_shape` to the body: one entry per
  output item with its `type`, its `phase` when present, and for a message the length of its
  text. No text, no ids. The turn engine carries the shape of the final response through
  `ToolLoopOutcome` and adds it as `output_shape` to the `agent.turn_finished` audit metadata on
  every path that writes that row (`turn_engine.py:2094`, `:3814`, `:3898`, `:3950`), when the
  provider supplied one. Other providers supply none and their rows are unchanged.

This corrects the cause on the only provider where it exists, rather than inspecting the reply's
language, and leaves the commentary in the model's own context where OpenAI expects it.

## 8. Regression Test Plan

First, in `backend/tests/unit/` beside the existing OpenAI adapter tests:
1. `_normalise_response` on an output with a commentary message then a final_answer message
   returns only the final answer's text. Fails today: both texts are joined.
2. The stream fed `output_item.added` (commentary), its deltas, `output_item.added`
   (final_answer), its deltas and `response.completed` yields `TokenDelta`s only for the final
   answer. Fails today: every delta is yielded.
3. Phased output with no final_answer gives empty `text`; output with no phase anywhere is joined
   as before; `provider_items` keeps every item in both cases.
4. `output_shape` lists each item's type, phase and text length and contains no text.

Then in `test_agent_turn_loop.py` (fakes in `turn_engine_fakes.py`): a turn whose fake provider
returns an `output_shape` writes it on `agent.turn_finished`, both for a posted reply and for an
`empty_reply` skip; a provider without one leaves the row without the key.

## 9. Risks and Rollback

A model that marks its whole answer `commentary` would go silent; Q-2 accepts that, and the
shape record makes it visible in the audit log. If OpenAI renames the phase values, the filter
falls back to joining nothing when no `final_answer` is present, which shows as a run of
`empty_reply` turns rather than a leak. Rollback is a code revert; nothing persisted changes
shape except audit metadata, which gains one optional key.

## 10. Acceptance Criteria

- [ ] AC-1: Regression tests 1 and 2 of Section 8 fail before the fix and pass after.
- [ ] AC-2: Phased output without a final_answer yields an empty reply, unphased output is
  unchanged, and `provider_items` is unchanged in both (Section 8 test 3).
- [ ] AC-3: `agent.turn_finished` carries `output_shape` for an OpenAI turn on the posted,
  skipped, observer and A2A paths, with no message text in it; turns on other providers do not
  carry the key.
- [ ] AC-4: On staging, after deploy, three CRAT autonomous runs post no agent message containing
  planning text, and the audit rows of their teacher turns show `output_shape`; the D-n entry
  records whether any response contained a commentary item.

## 11. SRS Delta

Add after [R13.79]:

> - **[R13.80]** **Final answer only.** An agent's reply, whether streamed or stored, consists
>   only of the provider's final answer. Intermediate commentary a provider marks as such, and
>   reasoning, are never shown in a room. The structure of the provider's final output (item
>   kinds, their marking, and text lengths, never their text) is recorded on the turn's audit
>   entry when the provider reports it.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** If a recurrence shows `output_shape` with a single final_answer message, the planning
  text was written inside the answer; a prompt rule or a content guard then becomes the question.
- **FU-2.** Gemini: skip parts with `thought: true` in both paths, so enabling thought summaries
  later cannot leak them.
- **FU-3.** OpenAI-compatible: decide whether to read `reasoning_content` separately and strip
  `<think>` blocks or harmony channels from `content`.
- **FU-4.** OpenAI: label replayed earlier-turn assistant messages `phase: "final_answer"`, as
  OpenAI's guidance asks for newer models.
