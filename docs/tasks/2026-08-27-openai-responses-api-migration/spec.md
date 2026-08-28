---
type: feature
status: implemented
created: 2026-08-27
requirements: [R7.08]
depends_on: [2026-08-27-provider-model-capability-table]
---

# OpenAI adapter: assess and migrate to the Responses API

## 1. Summary

The OpenAI adapter calls `POST /v1/chat/completions`
(`contexts/keys/infrastructure/adapters/openai.py:25`). OpenAI's own reasoning guide states that
"Reasoning models work better with the Responses API. While the Chat Completions API is still
supported, you'll get improved model intelligence and performance by using Responses", and the
current model documentation presents the gpt-5.6 family on the Responses API. More concretely, the
endpoint refuses a combination SMAP needs: from gpt-5.4 onwards, `reasoning_effort` may not be
sent alongside function tools on Chat Completions, and the error text names `/v1/responses` as the
remedy. Every SMAP agent turn sends tools, so on Chat Completions the platform can offer either
reasoning effort or agent tools, never both.

`2026-08-27-provider-model-capability-table` makes that limitation honest: the effort control is
disabled where it cannot apply. This dossier is about removing the limitation.

The deliverable is an assessment first and a migration second. §5 must reach a recommendation the
user approves before any adapter code is written, because the change touches the request shaping,
the response normalisation and the streaming path of the one adapter that serves the platform's
default provider.

## 2. Goals and Non-goals

**Goals**

- A written assessment of migrating `OpenAIAdapter` to `/v1/responses`: what changes in request
  shaping, response normalisation, streaming, and usage accounting, and what the migration cannot
  preserve.
- A recommendation with a stated alternative, including "do not migrate" as a real option.
- If migration is approved: reasoning effort and function tools compose on gpt-5.4 and later, and
  the models documented only on the Responses API become reachable.
- No regression in the router contract: the adapter keeps satisfying `ProviderAdapter` and
  `StreamingAdapter` as `provider_router.py` consumes them, including the `StreamComplete` terminal
  event and token accounting.

**Non-goals**

- The Anthropic and Gemini adapters. Their endpoints are current
  (`adapters/anthropic.py:24`, `adapters/gemini.py`).
- The capability table itself; that is the dependency.
- The embedding path. `/v1/embeddings` (`openai.py:26`) is unaffected and stays where it is.
- Supporting both endpoints behind a per-agent switch. If the assessment recommends migration, it
  is a replacement; a dual path doubles the surface that has to be tested and is the shape that
  makes streaming bugs hard to attribute.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Assess-only, or spec the migration outright? | Assess first, with the assessment as an approval gate inside this dossier. | The Responses API changes request shape, response shape and the SSE event vocabulary at once, on the adapter serving the default provider. Committing to it before the shapes are read is how a rewrite acquires a long tail. |
| Q-2 | Why does this depend on the capability table rather than running in parallel? | Logical prerequisite. | The migration's value is model-specific (which models are Responses-only, which accept which effort values), and the table is where that is expressed. Building this first would encode the same facts in a second place, which is the condition the table exists to end. Both dossiers also edit `openai.py`'s request builder. |
| Q-3 | Which of §5's three options is adopted? (AC-2) | **Option A: migrate to `/v1/responses`**, with §5's four conditions binding. Approved by the user 2026-08-28. | Reasoning effort and function tools compose, which is the capability the platform cannot offer today on its default provider. The assessment also found the migration is net-simplifying rather than net-complicating: two of `_chat_body`'s four conditional branches disappear, and `response.completed` carries the whole response object so the streaming and non-streaming normalisation paths collapse into one. Option C was rejected on its own premise, since streaming is the half that gets easier. |
| Q-4 | How is §5.6's reasoning-item continuity gap handled? | **Closed in this task**, with an opaque passthrough field on the neutral assistant message plus `include: ["reasoning.encrypted_content"]`. Approved by the user 2026-08-28. | The alternative was an FU recording an accepted loss. The loss is a quality regression on every multi-round tool turn, and no test in this repo can see a quality regression, so deferring it means deferring it indefinitely and never learning whether it mattered. The field is opaque by construction: the turn engine copies it without reading it, and the two other adapters ignore a key they do not know. |
| Q-5 | §5.7 and §10 both require a real key against the real endpoint. Is one available? | **No, not this session.** Build to the point of verifiability; leave the live-only criteria unticked. Decided by the user 2026-08-28. | Mirrors the treatment the capability-table dossier gave its own AC-6/AC-11/AC-15 rather than inventing a second convention. The affected criteria are named individually in §11 so that a later session knows exactly what is outstanding, and §5.7 item 1 (strict mode) is called out as the most likely cause of a migration that is green in `respx` and fails on the first real turn. |

## 4. Current State

`OpenAIAdapter` (`adapters/openai.py`) implements two protocol methods the router calls:

- `invoke` (`:202-206`) dispatching to `_chat` (`:208-223`) or `_embed` (`:225-242`).
- `stream` (`:244` onward), consuming SSE through `base.iter_sse_lines` (`base.py:113`) and
  accumulating text, tool-call fragments, finish reason and usage into a terminal `StreamComplete`.

The request is built by `_chat_body` (`:147` onward), which already carries four model-conditional
branches: `max_tokens` versus `max_completion_tokens`, suppression of `temperature`, `top_p` and
`seed` on reasoning models, and (as of `e16bc90`) suppression of `reasoning_effort` when tools are
present on gpt-5.4 and later. `_tools` (`:130-144`) maps the platform's neutral tool shape onto
Chat Completions' `{"type": "function", "function": {...}}` envelope. `_normalise_message`
(`:180` onward) maps a Chat Completions choice back onto the neutral `{text, tool_calls,
finish_reason}` body the turn engine consumes.

The router's contract with any adapter is narrow and is what constrains this work:
`ProviderCallResult(http_status, body, input_tokens, output_tokens)` from `invoke`, and for
`stream`, a sequence of `TokenDelta` ending in exactly one `StreamComplete`
(`provider_router.py:200-213` drives it and stashes the terminal on `_StreamTerminal`).
`_stream_member` distinguishes a failure before the first token (rotate to the next key) from one
after it (`ProviderStreamError`, not replayable), so any change to when the first `TokenDelta` is
emitted changes rotation behaviour.

Error classification is shared and must keep working: `base.scrub_error` (`base.py:75-81`) reads
`error.type`/`code`/`param` from the body, and `router_policy.classify_http` (`:36-57`) turns
400/404/422 into `ABORT`. A Responses API error body that does not carry the same envelope would
silently lose the diagnosis added in `1d9a3da`.

## 5. Assessment

Completed 2026-08-28. Sources are OpenAI's current developer documentation at
`developers.openai.com` and the generated request/response models in `openai/openai-python@main`,
which are produced from the same OpenAPI specification that defines the endpoint. Where a claim
rests on the generated models rather than on prose documentation, the file is named. Claims that
could not be settled from documentation alone are listed in §5.7 and are the content of the
live-key verification §10 already requires.

### 5.1 Request shape

The endpoint is `POST https://api.openai.com/v1/responses`. The structural change is that Chat
Completions thinks in messages and Responses thinks in items: `messages` becomes `input`, an array
whose members are typed Items rather than uniformly role-bearing messages. A role message Item
keeps the familiar `{"role": ..., "content": ...}` shape, and `content` may be a plain string or a
list of typed content parts, so the ordinary user and assistant turns SMAP sends translate
one-for-one.

The system prompt has a dedicated top-level `instructions` field, and a `role: "system"` message
inside `input` is also accepted for transcript preservation
(`https://developers.openai.com/api/docs/guides/migrate-to-responses`). Either satisfies
`_messages`' first branch (`openai.py:68-70`).

Content parts are renamed and flattened. `{"type": "text"}` becomes `{"type": "input_text"}`;
`{"type": "image_url", "image_url": {"url": ...}}` becomes `{"type": "input_image", "image_url":
"<data URL>"}` with `image_url` a plain string rather than an object
(`types/responses/response_input_image_param.py`); the Chat Completions `{"type": "file", "file":
{"filename", "file_data"}}` becomes `{"type": "input_file", "filename": ..., "file_data": ...}`
with the wrapper removed (`types/responses/response_input_file_param.py`). Both still accept a
base64 data URL, so `_content_parts` (`openai.py:34-59`) changes key names and nesting and nothing
else.

Tools use internal rather than external tagging: `{"type": "function", "name": ..., "description":
..., "parameters": {...}}`, with the `function` wrapper gone
(`https://developers.openai.com/api/docs/guides/migrate-to-responses`). `_tools`
(`openai.py:107-121`) loses one level of nesting.

The tool round-trip is where the item model actually bites. An assistant tool-use turn is not one
message carrying a `tool_calls` array; it is one `function_call` Item per call, and each result is
a separate top-level `function_call_output` Item rather than a `role: "tool"` message. The two are
linked by `call_id`, not by the item's own `id`
(`types/responses/response_function_tool_call.py`, `types/responses/response_input_param.py`). The
neutral message shape the turn engine builds (`turn_engine.py:4010` for the assistant turn,
`:4026-4034` for each result) is unchanged; the adapter expands one neutral message into several
Items.

Reasoning effort is `reasoning.effort`, a nested object rather than a top-level parameter, and its
documented value set is `none`, `minimal`, `low`, `medium`, `high`, `xhigh` and `max`, with which
values a given model accepts stated as model-dependent
(`https://developers.openai.com/api/docs/guides/reasoning`). That is exactly the seven-value enum
migration 0083 widened `agent_effort` to, so the capability table's `effort_values` column already
has the right vocabulary to express per-model acceptance. No conflict between `reasoning.effort`
and function tools is documented; the reasoning guide's function-calling section treats the two as
a normal combination, which is the entire reason this dossier exists.

The output ceiling is `max_output_tokens`, one name for every model
(`types/responses/response_create_params.py`).

`temperature` and `top_p` exist. **`seed` does not exist on the Responses API** — it is absent from
`ResponseCreateParamsBase` (`types/responses/response_create_params.py`). §5.6 covers what that
costs, which turns out to be nothing.

Streaming is `stream: true`. `stream_options` exists, but usage arrives on the terminal
`response.completed` event's response object regardless, so the `{"include_usage": true}` companion
that `openai.py:154` sends today has no counterpart to carry over.

One new parameter has to be sent rather than merely translated. `store` defaults to true on
Responses, which means OpenAI retains the response for at least 30 days, whereas Chat Completions
retains no application state by default
(`https://developers.openai.com/api/docs/guides/your-data`). A migration that does not send
`"store": false` silently changes the data-handling posture of every OpenAI agent turn on a
self-hosted platform. §8 treats this as a constraint, not a preference.

`_chat_body`'s four model-conditional branches (`openai.py:124-155`) fare as follows.

| Branch | Flag | Fate |
|---|---|---|
| `max_tokens` versus `max_completion_tokens` | `uses_completion_token_field` | Gone. `max_output_tokens` is unconditional, so the flag becomes dead for the OpenAI rows. |
| Suppress `temperature`/`top_p`/`seed` | `accepts_sampling` | Survives for `temperature` and `top_p`. `seed` has no target and is dropped. |
| Effort gated on membership in `effort_values` | `accepts_effort`, `effort_values` | Survives, relocated to `reasoning.effort`. `CapabilityFlags.forwardable_effort` (`base.py:153-165`) is unchanged. |
| Suppress effort when tools are present | `effort_conflicts_with_tools` | The condition it encodes does not exist on this endpoint, so the flag becomes dead for the OpenAI rows. |

Two of the six capability fields therefore stop being consumed by this adapter. Neither can simply
be deleted: `uses_completion_token_field` is OpenAI-only and would become an unread column, and
`effort_conflicts_with_tools` is likewise. §6 decides whether they are removed from the table or
retained as documented-dead; removing them is a second edit to a file this dossier's dependency
just rewrote, so it is a decision rather than a cleanup.

### 5.2 Response shape

A 200 response is a single `response` object, not a `choices` array. Its fields include `status`,
`output`, `usage`, `error` and `incomplete_details` (`types/responses/response.py`). `status` is one
of `completed`, `failed`, `in_progress`, `cancelled`, `queued` or `incomplete`.

The neutral body is recovered as follows. `text` is the concatenation of `content[].text` over
content parts of type `output_text` on output items of type `message`. `tool_calls` are the output
items of type `function_call`, whose `name` and `arguments` (a JSON string, as today) map directly,
and whose `call_id` — not `id` — is the value that must become the neutral `id`, because `call_id`
is what the next request has to echo on the matching `function_call_output`.

There is no `finish_reason` field and no Chat Completions finish-reason vocabulary. The nearest
equivalent is `status` together with `incomplete_details.reason`, whose documented values are
`max_output_tokens` and `content_filter` (`types/responses/response.py`). This is the one place a
change reaches outside the adapter. `is_truncated_finish_reason` (`provider_router.py:144-151`)
matches the frozen set `{"max_tokens", "length"}`, and the comment above it
(`provider_router.py:137-140`) states the design deliberately: each provider's raw value is passed
through verbatim so that logs and usage rows keep it, and the normalisation lives in the router.
Honouring that design means the adapter emits `max_output_tokens` and `_TRUNCATED_FINISH_REASONS`
gains that member, rather than the adapter quietly rewriting it to `length`. The only runtime
consumer is `turn_engine.py:3997`; `test_provider_adapters.py:163` and `:182` assert on the OpenAI
adapter's own output and on the predicate respectively, so both continue to hold under that
mapping.

### 5.3 Streaming

The stream is SSE carrying both an `event:` line naming the event type and a `data:` line whose
JSON payload repeats that type in its own `type` field. `base.iter_sse_lines` (`base.py:186-202`)
reads only `data:` lines and hands the raw JSON to the caller, so it works unchanged.

There is no `[DONE]` sentinel. The stream ends with `response.completed` (or `response.incomplete`
/ `response.failed`) and the body then closes
(`https://developers.openai.com/api/docs/guides/streaming-responses`). `iter_sse_lines`' `[DONE]`
check simply never fires and the iteration ends on EOF, so this too needs no change, but it is the
kind of difference that is invisible until a fake emits a sentinel the real endpoint does not.

The event vocabulary is large (58 members in `types/responses/response_stream_event.py`) and almost
all of it concerns hosted tools SMAP does not use. The events this adapter needs are:

| Event `type` | Role |
|---|---|
| `response.created`, `response.in_progress` | Lifecycle only. |
| `response.output_item.added` | Announces a new output item. For a `function_call` item this is where `call_id` and `name` arrive. |
| `response.output_text.delta` | The token-bearing event. Fields `delta`, `item_id`, `output_index`, `content_index`, `sequence_number` (`types/responses/response_text_delta_event.py`). |
| `response.function_call_arguments.delta` | Argument fragments. Fields `delta`, `item_id`, `output_index`, `sequence_number` (`types/responses/response_function_call_arguments_delta_event.py`). Carries neither name nor `call_id`. |
| `response.output_item.done` | Carries the completed item whole. |
| `response.completed` / `response.incomplete` / `response.failed` | Terminal. Each carries the full `response` object, including `usage` and `status`. |
| `error` | Top-level error event; fields `code`, `message`, `param`, `sequence_number` (`types/responses/response_error_event.py`). |

Three consequences matter.

First, the first token-bearing event is `response.output_text.delta`, and on a reasoning model the
items preceding it are reasoning items. Time to first `TokenDelta` is therefore no worse than on
Chat Completions, where the same thinking happens before the first content delta, and no better.
Because `_stream_member` (`provider_router.py:503`) distinguishes a failure before the first token
from one after it, and `TokenDelta` continues to be emitted only on a text delta, the
rotate-versus-abort boundary keeps precisely the meaning it has today.

Second, tool-call reassembly changes shape. The current accumulator keys on the Chat Completions
`tool_calls[].index` and picks up `id` and `function.name` from whichever fragment carries them
(`openai.py:268-277`). On Responses, `output_index` is the key, and `call_id` and `name` come from
`response.output_item.added` rather than from any delta. Equivalently — and this is the better
option — `response.output_item.done` delivers each `function_call` item complete, so the adapter can
read tool calls whole and stop reassembling fragments at all. Similarly `response.completed` carries
the entire final response object, so the terminal `StreamComplete` body can be built by the same
function that builds the non-streaming body. That collapses the two normalisation paths §10 warns
about into one, which is a reduction in the surface that can drift, not an increase.

Third, a mid-stream failure under HTTP 200 arrives in one of two shapes: the top-level `error`
event, or `response.failed`, whose `response.error` is `{code, message}` with a closed `code`
vocabulary including `server_error` and `rate_limit_exceeded`
(`types/responses/response_error.py`). Today's mapping (`openai.py:246-248`) is a substring test
against `"rate_limit"`. The replacement should be an explicit table in the shape of
`anthropic.py:29-32`, mapping `rate_limit_exceeded` to 429 and everything else to 500, so the
router's classification stays as sharp as it is now. `response.incomplete` is not an error: it is
truncation, and belongs on the §5.2 path. `scrub_stream_error` (`base.py:105-114`) takes only a
status and a kind and never the message, so nothing new can leak through it.

### 5.4 Usage accounting

`usage.input_tokens`, `usage.output_tokens` and `usage.total_tokens`, with
`usage.input_tokens_details.{cached_tokens, cache_write_tokens}` and
`usage.output_tokens_details.reasoning_tokens` (`types/responses/response_usage.py`). The mapping is
`prompt_tokens` to `input_tokens` and `completion_tokens` to `output_tokens`, and nothing else
changes for `record_call` or `record_usage_event`, which take exactly those two numbers on
`ProviderCallResult`.

Reasoning tokens are reported separately but are billed as output tokens and are already counted
inside `output_tokens` (`https://developers.openai.com/api/docs/guides/reasoning`). Recording them
as an additional quantity would double-count the user's spend, so this task does not.

### 5.5 Error envelope

HTTP non-2xx responses keep the API-wide envelope `{"error": {"message", "type", "param", "code"}}`;
the error-codes guide directs the reader to `error.code` and `error.type` without qualifying it by
endpoint (`https://developers.openai.com/api/docs/guides/error-codes`). `scrub_error`
(`base.py:96-102`) and `summarise_http_failure` (`probes/base.py:107-139`) therefore continue to
work unchanged, and the `provider_detail` chain added in `1d9a3da` keeps naming causes. This is the
weakest-sourced claim in the assessment — it rests on the guide's prose rather than on a per-endpoint
schema — so §5.7 carries it into the live check.

Note that the in-body `ResponseError` used by `response.failed` is a different, narrower object:
`code` and `message` only, no `type` and no `param`. That is why §5.3 specifies a code table rather
than reusing the HTTP-error path for in-stream failures.

### 5.6 What is lost

**`seed`, nominally.** The Responses API has no equivalent, and `agents.seed` is a real user-facing
column (`agents/infrastructure/tables.py:61`, forwarded at `turn_engine.py:177`). In practice the
loss is zero: `seed` is forwarded only when `accepts_sampling` is true (`openai.py:133-140`), and
every catalogued OpenAI row sets `accepts_sampling=False` (`model_specs.py:110-184` — `gpt-5.5`,
`gpt-5.4`, `gpt-5.4-mini`, `o3`, `o3-mini`), as does Q-2's floor for an uncatalogued id
(`model_specs.py:265-282`). No OpenAI model reachable today can receive a `seed`, so no behaviour
changes. `temperature` and `top_p` are in the same position but survive anyway.

**Reasoning-item continuity across tool rounds.** This is the real one. The Responses documentation
requires that every item of a response's `output` array be replayed in the next request when doing
function calling with a reasoning model, including the encrypted reasoning items the API returns —
under `store: false` those travel as `include: ["reasoning.encrypted_content"]`
(`types/responses/response_includable.py`,
`https://developers.openai.com/api/docs/guides/conversation-state`). SMAP's neutral message shape
carries `{id, name, arguments}` per tool call and nothing else (`turn_engine.py:4010`), so a
straight migration has nowhere to put an opaque provider item and the reasoning context is dropped
between tool rounds. The consequence is quality degradation on multi-round tool turns, not an error
and not a failed request. Closing it requires an opaque passthrough field on the neutral assistant
message that only this adapter writes and reads. §6 must decide whether that is in scope; it is a
change to a cross-context contract, not to one file.

**Nothing else.** Every shape SMAP actually sends — system prompt, user and assistant text turns,
image and PDF attachment blocks, function tools, tool results, effort, output ceiling, streaming,
usage — has a documented Responses equivalent.

### 5.7 What documentation could not settle

These are the content of the live-key verification §10 requires, not open design questions.

1. Whether function tools default to strict mode on Responses. The migration guide says Responses
   defaults to strict and to set `strict` explicitly. SMAP's tool schemas are agent-authored
   arbitrary JSON Schema, and strict mode imposes structural requirements on them, so if the
   default is strict the adapter must send `"strict": false` or agent tools will be rejected. This
   is the single most likely cause of a migration that passes every `respx` test and fails on the
   first real turn.
2. That the HTTP error envelope on `/v1/responses` really carries `type` and `param` and not only
   `code` and `message` (§5.5).
2a. Whether a non-reasoning model accepts `include: ["reasoning.encrypted_content"]` or rejects it.
   The adapter sends it unconditionally, which is the lower-risk of the two unverifiable options:
   gating it on `accepts_effort` would mean replaying a reasoning item whose content was never
   requested, and a contentless reasoning item on the input side is the failure that cannot be
   recovered from. Added during implementation, not present in the assessment as first written.
3. The capability table's own FU-12, deferred on 2026-08-28: which effort values each catalogued
   OpenAI model actually accepts. On this endpoint the question changes shape — `none` becomes a
   sendable value rather than a workaround for the tools conflict — and the same live session
   answers both, which is why BOARD.md suggested folding them together.

### Options weighed

**Option A: migrate.** Reasoning effort and function tools compose, which is the capability the
platform cannot offer today. `_chat_body` loses two of its four conditional branches. The streaming
and non-streaming normalisation paths collapse into one, because `response.completed` carries the
same object the non-streaming call returns. The platform moves onto the endpoint OpenAI documents
as primary for reasoning models. Costs: one adapter rewritten, `_TRUNCATED_FINISH_REASONS` gains a
member, `store: false` becomes a thing that must never be dropped, and reasoning-item continuity is
either accepted as lost or paid for with a change to the neutral message contract.

**Option B: stay.** No rewrite risk. The platform permanently cannot offer reasoning effort to an
OpenAI agent that has tools, which is every agent, on the platform's default provider. The
capability table makes that honest in the UI but does not make it less of a hole. It also leaves
SMAP on the endpoint OpenAI describes as the one you get less from.

**Option C: migrate the non-streaming path only.** Rejected on its own terms. §5.3 shows the
streaming path is the *easier* half after migration, not the harder one, because
`response.completed` hands back the whole response object and removes the bespoke reassembly that
exists today. Splitting the endpoints would leave two request builders for one provider in order to
avoid the part that got simpler.

### Recommendation

**Option A, with four conditions**, each of which is a §6 obligation rather than an aspiration:

1. `"store": false` on every request. It is the one parameter whose omission changes data handling
   silently, on a platform whose users bring their own keys and whose example course carries
   13-year-olds' accounts of distressing events.
2. The strict-mode question (§5.7 item 1) is settled against the real endpoint **before** the
   adapter is called done, not after. A `respx` fake cannot see it.
3. The in-stream error mapping is an explicit code table in the shape of `anthropic.py:29-32`, not
   a substring test, so the rotate-versus-abort boundary is inspectable.
4. The reasoning-item continuity gap (§5.6) is either closed with an opaque passthrough field on
   the neutral message, or recorded as an accepted, documented loss with an FU. It is not left
   unmentioned, because a silent quality regression on multi-round tool turns is precisely the kind
   of thing no test in this repo would catch.

The two capability fields that go dead (`uses_completion_token_field`,
`effort_conflicts_with_tools`) are left in place and documented as dead for the OpenAI rows, rather
than removed, so this task does not re-edit the table its dependency just landed.

## 6. Detailed Changes

Written against Q-3's approved recommendation. Every change is in the backend; there is no
migration, no API contract change and no frontend work.

### 6.1 `contexts/keys/infrastructure/adapters/openai.py` — the rewrite

The module keeps its two capabilities and its dispatch (`invoke` at `:179-184`). `_EMBED_URL` and
`_embed` are untouched per §2. `_CHAT_URL` becomes `https://api.openai.com/v1/responses`.

**Request building.** `_chat_body` becomes `_responses_body`, producing:

- `model` from `base.resolve_model`, unchanged.
- `instructions` from `payload["system"]` when set, replacing the leading system message
  (`:68-70`). The `input` array then carries only conversation turns.
- `input` from `_input_items(payload)`, replacing `_messages`. Per §5.1: a user or assistant turn
  becomes a role message Item; an assistant turn carrying `tool_calls` becomes its
  `provider_items` verbatim when present (§6.2) and otherwise one text message Item plus one
  `function_call` Item per call; a `role: "tool"` message becomes a top-level
  `function_call_output` Item keyed on `call_id`.
- `_content_parts` keeps its structure and changes part types to `input_text` / `input_image` /
  `input_file` per §5.1. The text-only-model note (`_attachment_note`, `:28-31`) is unchanged apart
  from its part type.
- `tools` from `_tools`, flattened to internal tagging, with `"strict": False` set explicitly per
  §5.7 item 1. The reason goes in a comment: SMAP's tool schemas are agent-authored arbitrary JSON
  Schema and strict mode constrains their shape.
- `max_output_tokens` from `payload["max_tokens"]`, unconditionally. The
  `uses_completion_token_field` branch goes away.
- `temperature` and `top_p` under `caps.accepts_sampling`, unchanged in meaning. `seed` is dropped
  with a comment recording that the Responses API has no equivalent and that no catalogued OpenAI
  model could receive one anyway (§5.6).
- `reasoning: {"effort": ...}` from `caps.forwardable_effort(payload.get("effort"))`. The gate
  itself (`base.py:153-165`) is not touched.
- `store: False`, unconditionally, per Q-3 condition 1.
- `include: ["reasoning.encrypted_content"]`, per Q-4.
- `stream: True` when streaming. `stream_options` is not sent (§5.1).

Each conditional keeps a comment naming the failure it prevents, per §9.

**Non-streaming normalisation.** `_normalise_message` becomes `_normalise_response(data)`, taking
the whole response object and walking `output` once per §5.2: `output_text` parts of `message`
items concatenate into `text`; `function_call` items become `{"id": call_id, "name", "arguments"}`;
`finish_reason` is `incomplete_details.reason` when `status == "incomplete"` and `status`
otherwise. Usage reads `usage.input_tokens` / `usage.output_tokens`.

**Streaming.** `stream` keeps its shape and its use of `base.iter_sse_lines`, and switches to the
event vocabulary in §5.3. Because `response.completed` carries the full response object, the
terminal `StreamComplete` body is built by handing that object to the same `_normalise_response`
the non-streaming path uses, which is the divergence §10 warns about being removed rather than
doubled. `TokenDelta` is emitted on `response.output_text.delta` only, preserving the
rotate-versus-abort boundary. `response.incomplete` is terminal and not an error; it goes through
`_normalise_response` too. Tool-call fragment accumulation and `_safe_json` are deleted: tool calls
come off the terminal response object whole.

A module-level `_STREAM_ERROR_STATUS` table in the shape of `anthropic.py:29-32` maps the
`ResponseError` code vocabulary onto a synthetic HTTP status (`rate_limit_exceeded` to 429,
everything else to 500), consumed by both the top-level `error` event and `response.failed`, per
Q-3 condition 3. `base.scrub_stream_error` is unchanged and remains the only path out.

### 6.2 The opaque passthrough (Q-4)

Three small edits, none of which teaches a non-OpenAI layer anything about OpenAI.

1. `openai.py`'s `_normalise_response` puts the response's `output` array on the neutral body under
   `provider_items`, alongside `text` / `tool_calls` / `finish_reason`.
2. `turn_engine.py:4010` copies it onto the assistant message it appends, without reading it. The
   final no-tools synthesis pass (`:4064-4080`) already rebuilds its messages field by field, so
   the key is dropped there by construction.
3. `openai.py`'s `_input_items` prefers `provider_items` over synthesising `function_call` items
   when the assistant message carries it.

The key never reaches PostgreSQL: `ProviderCallResult.body` is not persisted by `record_call`
(`provider_router.py:288-309`), and `_history_message` (`turn_engine.py:3674-3686`) carries no
tool-call data at all. `base.py`'s canonical-payload docstring gains the field so the contract is
written down where the other adapters' authors will read it.

### 6.3 `contexts/keys/application/provider_router.py`

`_TRUNCATED_FINISH_REASONS` (`:141`) gains `"max_output_tokens"`, and the comment above it gains
the Responses API to its list of provider spellings. This is the only change outside the adapter
and the passthrough, and it exists because the file's stated design is to pass each provider's raw
value through verbatim and normalise in one place (§5.2).

### 6.4 `contexts/agents/domain/model_specs.py`

Comment-only. The two capability fields that go dead for the OpenAI rows
(`uses_completion_token_field`, `effort_conflicts_with_tools`) are annotated as such rather than
removed, per §5.1's closing paragraph and Q-3. Values are not changed: they still describe Chat
Completions accurately, and the Anthropic and Gemini rows still consume the same dataclass.

## 7. NFR Checklist

- [ ] i18n: N/A unless new user-facing error copy is needed; the room's copy is keyed on error
      kinds that do not change.
- [ ] Audit log: no new event. `provider_detail` on `agent.turn_failed` (`1d9a3da`) must keep being
      populated, which §5 item 5 governs.
- [ ] Tenant isolation: N/A. No endpoint changes.
- [ ] Error handling UX: the rotate-versus-abort distinction is the one to preserve; a
      misclassified error either burns a key group or hammers a provider.
- [ ] Performance: streaming latency to first token is the metric that matters, and §5 item 3 is
      where it is established.

## 8. Security Considerations

The adapter handles decrypted provider keys. Constraints that must survive any rewrite:

- The secret reaches the adapter as a parameter and goes into a header (`_headers`, `openai.py:46`
  region). It is never logged, never placed in a URL or query string, and never echoed into a
  normalised body. `test_provider_adapters.py` asserts the last of these on every adapter test.
- `scrub_error` must remain the only path from a provider error body into anything persisted or
  displayed. Providers reflect masked key material in `error.message`, which is why that field is
  excluded and why `1d9a3da` restricts `type`/`code`/`param` to an identifier shape.
- Tool schemas are agent-authored and reach the provider. Any change to `_tools` must not begin
  interpolating them anywhere other than the JSON body.
- **New with this endpoint (§5.1): `store` defaults to true.** Omitting it means OpenAI retains
  every turn's content for at least 30 days under the user's own account, where Chat Completions
  retained nothing by default. On a BYO-key platform whose example course carries 13-year-olds'
  accounts of distressing events, that is a change in data handling that must not happen by
  omission. `"store": false` is unconditional and is AC-3.
- The Q-4 passthrough carries provider-opaque encrypted reasoning content. It stays in memory for
  the duration of one turn's tool loop: `ProviderCallResult.body` is not persisted
  (`provider_router.py:288-309`) and the room history never carried tool-call data
  (`turn_engine.py:3674-3686`). It must not be logged, and it must not reach a provider other than
  the one that issued it — which it cannot, since the message list is built per turn for one agent
  and one provider.

## 9. Quality Notes

**Existing debt in touched files**

- `openai.py` carries model-family knowledge as regexes. The dependency removes them; this task
  must not reintroduce any.
- `_chat_body`'s conditional branches are individually commented with the failure each prevents.
  That comment style is the reason the gpt-5.4 incident was diagnosable at all, and it should
  survive into whatever replaces the function.

**Patterns to follow**

- `adapters/base.py` holds what is shared across adapters (`new_client`, `iter_sse_lines`,
  `resolve_model`, `scrub_error`). Anything the Responses path needs that another provider will
  also need belongs there, not in `openai.py`.
- `adapters/anthropic.py`'s stream handler is the in-repo exemplar of mapping a provider-specific
  event vocabulary onto the neutral `TokenDelta`/`StreamComplete` pair, including its
  `_STREAM_ERROR_STATUS` table (`:40-43`) for in-stream errors delivered under HTTP 200. The
  Responses path needs the same construct.

**Reuse inventory**

- `base.new_client` / `base.new_client(stream=True)`, `base.iter_sse_lines`, `base.resolve_model`,
  `base.scrub_error`, `base.scrub_stream_error`.
- `_safe_json` and the tool-fragment accumulation already in `openai.py`, if the fragment shape is
  unchanged.
- `test_provider_adapters.py`'s `_chat`, `_sse` and `_ok_chat_route` helpers.

## 10. Risks and Rollback

- **This is the default provider's adapter.** A streaming regression is visible on every OpenAI
  agent turn, and the unit tier drives adapters with `respx` fakes, so a shape mismatch against the
  real API is exactly what it cannot see.
- **The rotate-versus-abort boundary is subtle and load-bearing.** Getting it wrong in the
  permissive direction replays a bad request across every key in a group.
- **`fake_provider.py` cannot produce an agent turn** (recorded in
  `2026-08-24-agent-readable-live-drafts` §17), so end-to-end verification of this change requires a
  real key against a real endpoint. That has to be planned, not discovered at the end.

Rollback: the adapter is one file behind a protocol the router owns, so reverting the commit
restores the previous endpoint with no data or schema implications.

## 11. Acceptance Criteria

- [x] AC-1: §5 is complete, every claim cited to current OpenAI documentation, with a
      recommendation and the option it was chosen over.
- [x] AC-2: the user has approved the recommendation, recorded as a Q-n row.
- [x] AC-3: the adapter posts to `/v1/responses`, and a chat request carries `instructions`,
      `input`, flattened `tools`, `max_output_tokens`, `store: false` and
      `include: ["reasoning.encrypted_content"]`. `/v1/embeddings` is untouched.
- [x] AC-4: `reasoning.effort` and `tools` are sent **together** for a model whose spec lists the
      requested effort value. This is the capability the task exists to deliver, so it is asserted
      on the request body of a call that carries tools.
- [x] AC-5: `forwardable_effort`'s gate still holds on the new shape — an effort value not in the
      model's `effort_values` omits `reasoning` entirely, and Q-2's floor (an uncatalogued id)
      sends no `reasoning`, no `temperature` and no `top_p`.
- [x] AC-6: a neutral history containing a system prompt, user and assistant text turns, an
      assistant tool-use turn and its tool results round-trips into the correct `input` item
      sequence, with `function_call_output` keyed on the same `call_id` the `function_call`
      carried.
- [x] AC-7: attachment blocks map to `input_text` / `input_image` / `input_file`, and a
      non-vision model gets `_attachment_note` instead.
- [x] AC-8: a non-streaming 200 response normalises to `{text, tool_calls, finish_reason}` with
      `tool_calls[].id` taken from `call_id`, and usage read from
      `usage.input_tokens`/`usage.output_tokens`.
- [x] AC-9: `status: "incomplete"` with `incomplete_details.reason == "max_output_tokens"`
      normalises to that `finish_reason`, and `is_truncated_finish_reason` returns True for it.
      The existing Chat Completions vocabulary (`length`, `max_tokens`) still returns True and
      `tool_calls`/`tool_use` still return False.
- [x] AC-10: a streamed turn emits one `TokenDelta` per `response.output_text.delta` and exactly
      one terminal `StreamComplete`, whose body and token counts are built from
      `response.completed`'s response object. No `[DONE]` sentinel is required to terminate.
- [x] AC-11: a streamed turn containing a `function_call` reassembles it correctly from the
      terminal response object, including a tool call whose arguments were delivered across
      several `response.function_call_arguments.delta` events.
- [x] AC-12: an in-stream failure under HTTP 200 maps through `_STREAM_ERROR_STATUS` —
      `rate_limit_exceeded` to 429, an unknown code to 500 — for both the top-level `error` event
      and `response.failed`, and the resulting body is `scrub_stream_error` output only. Verified
      for both, not one.
- [x] AC-13: a non-2xx HTTP response still yields `scrub_error` output and no adapter test finds
      the secret anywhere in a normalised body, on every path (`invoke` chat, embed, stream, and
      each error path).
- [x] AC-14: the Q-4 passthrough round-trips: a response's `output` array reaches the neutral body
      as `provider_items`, the turn engine copies it onto the assistant message it appends, and the
      next request's `input` carries those items verbatim rather than synthesised ones. The
      synthesis pass drops the key.
- [ ] AC-15 (live key required, Q-5): a real turn against `api.openai.com/v1/responses` with a real
      key succeeds with tools and `reasoning.effort` sent together, confirming §5.7 item 1 (whether
      `"strict": false` is required) and §5.7 item 2 (that the HTTP error envelope carries `type`
      and `param`). **Expected to close unticked this session.**
- [ ] AC-16 (live key required, Q-5): the capability table's FU-12 answered in the same session —
      which effort values each catalogued OpenAI model accepts on this endpoint, `none` included.
      **Expected to close unticked this session.**
- [x] AC-17: FU-2 closed — `scrub_stream_error` filters its `kind` through `safe_ident`, so an
      in-stream error code that is not identifier-shaped is dropped whole. Asserted on the OpenAI
      path (four non-conforming shapes, including a masked-key sentence) and on the Anthropic path,
      since the filter lives in `adapters/base.py` and reaches all three adapters at once. Added
      2026-08-28 at the user's direction, after the close-out.
- [x] AC-21: the replay is switched off for the remainder of a turn once a round was rejected with
      items attached, and a truncated round never contributes items at all. Both guards were
      mutation-checked (red with the condition removed, green with it). From the second
      `/code-review` pass; added 2026-08-28.
- [x] AC-22: an in-stream error code that faults the request maps to 400 and aborts the group,
      while an account-scoped or transient one still rotates. From the second `/code-review` pass;
      added 2026-08-28.
- [x] AC-19: a non-streaming response whose `status` is `failed` is classified as a failure, not
      returned as an empty success at HTTP 200. From the `/code-review` pass; added 2026-08-28.
- [x] AC-20: replayed provider items are never sent to a key other than the one that produced them,
      and the turn survives a `request_rejected` round by retrying once without them. The key tag is
      asserted to be absent from the outgoing body. From the `/code-review` pass; added 2026-08-28.
- [x] AC-18: FU-3 closed — the replayed provider items held across one tool loop are bounded by
      `_MAX_RETAINED_PROVIDER_ITEM_BYTES`, shedding whole rounds oldest-first. Asserted both
      directions: over budget sheds exactly the oldest round and leaves its `tool_calls` intact,
      under budget touches nothing. Added 2026-08-28 at the user's direction, after the close-out.

## 12. Test Plan

All adapter-level assertions live in `backend/tests/unit/test_provider_adapters.py`, reusing its
`_chat`, `_sse` and `_ok_chat_route` helpers, adapted to the new endpoint and event vocabulary. The
OpenAI section of that file is rewritten rather than extended: its request-shape assertions all
describe Chat Completions and would otherwise assert a shape the adapter no longer sends.

- T-1 to T-4 cover AC-3 to AC-5: request-body assertions on a tools-bearing call, on a
  spec-listed effort value, on an out-of-range effort value, and on Q-2's floor.
- T-5 and T-6 cover AC-6 and AC-7: history and attachment translation into `input` items.
- T-7 to T-9 cover AC-8, AC-9 and AC-13 on the non-streaming path.
- T-10 to T-13 cover AC-10 to AC-12 on the streaming path, including the multi-fragment tool call
  and both in-stream error shapes.
- T-14 covers AC-14 and spans two files: the adapter half in `test_provider_adapters.py`, the turn
  engine half in `test_agent_turn_loop.py`, since the copy at `turn_engine.py:4010` is where the
  passthrough would silently stop working.
- T-15 covers AC-9's router half in the same place `test_provider_adapters.py:191-192` already
  asserts the predicate.

Per §10 and Q-5, no `respx` fake can establish that the request shape is *accepted*; AC-15 and
AC-16 are the live half and are expected to remain open at close.

## 13. SRS Delta

None expected. `[R7.08]` governs key-group exhaustion semantics, which this task must preserve
rather than change. Revisit at §6 if the assessment finds a user-visible behaviour the SRS does not
already describe.

## 14. Open Questions

- Whether the Responses API is available on all the deployment targets SMAP supports. The platform
  is self-hosted against api.openai.com directly, so this is expected to be moot, but it was not
  verified.
- ~~Whether any SMAP feature depends on a Chat Completions behaviour with no Responses
  equivalent.~~ Answered by §5.6: only `seed`, which no catalogued OpenAI model can receive today,
  and reasoning-item continuity, which Q-4 closes.
- The two items in §5.7 that documentation could not settle. Both are AC-15 and expected to remain
  open at this dossier's close per Q-5.

## 15. Deviation Log

- **D-1: the three gpt-5.x rows had `effort_conflicts_with_tools` cleared, which §6.4 said would
  not happen.** §6.4 as written kept every capability value untouched and annotated the two dead
  fields with comments. That is wrong for `effort_conflicts_with_tools`, and the assessment missed
  why: unlike `uses_completion_token_field`, this field is not read by the adapter directly but by
  the shared gate `CapabilityFlags.forwardable_effort` (`base.py:153-165`), which the migration
  deliberately left untouched. A row that still declared the conflict would therefore have gone on
  dropping the very effort value the migration exists to deliver, and the agent-config form reads
  the same field (`AgentDetailView.vue:299,353,364`) to disable its control, so the UI would also
  have gone on saying the setting is unavailable. `gpt-5.5`, `gpt-5.4` and `gpt-5.4-mini` are now
  `False` with a comment giving the reason; the gate itself is unchanged and still honours the flag
  for any future row that sets it, which `test_openai_drops_reasoning_effort_when_a_conflicting_
  model_carries_tools` keeps pinned. `uses_completion_token_field` is genuinely unread now and its
  values were left alone as §6.4 intended.
- **D-2: `frontend/tests/mocks/handlers.ts` was edited, which §6 said had no frontend work.** The
  fixture's three OpenAI rows asserted `effort_conflicts_with_tools: true`, which D-1 makes false of
  the shipped catalogue. No frontend test reads that branch (verified by grep across
  `frontend/**/__tests__/`), so this is a truthfulness fix to a fixture rather than a behaviour
  change, but it is still a file §6 did not list.
- **D-4: a `/code-review` pass found three defects the two gates missed, all fixed (AC-19, AC-20).**
  Two are worth a later reader's attention. **The migration created a success shape that did not
  exist before**: on `/v1/responses` a run that fails after the request was accepted returns HTTP
  200 with `status: "failed"` and an empty `output`, where every Chat Completions failure was a
  non-2xx. The streaming path handled it (`response.failed`) and `_chat` did not, so the router
  would have booked a success, skipped rotation, and handed the caller an empty string —
  `summariser.py:70` writing an empty summary and both triple extractors finding nothing, silently.
  **And the Q-4 replay had a cross-key hazard neither gate traced**: each tool round is an
  independent `call_stream` that re-picks a group member (`provider_router.py:457-486`), a key group
  legitimately holds keys from more than one OpenAI account, and encrypted reasoning is decryptable
  only by the account that produced it — so a quota rollover mid-loop turns the replay into a
  deterministic 400, which `classify_http` makes an ABORT that kills the whole group. That is
  precisely the `provider_exhausted:request_rejected` this two-dossier effort started from, which
  makes it the one regression this work could least afford to introduce. Closed twice over: the
  adapter tags items with a non-reversible digest of the key that produced them and replays only on
  a match, and the turn loop retries a rejected round once with every item stripped. The third was
  `_shed_provider_items` dropping the newest round when that round alone exceeded the budget,
  contradicting its own documented policy.
- **D-5: a second `/code-review` pass found four more, all fixed (AC-21, AC-22).** Three of them
  are the same defect wearing different clothes, and the pattern is the lesson: **the fix for D-4
  guarded the moment the replay is *sent* and left the moments it is *created* unguarded.** The
  retry was one-shot while the replay re-armed itself every round, so a systemic refusal recovered
  once and then died one round later with the retry spent. A round truncated at the output ceiling
  contributed its half-written items — a `function_call` whose `arguments` stops mid-JSON, which
  this loop already has a branch for — and the synthesised path never had that exposure because it
  re-serialises the arguments. And the retention budget was enforced in bytes but *justified*
  against a token window, on a payload (base64-ish encrypted content) that tokenises far worse than
  the `len // 4` the reasoning implicitly assumed; 96 KB could have been 40k+ tokens of a 128k
  window, stacked on an allocation computed as if it were zero. The fourth is unrelated to the
  replay: `_STREAM_ERROR_STATUS` defaulted every unlisted code to 500, so a deterministic
  `response.failed` (content policy, invalid prompt, a bad image in the request) was replayed
  against every key in the group with backoff instead of aborting it.

  Both new replay guards were **mutation-checked** — red with the condition removed, green with it
  — rather than merely observed passing.
- **D-3: §5.7 gained item 2a during implementation.** The assessment did not ask whether a
  non-reasoning model accepts `include: ["reasoning.encrypted_content"]`. It cannot be settled from
  documentation, the trade-off between the two available choices is recorded at that item, and it
  joins AC-15's live check.

## 17. What was verified, and what was not

Two later commits closed FU-2 and FU-3 in this same dossier (AC-17, AC-18); the paragraph below
describes the state before them, and their own CI run is the one to read for the final tree.

Every mechanical and contract gate ran on CI: PR #170, run `33171817174`, **22 of 22 jobs green**,
including `backend-test`, `backend-typecheck`, `backend-db`, `backend-integration`,
`backend-wiring`, `frontend-e2e` and `frontend-gate-openapi-drift`. Run locally against the diff:
`ruff check`, `ruff format --check`, the four affected unit files, and the `check-security` and
`check-quality` gates (0 Critical, 0 High; one MEDIUM to FU-3, one Hardening to FU-2, one Info
fixed in place). No migration, no API contract change, so gate 2 is N/A beyond the drift check,
which passed unchanged.

**What no gate covers, and what a later session must therefore not assume.** Per Q-5 there was no
real key this session, so **AC-15 and AC-16 are closed unticked**. Nothing in this task has ever
sent a request to `api.openai.com/v1/responses`. Every claim about the request being *accepted*
rests on documentation plus `respx` fakes the adapter's own author wrote, which is precisely the
combination §10 warned cannot see a shape mismatch. Three specific unknowns, in the order they
would bite:

1. **Strict mode on function tools** (§5.7 item 1). The adapter sends `"strict": false` explicitly
   because the migration guide says Responses defaults to strict. If that reading is wrong in
   either direction the first real turn with tools fails, and no unit test can tell.
2. **`include: ["reasoning.encrypted_content"]` on a non-reasoning model** (§5.7 item 2a, D-3). Sent
   unconditionally, which is the lower-risk of two unverifiable options; a 400 here would affect
   `gpt-4o`-class models only.
3. **The HTTP error envelope** (§5.7 item 2). `scrub_error` and the `provider_detail` chain assume
   `type`/`code`/`param` survive on this endpoint. If only `code` and `message` do, errors still
   scrub safely but stop naming causes, silently undoing `1d9a3da`.

The browser pass was not performed either. The user-visible consequence of this task is that the
agent-config effort control stops being disabled for gpt-5.x (D-1); that follows from the capability
table's data changing, and the frontend logic is untouched and already covered, but nobody looked at
the form.

## 16. Follow-ups

Both follow-ups below were **closed in this dossier on 2026-08-28**, at the user's direction, after
the initial close-out. Their entries are kept as written so the reasoning that opened them survives;
what changed is recorded under each, and the criteria are AC-17 and AC-18.

- **FU-3: the context-window guard does not see the growth `provider_items` introduces.** The
  `F-16 AC-6` guard runs `estimate_tokens` over the assembled messages *before the initial
  dispatch* only, so the reasoning blobs the tool loop now accumulates on each assistant turn are
  invisible to it. Bounded by `MAX_TOOL_ROUNDS`, so it inflates the room owner's own token spend
  rather than running away, but a long tool loop can now approach the context limit from a
  starting point the guard judged safe. Found by the security gate as a MEDIUM. Fix direction:
  re-run the guard per round, or drop reasoning items from the replay once the estimate nears the
  limit.

  **Closed (AC-18) by the second of those two, not the first, and the reason matters.** Re-running
  the guard per round was rejected on inspection: `_estimate_messages_tokens`
  (`turn_engine.py:4434-4448`) counts message `content` and text blocks only — not `tool_calls` and
  not `provider_items` — so a per-round call to it would have been *blind to the very growth it was
  added to catch*, and would have shipped as a guard that could never fire. It also turned out the
  broader problem is older and wider than this task: `turn_engine.py:2863` already names
  "Mid-tool-loop growth is a separate vector (FU-4)", carried since
  `2026-07-14-knowledge-context-token-budget` and re-declined by `2026-07-16-agent-skills`, because
  tool *outputs* are unbudgeted for up to `MAX_TOOL_ROUNDS` rounds.

  So the fix bounds this task's own contribution and says so, rather than pretending to close
  FU-4. `_shed_provider_items` drops whole rounds, oldest first, past
  `_MAX_RETAINED_PROVIDER_ITEM_BYTES`. Whole rounds because a clipped item would be replayed as a
  malformed one and fail the request outright, whereas shedding a round degrades exactly to the
  pre-Q-4 behaviour. Oldest first because the most recent round is the one the model is continuing
  from. Bytes rather than tokens because the payload is provider-encrypted content, where
  `estimate_tokens`' `len // 4` heuristic would put a number on it that reads as precision it does
  not have.
- **FU-2: `base.scrub_stream_error` does not apply the identifier-shape check that
  `summarise_http_failure` applies.** `scrub_error`'s path runs the provider's `type`/`code`/`param`
  through `_safe_ident` (`probes/base.py:128-136`), which drops anything that is not
  identifier-shaped, precisely so a masked key reflection smuggled into `code` cannot survive.
  `scrub_stream_error` (`base.py:105-114`) interpolates its `kind` argument directly. Pre-existing
  and unchanged by this task — the previous adapter passed `error.type`/`code` in exactly the same
  way, and on this endpoint the in-stream `code` is a closed vocabulary, which makes it narrower
  than before rather than wider. Out of scope here because fixing it touches all three adapters'
  in-stream error paths at once.

  **Closed (AC-17).** Touching all three at once turned out to be the argument *for* the fix
  rather than against it: the filter belongs in `adapters/base.py`, which is the single place all
  three already share, so one edit closes the gap everywhere instead of three. `_safe_ident` is
  now public `safe_ident` with a docstring saying why, and `scrub_stream_error` runs its `kind`
  through it. No real value changes — every code all three providers actually send is
  identifier-shaped — which is what makes the change safe to land alongside a migration.

- FU-1: Gemini's Interactions API reached general availability in June 2026 and is documented as
  recommended for new projects, with `generateContent` retained for compatibility. The same
  assess-then-decide question therefore exists for `adapters/gemini.py`. Not in scope here, and not
  urgent: unlike the OpenAI case there is no known capability SMAP cannot reach on the current
  endpoint.
