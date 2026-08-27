---
type: feature
status: approved
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

To be completed during this task, before any adapter edit. It must answer, each with a citation to
current OpenAI documentation:

1. **Request shape.** How `messages` maps to the Responses API's input, how tools are declared,
   how reasoning effort is expressed, and which of `_chat_body`'s four conditional branches survive.
2. **Response shape.** How to recover the neutral `{text, tool_calls, finish_reason}` body, and
   what the finish-reason vocabulary is. `is_truncated_finish_reason` (`provider_router.py`) has a
   Chat Completions vocabulary today and is asserted by
   `test_provider_adapters.py:163` and `:182`.
3. **Streaming.** The SSE event vocabulary, where the first token-bearing event appears, and how a
   mid-stream error is delivered. `openai.py:263-277` currently maps an in-stream error object onto
   an HTTP status by inspecting `type`/`code`; the equivalent must exist or the router loses its
   rotate/abort distinction.
4. **Usage accounting.** Field names for input and output tokens, and whether a reasoning-token
   count is reported separately. `record_call` and `record_usage_event` bill from these.
5. **Error envelope.** Whether `error.type`/`code`/`param` survive, so `scrub_error` and the
   `provider_detail` chain keep naming causes.
6. **What is lost.** Anything Chat Completions does that Responses does not, in the shapes SMAP
   actually sends.

### Options to be weighed

**Option A: migrate.** Reasoning effort and tools compose; the newest models become reachable;
the platform is on the endpoint OpenAI documents as primary.

**Option B: stay, and keep the capability table's honest disabling.** No rewrite risk. The platform
permanently cannot offer reasoning effort to an OpenAI agent with tools, which is every agent.

**Option C: migrate the non-streaming path only.** Smaller. Leaves two request builders for one
provider, which is the divergence this codebase has already been bitten by.

### Recommendation

To be written, then approved, before §6 is implemented.

## 6. Detailed Changes

Deliberately unfilled: it depends on §5's outcome. To be written against the approved
recommendation before `/build` runs. If Option B is recommended, this dossier closes as
`abandoned` with the assessment retained as its product, and that is a legitimate result.

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

- [ ] AC-1: §5 is complete, every claim cited to current OpenAI documentation, with a
      recommendation and the option it was chosen over.
- [ ] AC-2: the user has approved the recommendation, recorded as a Q-n row.
- [ ] AC-3 onward: to be written against the approved recommendation. If Option B is recommended,
      AC-1 and AC-2 are the whole dossier and it closes `abandoned`.

## 12. Test Plan

To be written with §6. Two constraints are already fixed: the adapter-level assertions live in
`backend/tests/unit/test_provider_adapters.py` and must cover request shaping, response
normalisation, streaming reassembly and secret non-leakage as they do today; and per §10 at least
one verification against the real endpoint with a real key is required, since no fake can establish
that the request shape is accepted.

## 13. SRS Delta

None expected. `[R7.08]` governs key-group exhaustion semantics, which this task must preserve
rather than change. Revisit at §6 if the assessment finds a user-visible behaviour the SRS does not
already describe.

## 14. Open Questions

- Whether the Responses API is available on all the deployment targets SMAP supports. The platform
  is self-hosted against api.openai.com directly, so this is expected to be moot, but it was not
  verified.
- Whether any SMAP feature depends on a Chat Completions behaviour with no Responses equivalent.
  §5 item 6 exists to answer this and the answer is not currently known.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

- FU-1: Gemini's Interactions API reached general availability in June 2026 and is documented as
  recommended for new projects, with `generateContent` retained for compatibility. The same
  assess-then-decide question therefore exists for `adapters/gemini.py`. Not in scope here, and not
  urgent: unlike the OpenAI case there is no known capability SMAP cannot reach on the current
  endpoint.
