---
type: bugfix
status: implemented
created: 2026-07-22
requirements: []
depends_on: []
---

# The tool loop degrades every failure the same way, whatever category it belongs to

## 1. Summary

Three confirmed defects in `_stream_with_tools` and its tool-dispatch surface. They share a
conceptual frame — the turn loop has exactly **one** failure vocabulary, "degrade and
continue", and applies it uniformly to three categories that require different handling — but
there is **no single unifying code change**, and this dossier says so plainly so no one goes
looking for the one-line fix.

| Category | Correct handling | What the loop does |
|---|---|---|
| Domain failure (unknown skill, bad approval id) | tell the model, continue — **correct today** | degrade and continue |
| Infrastructure failure (DB abort, key group exhausted) | must not be reported to the model as a tool outcome; abort or isolate | degrade and continue |
| Protocol violation (truncated JSON, malformed args) | reject before dispatch, tell the model to retry smaller | degrade and continue |

- **A (F-6)** — a tool's DB failure poisons the turn's session. The catch converts the Python
  exception but cannot clear the Postgres transaction state, so the loop streams a complete
  answer to the user and then dies persisting it. The user watches a full reply appear and
  vanish; provider spend is paid and nothing persists.
- **B (F-16)** — truncated streamed tool-call JSON silently becomes `{}`. `finish_reason` is
  carried into the body by all three adapters and read by nobody, and there is no schema
  validation anywhere in dispatch.
- **C (F-17)** — a failed final synthesis call is swallowed and round-8 filler is persisted as
  the agent's answer with a success audit.

What justifies one dossier is that all three live in the same ~120 lines, are exercised by the
same test seam (`_stream_with_tools` with a fake router and fake registry), and would otherwise
mean three passes over the same code with three near-identical harnesses.

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-6, F-16, F-17 (all major,
all confirmed).

## 2. Observed vs Expected

**A.** The turn's session is handed to every tool at construction
(`backend/contexts/agents/application/runtime/turn_engine.py:1837-1843`, `:952`).
`registry.call` is invoked bare at `:2690`, and `ToolRegistry.call`
(`backend/contexts/agents/application/runtime/tool_registry.py:176-179`) catches `Exception`
with no discrimination. Once any statement errors, the backend marks the transaction aborted and
every later `execute` raises. The loop continues, streams to the room (`:2677`), and dies at
`MessageService.send_agent` (`:2181`), landing in the outer handler (`:2220-2246`) which rolls
back and reports a failed turn.

The engine knows this hazard verbatim: all three `begin_nested()` uses in the file (`:2256`,
`:2283`, `:2845`) are read-only best-effort lookups whose docstrings say a plain rollback
"would discard the whole transaction". The write path — the one that needs it — has none.

Confirmed poisoning entry points, all writing `audit.emit` on the turn session with no
savepoint (`backend/shared_kernel/audit.py:115-136` inserts directly into the caller's session):

| Site | Behaviour on failure |
|---|---|
| `backend/contexts/agents/application/runtime/builtin_tools.py:527-548` `_audit_tool_invoke` (called `:567`, `:584`, `:586`, `:623`, `:662`, `:665`, `:669`) | **swallows at `:547`, logs a warning, and reports success to the model** — poisons while claiming OK, bypassing the registry catch entirely. The worst variant. |
| `backend/contexts/agents/application/tools/web_search.py:188-203`, `file_tool.py:102-116`, `code_exec.py:59-72` | raise → caught per-tool → `is_error=True`. Poisons, at least flags an error. |
| `backend/contexts/orchestration/application/wakeup_service.py:357-369` | raise → registry catch-all |

**The correct pattern already exists in this repo, on the same session.**
`backend/contexts/keys/infrastructure/usage_events.py:38-63` wraps its INSERT in
`async with db.begin_nested()`, and its docstring at `:39-44` states the exact rationale:
"swallowing the Python exception alone does not clear that … every later `db.execute` on the
shared request session would raise `InFailedSqlTransaction`". That path is **cleared** — the
router's mid-stream usage write is already safe. `audit.emit` never got the same treatment.

**B.** `_safe_json` (`backend/contexts/keys/infrastructure/adapters/anthropic.py:307-314`,
`openai.py:321-328`) maps both `JSONDecodeError` and a non-dict parse onto `{}`, applied at
stream close (`anthropic.py:289`, `openai.py:304`). Gemini is structurally immune —
`gemini.py:246` takes `functionCall.args` as an already-parsed object. `finish_reason` is
carried into the body by all three (`anthropic.py:299`, `openai.py:313`, `gemini.py:265`) and
documented as part of the normalised contract at `adapters/base.py:42`; a repo-wide grep finds
**zero** consumers outside the adapters and their tests. No validation stands between `{}` and
the tool: `tool_registry.py:172-179` calls `tool.invoke(args)` directly, and `input_schema` is
only serialised into the provider spec (`:139-145`). Tools then degrade rather than reject —
`builtin_tools.py:149` does `str(args.get("query", ""))`, returning a plausible empty result set
flagged as success.

**C.** `turn_engine.py:2747-2761` wraps the whole final call in `except Exception`, logs at
`:2759`, and returns `last_text` — described by the code's own comment at `:2700-2703` as
"typically 'let me check…' or empty". `KeyGroupExhausted` and `ProviderStreamError` are plain
`Exception` subclasses. The `tuple[str, int]` signature gives the caller at `:2106` no channel
to distinguish synthesis from total provider failure.

Two additions to the audit's account, both found during analysis:

- **A second call site.** The headless A2A path calls `_stream_with_tools` at `:870` and audits
  `agent.turn_finished` at `:881` with **no** `if not final_text.strip()` guard — so on that
  path even the empty half of C is reported completed. Any fix must cover both callers.
- **A third silent degrade in the same block.** `:2758` reads
  `str(final_body.get("text", last_text))`. If the final call *succeeds* but the router never
  yields a `StreamComplete`, `final_body` stays `{}` and the filler is returned **with no log
  line at all**. Same shape as C, not covered by the `except`.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is there a single fix for all three? | **No.** A shared *vocabulary*, not a shared fix. | A needs transaction isolation, B needs a validation gate, C needs a discriminated return. Grouping is a change-surface and test-harness convenience with a shared conceptual frame. Stated explicitly so the implementer does not hunt for a unifying patch. |
| Q-2 | Should a savepoint wrap `registry.call` directly? | **No — blocked, and this is the key discovery.** | `cast_approval_vote` calls `approval_service.cast_vote`, which calls `await self._db.commit()` at `backend/contexts/orchestration/application/approval_service.py:226` — on the turn's own session, mid-tool-round. A naive `begin_nested()` around `:2690` puts a `commit()` inside a savepoint context manager, whose `__aexit__` then releases a savepoint the commit already discarded. **Verify empirically against SQLAlchemy 2.0 + asyncpg before finalising**, but it rules out the wrapper as a blind choice. Note the asymmetry it exposes: `update_wakeup` (`wakeup_service.py:339-371`) does *not* commit and depends entirely on the reply commit at `:2189`. The two DB-writing built-ins have opposite transaction contracts — itself worth recording. |
| Q-3 | Where should A's isolation go instead? | **Savepoint `audit.emit` at the source**, mirroring `usage_events.py:38-63`. | One edit closes the strongest trigger (`_audit_tool_invoke`) plus the three tool-side emitters and `agent.wakeup_clamped`. **Caveat**: `audit.emit` is called from nearly everywhere; a savepoint per audit row is a measurable cost on hot paths and changes rollback semantics for callers who *want* the audit row to die with the domain write. Safer variant: an opt-in `isolated: bool = False` parameter set at the tool-path call sites only. Decide explicitly; do not default it on without measuring. |
| Q-4 | Should the registry re-raise database errors instead of degrading them? | **Yes**, catching `SQLAlchemyError` separately and re-raising; everything else keeps degrade-and-continue. | A DB fault is not a tool outcome the model can act on, and continuing to burn provider spend against a doomed transaction is strictly worse than failing fast. This inverts the current comment ("a tool failure must not abort the turn") for exactly one category — **the comment must be rewritten to say which category and why.** |
| Q-5 | `tool_registry.py:11-12` boasts it imports no infrastructure. Does Q-4 break that? | **Yes, and it is a design decision to record, not an implementation detail.** | Two options: import only the exception type (a domain-visible error class, arguably acceptable), or have the engine pass a classification predicate. Either is defensible; leaving it implicit is not. |
| Q-6 | Should tools get their own session instead? | **No.** | It would fix the poisoning but break `update_wakeup`, whose write is deliberately atomic with the reply, and would double-connection every tool-using turn. |
| Q-7 | For B, read `finish_reason`, validate against `input_schema`, or both? | **Both, in that order.** | Truncation and schema violation are different failures needing different messages. Note `jsonschema==4.23.*` is **already a runtime dependency** (`backend/pyproject.toml:28`) and a ready-made helper exists to copy: `backend/contexts/activities/application/validators/schema.py:27-30` (`payload_errors`, using `Draft202012Validator.iter_errors`). |
| Q-8 | How should a truncation be signalled to the model? | In the tool-result **`content` string**, not via `is_error`. | `is_error` is translated only by the Anthropic adapter (`anthropic.py:78`); OpenAI (`openai.py:96-103`) and Gemini (`gemini.py:45-55`) drop it. Error signalling to the model is carried by `content` alone on two of three providers — load-bearing for this fix. |
| Q-9 | For C, re-raise or return a discriminated result? | **Discriminated result**, persisting the filler **with an explicit marker**. | Re-raising turns a recoverable situation — eight rounds of real tool work already done — into a wholly failed turn, a regression for the user. Losing that work entirely is worse than showing it with an honest flag. This needs a `$t()` string and a conversation-slice change, so the backend can land first with the metadata and audit field and the UI marker follows. |
| Q-10 | Does this depend on any open dossier, or overlap the a2a orchestration audit? | No hard dependency, but **coordinate**. `depends_on: []`. | `2026-07-19-large-artifacts-silently-dropped` touches `turn_engine.py:1133`, a different region — judged not an overlap prerequisite. The a2a audit's `2026-07-22-turn-idempotency-and-locking/` touches the turn loop's locking and A2A entry paths; different lines, but sequence with awareness. |

## 4. Reproduction

**A.** An MCP-bound agent, a chatroom turn, and a DB fault reachable during the tool call.

1. Start the turn. The pre-stream commit lands at `:2104`, so tool writes sit in a fresh
   transaction.
2. The model emits a tool call; `_build_mcp_tool_from_agent_tool._invoke` runs
   (`builtin_tools.py:563`).
3. Force the `audit_logs` INSERT inside `_audit_tool_invoke` (`:586`) to fail — a statement
   timeout, an FK violation, or in test a patched `session.execute` raising on that insert only.
4. `:547` swallows and logs; the tool returns success; the model produces a full reply and
   tokens stream to the room via `:2677`.
5. `MessageService.send_agent` (`:2181`) raises `InFailedSqlTransaction`. `:2220` rolls back.
   `:2238`'s recovery audit **also** fails on the same aborted transaction and is swallowed at
   `:2240`.
6. **Observed**: the user watches a complete answer appear token by token and vanish. Provider
   spend paid, nothing persisted, and even the `agent.turn_failed` audit row is lost.

Cheaper unit variant: a fake tool whose `invoke` runs a deliberately invalid statement on a real
session.

**B — deterministic truncated arguments**, forced at the adapter layer with `respx`, exactly as
`backend/tests/unit/test_provider_adapters.py:262-302` already does for the complete case —
emit the SSE fragments and **omit the closing fragment**:

- Anthropic: `content_block_start` with a `tool_use` block, one or more `input_json_delta`
  carrying unterminated JSON, then `message_delta` with `{"stop_reason":"max_tokens"}`.
  `_safe_json` (`anthropic.py:289,307-314`) yields `{}`.
- OpenAI: `delta.tool_calls[0].function.arguments` truncated, then a chunk with
  `finish_reason: "length"`.

End to end: feed that through `_stream_with_tools` with a real registry holding `web_search` —
`registry.call("web_search", {})` → `str(args.get("query",""))` → an empty result set returned
as success. Or with `file`: `{"op":"write","path":…}` truncates the target and reports
`written: 0` without `is_error`.

**C.** An agent whose key group can be driven to exhaustion, and a prompt burning all 8 rounds
(`MAX_TOOL_ROUNDS = 8`, `turn_engine.py:96`).

1. Round 8 leaves `last_text` holding non-empty filler.
2. Make the final `call_stream` at `:2749` raise `KeyGroupExhausted`.
3. `:2759` logs a warning; `:2761` returns `(last_text, 8)`; `:2117` passes because it is
   non-empty; `:2181` persists it; `:2188` audits `agent.turn_finished {"tool_rounds": 8}`.
4. **Observed**: "Let me check that for you." is the final committed answer, no error anywhere
   in the UI, and it becomes permanent history conditioning the next turn.

Fully unit-testable with the existing `_FakeRouter` shape
(`backend/tests/unit/test_agent_turn_loop.py:25-53`): tool calls for rounds 1-8, raise on 9.

## 5. Root Cause Analysis

**A** — the tool boundary reports infrastructure failure as a tool outcome, and there is no
savepoint isolating the write. Root cause is the missing isolation at
`shared_kernel/audit.py:115-136` and the undiscriminated catch at `tool_registry.py:176-179`;
the strongest trigger is `_audit_tool_invoke`'s swallow at `builtin_tools.py:547`, which
poisons while reporting success.

**B** — the argument boundary has no protocol-violation channel: `_safe_json` erases the
distinction between "no arguments" and "arguments truncated", and the one field that carries
the distinction (`finish_reason`) has no consumer.

**C** — the provider boundary has no discriminated return, so `_stream_with_tools`' caller
cannot tell a synthesised answer from a total provider failure.

**Explicitly not a shared root cause** (Q-1). The shared property is the missing category
distinction, which is a framing that makes the three legible together — not a defect with one
fix.

## 6. Blast Radius and Sibling Suspects

**Unconditional `except Exception` on paths writing to the turn session:**

| Site | Verdict |
|---|---|
| `tool_registry.py:176-179` | **Confirmed** — A primary |
| `builtin_tools.py:547` `_audit_tool_invoke` | **Confirmed** — worst variant, reports success while poisoned |
| `builtin_tools.py:153, 194, 383, 432, 664` | **Confirmed as poisoning vectors** — they flag `is_error`, so milder than `:547`, but the session is equally dead |
| `provider_router.py:513-514` `_account()` | **Cleared** — the write beneath it (`usage_events.py:38-63`) is savepointed. Same shape as `_audit_tool_invoke`, opposite outcome. **This is the precedent to copy.** |
| `provider_router.py:708-709` | **Cleared** — same underlying write |
| `turn_engine.py:2260, 2285, 2849` | **Cleared** — each preceded by `begin_nested()`, all read-only |
| `turn_engine.py:959-961`, `:979`, `:1033`, `:1039`, `:1093`, `:1260`, `:1336`, `:1394`, `:2479` | Read/staging only, no turn-session writes. **Cleared for A**, though each is a silent-degrade site in the same family |
| `turn_engine.py:1492`, `:894`, `:2240` | **Cleared** — post-failure, already rolled back |
| `turn_engine.py:2601-2605` headless compaction | Calls `self._db.rollback()` inside a broad catch on the turn session. Not on the tool path; **suspect, out of scope** — see FU-1 |
| `wakeup_service.py:173-174` | Same class, not reachable from the tool path. **Cleared here**; FU-1 |
| `workflow/application/executors/approval_gate.py:34,112` | Known from F-18, owned by the a2a audit. **Out of scope** |

**Provider response fields carried but never read:**

| Field | Verdict |
|---|---|
| `finish_reason` (all three adapters; contract at `base.py:42`) | **Confirmed** — defect B. Zero consumers outside adapters and tests |
| `ProviderCallResult.http_status` on the `StreamComplete` at `:2681` | **Cleared** — `provider_router.py:531-540` yields the terminal event only when `classify_http` returns OK; every non-OK path raises |
| `ToolResult.is_error` outbound (`turn_engine.py:2697`) | **Confirmed, partial** — honoured only by Anthropic (`anthropic.py:78`); OpenAI and Gemini drop it. Arguably correct (neither API has the field), but it makes `content` the only reliable error channel — **load-bearing for B's fix** (Q-8) |
| `usage.input_tokens` / `output_tokens` | **Cleared** — consumed by `UsageAccountant.record_call` |

**Other swallow-and-continue in the runtime:** `turn_engine.py:2758` — **confirmed**, a fourth
silent degrade in C's own block with no log line; fold into C's fix. `:2619-2621`, `:2633-2634`,
`:286`, `:306`, `:641` are Redis-only best-effort — **cleared**. `:2314`, `:2367`, `:2388` are
post-commit dispatch — **cleared**.

**Naming hazard.** `turn_engine.py:2950-2952` contains a docstring reading "(F-16 pre-dispatch
guard)" referring to `_estimate_messages_tokens`. That is a **different, earlier** F-numbering —
the file also references `FIX-11`, `FU-4`, `D-13` — and is unrelated to this audit's F-16. Any
comment this fix adds must cite `2026-07-22-agent-config-runtime/F-16` in full so the collision
does not compound.

## 7. Fix Design

Sequenced as six self-contained, independently revertible commits. Commits 1, 2 and 6 are
behaviour-visible to the model and should not ship in the same deploy as 5 if the metrics are
to stay attributable.

1. **Savepoint `audit.emit`** (`shared_kernel/audit.py:115-136`), mirroring
   `usage_events.py:38-63` — same shape, same docstring rationale. Per Q-3, prefer an opt-in
   `isolated=` parameter set at the tool-path call sites over defaulting it on.
2. **Re-raise `SQLAlchemyError` from the registry** (`tool_registry.py:176-179`) per Q-4,
   rewriting the comment to name the category, and recording the Q-5 boundary decision.
3. **Stop `_audit_tool_invoke` reporting success when it failed** (`builtin_tools.py:547`).
   With (1) in place the swallow is defensible; minimum, return a flag the caller folds into
   `is_error`, so the model is not told a tool ran cleanly when its record did not. Raise the
   log line from `warning` to `error` — a lost audit row is not a warning-level event on a
   BYO-key platform.
4. **Extract the duplicated chat-request builder.** `turn_engine.py:2654-2664` and `:2731-2739`
   are two near-identical `ProviderRequest` builds differing only in `tools` and the messages
   list, with duplicated `effort` and `_sampling_payload` handling. Commit 5 touches both;
   leaving them divergent guarantees the next drift. Pure refactor, characterization tests only.
5. **C: discriminated outcome and an honest marker.** Replace `tuple[str, int]` with a small
   frozen dataclass carrying `text`, `rounds`, `synthesis_failed` and an error kind, updating
   both callers (`:870`, `:2106`). On synthesis failure with non-empty `last_text`, **persist
   the filler but mark it**: set the flag in `reply_meta` at `:2138`, audit it at `:2188` and at
   `:881`, and emit the error kind on the `agent.finished` WS event so the frontend can render
   a marker. On synthesis failure with empty `last_text`, keep the existing `:2117-2136` skip
   but change the audit reason from `empty_reply` to the error kind, so a provider outage stops
   being recorded as a benign skip. Also fix the un-`except`ed fallback at `:2758`.
6. **B: reject truncated and schema-invalid arguments before dispatch.** Read `finish_reason`
   at `:2680-2690`; when it indicates truncation (`max_tokens` for Anthropic, `length` for
   OpenAI — **the vocabularies differ and `base.py:42` does not normalise them; normalising is
   part of the work**) and `tool_calls` is non-empty, do not dispatch — append a tool result
   telling the model its arguments were cut off at the token ceiling and to retry with a smaller
   payload. Then validate args against `input_schema` in `ToolRegistry.call` between the name
   lookup and `tool.invoke`, using `payload_errors` (Q-7), returning `is_error=True` with the
   jsonschema messages verbatim.

**This also closes F-20 for free**: `{"op":"write","path":"x"}` with `content` missing currently
truncates the file (`builtin_tools.py:380`) because `_FILE_SCHEMA` (`:113-126`) does not require
`content`. The schema still needs tightening (conditional `required` on `op == "write"`), but
validation is the enforcement point that makes the tightening effective.

**Why this corrects rather than masks.** Each change gives one category its own channel: an
infrastructure fault stops being reported as a tool outcome, a protocol violation stops being
reported as an empty-but-valid call, and a provider failure stops being reported as an answer.
None of them is a guard bolted onto a symptom.

## 8. Regression Test Plan

**`backend/tests/unit/test_agent_turn_loop.py`** — the harness is exactly right and needs
extending, not replacing: `_FakeRouter` (`:25-53`) and `_FakeRegistry` (`:56-65`) plus
`te.TurnEngine.__new__` (`:81`) drive `_stream_with_tools` with no DB.

**The failing test comes first** — `test_truncated_tool_arguments_are_not_dispatched`: the
router returns `{"tool_calls": [...], "finish_reason": "max_tokens"}`; assert
`registry.invoked == []` and that the appended tool-result content mentions truncation.
**Fails today**: `:2680-2690` ignores `finish_reason` and the call dispatches with `{}`.

Then: `test_synthesis_failure_is_reported_not_hidden` (rounds 1-8 with filler, raise on 9;
assert the outcome carries the failure — **fails today**, `:2759-2761` returns `(last_text, 8)`
with no channel to observe); `test_synthesis_missing_terminal_event_is_reported` (round 9 yields
`TokenDelta` only — **fails today**, `:2758` falls back silently with no log);
`test_stream_with_tools_returns_a_discriminated_outcome`.

Note the two existing tests unpack `text, rounds = await …` (`:89`, `:144`) and must be updated
by commit 5 — as must the three fakes in `test_observer_agents.py:900,1047,1078` and two in
`test_a2a_turn_dispatch.py:116,223`.

**`backend/tests/unit/test_agent_runtime_tools.py`** — `test_registry_swallows_tool_exception`
(`:88-97`) **locks in the current behaviour and must be split**: keep it for non-DB exceptions,
add `test_registry_reraises_sqlalchemy_errors`. New:
`test_registry_rejects_arguments_violating_input_schema` — a tool with `_CODE_EXEC_SCHEMA`
(`builtin_tools.py:103-111`, `required: ["source"]`) called with `{}` returns `is_error=True`
naming the missing field and `invoke` never runs. And
`test_file_write_without_content_is_rejected` for the F-20 compounding case.

**`backend/tests/unit/test_provider_adapters.py`**, modelled on `:262-302`:
`test_anthropic_stream_truncated_tool_json_is_flagged_not_emptied` and the OpenAI equivalent —
today `_safe_json` makes `{}` indistinguishable from a legitimate empty-argument call. If the
fix normalises truncation vocabulary, add a test asserting `max_tokens` / `length` / Gemini's
`MAX_TOKENS` map to one value.

**`backend/tests/integration/` — the gap the audit named.** Nothing in `backend/tests`
exercises a tool raising against a **live** session, and only an integration test can prove A's
fix. The vehicle exists: `tests/integration/conftest.py:39-50` provides a real-DSN
`sessionmaker` and `:53-87` a real `project` fixture. Add
`test_tool_db_failure_does_not_poison_the_turn.py`: open a real session, insert without
committing, run a statement that aborts the transaction (violate an FK on `audit_logs`), then
assert that after the savepoint rolls back a subsequent `execute` **succeeds** and the earlier
insert still commits. This also settles the Q-2 question empirically.

**`backend/tests/unit/test_builtin_tools_wiring.py`** — a drift test in the style of `:236-246`:
every `Tool` built by `build_agent_tools` must carry an `input_schema` passing
`Draft202012Validator.check_schema`, or the new validation gate silently no-ops on a malformed
schema. Note `_build_mcp_tool_from_agent_tool` uses `{"type":"object","additionalProperties":True}`
(`builtin_tools.py:593`), which validates everything — so MCP tools get truncation detection but
no schema enforcement until the MCP contract dossier lands. **That limit is an accepted gap
here**, recorded in FU-3.

## 9. Risks and Rollback

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `cast_approval_vote`'s `commit()` is incompatible with a savepoint wrapping `registry.call` | high **if that design is chosen** | tool path breaks entirely | Verify empirically first; prefer savepointing `audit.emit`, which no committing tool sits inside (Q-2, Q-3) |
| Savepointing all of `audit.emit` costs a round-trip per audit row on hot paths | medium | latency on auth, tenancy, keys endpoints | Opt-in `isolated=`; measure before defaulting on |
| Re-raising `SQLAlchemyError` converts turns that today complete-then-fail into turns that fail earlier | medium | more visible failures, same net outcome | Strictly an improvement — spend saved, no phantom answer — but it shows as a metrics delta. Log the classification so the change is attributable |
| Rejecting truncated calls costs the model a round against `MAX_TOOL_ROUNDS = 8` | medium | a turn that today half-works may exhaust rounds | The alternative is a silent wrong answer. **Consider not counting a rejected-args round against the budget** |
| Schema validation rejects calls that today succeed by coercion — `_opt_int` (`tool_registry.py:653-659`) accepts strings that `{"type":"integer"}` rejects | medium | behaviour change on tolerant tools | Audit every `input_schema` against its tool's actual coercion before enabling. **Consider a report-only mode for one release**, logging violations without rejecting |
| Commit 5's signature change touches 7+ test files and 2 call sites | high | mechanical churn | Ship as its own commit before the behaviour change (commit 4 then 5) |
| C's frontend marker needs an i18n string and a conversation-slice change | certain | cross-stack scope | Backend lands first with metadata and audit field; the UI marker follows |

**Security.** Audit integrity is the sharp edge: `mcp.tool_invoked` is the trail for what an
agent did with the user's keys and the sandbox, and the invariant is **a tool that ran must be
recorded**.

- Savepointing `audit.emit` **strengthens** the invariant. Today a failed audit insert is
  swallowed *and* kills every subsequent write, so the audit row is lost **and** so are the
  reply and the `agent.turn_finished` row. With a savepoint only the one row is at risk.
- **Do not** move the audit write after the tool result is returned, or defer it post-commit —
  that would create a window in which a tool ran and no record exists. If deferral is ever
  proposed, it must be to an outbox written in the same transaction.
- **Re-raising `SQLAlchemyError` must not leak DB detail to the model.** `tool_registry.py:179`
  interpolates `{exc}` into model-visible content, and SQLAlchemy strings can carry the failing
  SQL, table names and parameter values. Route any DB error through an error-kind
  classification, never `str(exc)`. Note this is a **pre-existing leak**: `str(exc)` already
  reaches the model at `:179` and at `builtin_tools.py:154, 195, 384, 585, 665`. See FU-2.
- Schema-violation messages echo the model's own arguments back to it — model-generated, not
  user-generated, so not a new injection channel, but they must go through `clip_tool_output`
  (`tool_registry.py:72-85`) or a large malformed argument blows the context window.
- C's marker reuses `_err_kind` (`turn_engine.py:2942-2947`), whose output already reaches the
  WS payload at `:2229` — nothing new, but confirm `reason` is a closed vocabulary that never
  contains a key id.

**Rollback.** Each commit is independently revertible; the ordering above is chosen so that
reverting any one leaves a coherent state.

## 10. Acceptance Criteria

- [x] AC-1: `test_truncated_tool_arguments_are_not_dispatched` (§8) fails against current code
      and passes after the fix.
- [x] AC-2: a failed `audit_logs` insert during a tool invocation does not abort the turn's
      transaction — proven by the integration test, not only by unit control flow.
      `tests/integration/test_tool_db_failure_does_not_poison_the_turn.py`, run against a real
      Postgres at migration head, with a control test proving the savepoint (not the swallow)
      is what saves the transaction.
- [x] AC-3: a database error raised by a tool fails the turn rather than being reported to the
      model as a tool result, and no DB detail reaches model-visible content.
      `test_registry_reraises_infrastructure_errors`,
      `test_a_database_fault_never_puts_sql_in_the_reported_error_kind`.
- [x] AC-4: `_audit_tool_invoke` never reports success to the model when its own write failed.
      `test_mcp_tool_that_could_not_be_recorded_is_not_reported_as_clean`.
- [x] AC-5: a tool call whose arguments were truncated at the token ceiling is not dispatched;
      the model receives a message naming truncation and advising a smaller payload, in the
      result `content` (not only `is_error`). Same test as AC-1; see D-5 for the per-call
      refinement.
- [x] AC-6: arguments violating a tool's `input_schema` are rejected before dispatch with the
      violations named; `file` op=write without `content` is rejected.
      `test_registry_rejects_arguments_violating_input_schema`,
      `test_file_write_without_content_is_rejected`. See D-3 for where the `file` rule is
      enforced and D-9 for the regex keywords excluded from validation.
- [x] AC-7: a failed final synthesis is never reported as a successful turn — the persisted
      message carries a marker, the audit records the error kind, and the WS event carries it.
      `test_synthesis_failure_is_reported_not_hidden`,
      `test_a_failed_synthesis_marks_the_observation_it_persists`,
      `test_an_empty_failed_synthesis_is_not_filed_as_a_benign_skip`. The field is
      `synthesis_error`, not `error` — see D-8.
- [x] AC-8: the headless A2A path receives the same treatment as the room path.
      `test_headless_turn_records_a_failed_synthesis`.
- [x] AC-9: a successful final call that yields no terminal event is logged and marked, not
      silently degraded. `test_synthesis_missing_terminal_event_is_reported`.
- [x] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .` and `mypy .` pass in
      `backend/` (mypy: 891 source files).

## 11. SRS Delta

None. No `[Rxx.yy]` governs tool-dispatch failure handling; this restores internal consistency
with the savepoint discipline the engine already applies to its read paths and with the empty-
reply guard the reply path already enforces. See FU-4.

## 12. Deviation Log

Line citations throughout §2 and §7 had drifted by roughly +50 to +280 lines
(`turn_engine.py` is now ~3400 lines); every cited construct was verified present and
unchanged in behaviour before implementation started. Three substantive drifts found at
that check, none of which invalidated the design:

- `_build_mcp_tool_from_agent_tool` now advertises a **captured** `input_schema` when the
  MCP contract dossier's probe has run (`builtin_tools.py:687-689`), which partly closes
  FU-3 — see the amended entry there.
- A `file_search` tool was added since the spec was written; it is inside the validation
  gate's blast radius and was audited with the rest.
- The room path grew an **observer** branch, so commit 5 had three persist/audit sites to
  cover rather than two, and nine `_stream_with_tools` test fakes rather than the seven §9
  predicted.

- **D-1 — Q-5 resolved as the injected predicate.** `ToolRegistry` takes an optional
  `is_infra_error` callable; the engine supplies `_is_infrastructure_error`. Chosen by the
  user over importing `SQLAlchemyError` directly, so `tool_registry`'s "no external infra"
  docstring claim stays literally true. Absent a predicate the registry degrades
  everything, exactly as before the seam existed.
- **D-2 — `audit.emit(isolated=True)` returns `bool` and swallows, rather than
  savepointing and re-raising.** §7 commit 1 did not say which. Returning a flag is what
  lets commit 2's re-raise fire only on genuine domain-write faults instead of also on an
  isolated audit failure that damaged nothing. Consequence: `web_search`, `file_tool` and
  `code_exec` no longer set `is_error` when only their audit row is lost — previously they
  did, by raising, at the cost of poisoning the transaction. §7 commit 3 named only
  `_audit_tool_invoke`, which does now report it, so the scope is as specified; the change
  for the other three is recorded here rather than left implicit.
- **D-3 — F-20's conditional `required` is enforced in the `file` tool, not in
  `_FILE_SCHEMA`.** §7 proposed `if/then` on `op == "write"`. Gemini receives
  `input_schema` verbatim as an OpenAPI-subset `parameters` (`adapters/gemini.py:98`),
  which does not carry `if/then`; a rejected schema is a deterministic 400, and the
  Anthropic adapter's own comment records that a deterministic 400 burns every key in the
  group via rotation. Enforcing in the tool holds on all three providers. The schema
  keeps a description saying `content` is required for write.
- **D-4 — truncation vocabulary normalised as a predicate, not by rewriting the field.**
  `is_truncated_finish_reason` lives beside the `StreamComplete` contract in
  `provider_router.py`. The adapters are untouched, so the raw provider value still
  reaches logs and usage rows, and no existing adapter test had to change.
- **D-5 — the truncation gate is per tool call, not per response.** §7 said "when it
  indicates truncation and `tool_calls` is non-empty, do not dispatch". Only the
  *empty-argument* case is actually ambiguous: blocks stream in order, so an earlier call
  in a truncated response can have arrived whole, and refusing it would discard work the
  model already paid for. A round is left uncharged only when nothing dispatched.
- **D-6 — a rejected round does not consume the tool-round budget**, for the first
  `_MAX_ARG_REJECTIONS = 2`; past that every rejection charges, which is what bounds the
  loop at `MAX_TOOL_ROUNDS + 2` provider calls. §9 raised this as a "consider"; the user
  chose it. Charging a round the model did no work in makes "retry with a smaller
  payload" unusable advice.
- **D-7 — a terminal event carrying no `text` now yields `""` rather than `last_text`.**
  §7 only named the missing-terminal-event case. A synthesis that returned nothing is the
  same failure, and falling back to the round-8 filler there is exactly defect C; the
  empty-reply guard handles the result honestly.
- **D-8 — the marker field is `synthesis_error`, not `error`.** Found by the quality gate:
  on the success path this dict is splatted into an `agent.finished` event that also
  carries a delivered `message_id`, and `useChatroomSocket.ts:377` reads a top-level
  `error` there as "the turn failed", rendering *"please try again"* next to the reply
  that just arrived. A regression test pins the key out.
- **D-9 — `pattern` and `patternProperties` are stripped before validating.** Found by the
  security gate as a HIGH: validating made `input_schema` executable, and that schema is
  author-controlled for `LOCAL_FUNCTION` and MCP-server-controlled for bindings, so a
  `pattern` is an attacker-chosen regex run against model-written arguments — measured at
  45s of un-preemptible CPU for `^(a+)+$` against 30 characters, which blocks the worker's
  event loop and therefore every concurrent turn on it. No built-in schema uses either
  keyword and the provider still receives the full schema, so nothing is lost.
- **D-10 — the skip path's `reason` vocabulary widened.** A failed synthesis with no text
  reports the error kind instead of `empty_reply`, and the observer variant emits
  `observation.failed` rather than `observation.skipped`. Required by §7 commit 5;
  recorded because `reason` is a user-visible string. `ObserverPanel.vue:118-120` guards
  unknown skip kinds with `te(key) ? t(key) : ''`, so nothing renders a raw key.
- **D-12 — `_without_regex` stripped `pattern` in *name* position too.** Found by
  `/code-review`. D-9's strip descended into `properties` and dropped a parameter
  literally *named* `pattern` — an ordinary name for a search or glob tool, and MCP
  servers built with the TypeScript SDK emit `additionalProperties: false`. With
  `required` still listing it, the tool became uncallable in both directions: supply the
  argument and it is "not allowed", omit it and it is "required". Reproduced, then fixed
  by making the strip keyword-position-aware (`properties`, `$defs`, `definitions`,
  `dependentSchemas` hold name-keyed subschemas; `enum`, `const`, `default`, `examples`
  hold instance data).
- **D-13 — the built-in tools' own catch-all defeated AC-3.** Also from `/code-review`.
  `web_search`, `file`, `code_exec`, `file_search` and the function tool each wrap their
  work in `except Exception`, so a DB fault inside them — `WebSearchTool._active_key`
  reads `SearchKeyRepository` on the turn's session — never reached
  `ToolRegistry.call`'s classification, and `str(exc)` put the failing SQL into the
  model's context. AC-3 held only for tools that raise past those blocks. They now
  re-raise infrastructure faults, and the classification moved to
  `shared_kernel/db/faults.py` so the registry's predicate and the tools' cannot drift.
- **D-14 — the façade tools' lost audit rows are now marked too**, reversing half of D-2.
  The review was right that leaving `file`, `code_exec` and `web_search` unmarked put the
  sandbox-write and BYO-key-search records — the ones AC-4's invariant most cares about —
  in the one state AC-4 forbids. They audit several frames below the code that shapes the
  result, so rather than thread a flag through three façades, `audit.emit` counts lost
  isolated writes on `session.info` and the builders bracket the call. That also covers
  the next tool added without it having to remember.
- **D-15 — `TurnResult` carries the synthesis marker.** AC-8's parity held for the audit
  row but not for the value the caller acts on: `a2a_handler` hands `result.text` on as a
  REPLY, so a calling agent or an `agent_invocation` node consumed the filler as an
  answer. `TurnResult` now carries `synthesis_failed` / `error_kind`, and the reply
  envelope carries them when set. Still delivered rather than turned into an error — the
  Q-9 reasoning is unchanged.
- **D-16 — a no-argument tool is no longer refused as truncated.** `truncated and not
  args` also caught tools that declare no parameters, where `{}` is complete by contract,
  handing the model advice ("retry with a smaller payload") it could not act on.
  `ToolRegistry.expects_arguments` now gates it; the permissive fallback an unprobed MCP
  binding carries still counts as taking arguments.
- **D-11 — three test fakes needed a savepoint-capable session.** A bare `AsyncMock` as
  the turn's `db` returns a coroutine from `begin_nested()`, so every isolated audit write
  failed into the swallow; with AC-4 in place that then reported the call as an error.
  Fixed in `test_builtin_tools_wiring.py` and `test_function_tool_result.py` rather than
  loosening the production behaviour — a fake whose audit writes all fail is not covering
  the audit path.

## 13. Follow-ups

- **FU-1** — `turn_engine.py:2601-2605` (headless compaction) and `wakeup_service.py:173-174`
  both call `rollback()` on a shared session inside a broad catch. Same class as A, not on the
  tool path; cleared for this dossier but worth hardening.
- **FU-2** — `str(exc)` reaches model-visible tool content at `tool_registry.py:179` and
  `builtin_tools.py:154, 195, 384, 585, 665` for *every* exception type, not only DB errors.
  Pre-existing information-disclosure surface, independent of this fix.
- **FU-3** — MCP tools are advertised with a permissive `input_schema`
  (`builtin_tools.py:593`), so the new validation gate cannot enforce anything for them. They
  still gain truncation detection. Closed by the MCP tool-contract dossier
  (`docs/tasks/2026-07-22-mcp-tool-contract/`).
  **Amended at implementation:** that dossier has landed, so an MCP binding whose contract
  was captured by the "Test" probe now advertises the server's real schema
  (`builtin_tools.py:687-689`) and *does* get enforcement. The gap survives only for
  bindings never probed and for legacy rows, which still fall back to
  `{"type": "object", "additionalProperties": True}`. Note the inversion this creates: the
  captured schema is written by the MCP server, which is why D-9 strips its regex keywords.
- **FU-4** — No SRS entry states what the platform guarantees when a tool or a provider call
  fails mid-turn. The policy lives entirely in code comments.
- **FU-5** — `update_wakeup` and `cast_approval_vote` have **opposite transaction contracts**:
  the first depends on the reply commit, the second commits itself mid-round. That asymmetry is
  undocumented and is what makes Q-2 non-obvious.
- **FU-6** — `turn_engine.py` is ~3000 lines and concentrates the turn lifecycle, prompt
  assembly, compaction, tool dispatch and cancellation. Six confirmed findings from this audit
  are "guard present in one region, absent in a sibling region". Route to `check-quality`; this
  dossier deliberately does not split the module.
- **FU-7 — the UI half of Q-9 is still open.** The backend now emits `synthesis_failed` and
  `synthesis_error` on the message metadata, the observation metadata, the audit row and the
  `agent.finished` event, but no client reads them, so a marked reply renders identically to
  a clean one. The new error kinds (`database_error`, `provider_no_terminal_event`,
  `synthesis_failed`) also have no entry in `AGENT_ERROR_MESSAGE_KEYS`
  (`frontend/src/slices/conversation/constants/agentErrors.ts`) and would resolve to the
  generic fallback if they ever reached the `error` channel. Needs a conversation-slice
  change plus `$t()` strings in both locales.
- **FU-8** — the room path's failed-synthesis-with-no-text case emits `agent.finished` with a
  `reason` but no `error`, so the client shows no badge at all. That matches the previous
  `empty_reply` behaviour and is not a regression, but a provider outage that leaves nothing
  to say is arguably worth surfacing. Decide with FU-7.
- **FU-9** — `_chat_request` puts the caller's `messages` list into the payload by reference
  and the loop appends to it afterwards. Harmless at runtime (the request is consumed before
  the mutation) but it means any post-hoc assertion on `request.payload["messages"]` reads the
  mutated list. Pre-existing; preserved exactly by the commit-4 refactor.
- **FU-10** — `schema_violations` fails open, so an MCP server can opt its own tools out of
  validation by returning a schema jsonschema cannot build. Not a regression (before this
  work nothing was validated) and each tool's own guards still apply, but it means the gate
  is advisory for untrusted schemas rather than load-bearing.
- **FU-12 — stringified numbers are now hard-rejected.** `_UPDATE_WAKEUP_SCHEMA` and
  `_READ_SKILL_SCHEMA` type `every_n_messages` / `silence_minutes` / `offset` as
  `integer`, and `_opt_int` exists precisely because function-calling models emit `"30"`.
  With validation in front of dispatch, `{"silence_minutes": "30"}` is refused rather than
  coerced, and `read_skill`'s own "offset must be an integer" branch is unreachable via
  the registry. Deliberate — the schema is the advertised contract and the rejection names
  the fix — but it costs a round on a known model quirk. If it shows up in practice, widen
  those two schemas to `["integer", "string"]` rather than removing the gate.
- **FU-13 — `session.begin_nested()` flushes before issuing the SAVEPOINT**, so a pending
  ORM write that fails to flush would be logged as "isolated audit emit failed" and the
  caller told only its audit row was lost while its transaction is dead. Theoretical
  today: the codebase is Core-only (`session.add(` appears nowhere under `contexts/`), so
  there is no pending ORM state to flush. Worth narrowing the `try` if ORM-style writes
  ever land.
- **FU-11** — `test_web_search_degrades_on_missing_key`
  (`tests/unit/test_builtin_tools_wiring.py`) leaks an un-awaited coroutine and emits a
  `RuntimeWarning`. Verified against `629c321` as pre-existing: the real `WebSearchTool`
  runs against an `AsyncMock` session. Unrelated to this dossier.
</content>
