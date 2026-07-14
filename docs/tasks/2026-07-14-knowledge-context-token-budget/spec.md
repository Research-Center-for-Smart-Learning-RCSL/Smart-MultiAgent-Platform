---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R9.10, R11.19, R10.09, R11.14]
---

# Turn assembly has no whole-request token budget (F-16, F-17)

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-16, F-17).

This dossier fixes two findings that share one root: the TurnEngine never measures the
*assembled* provider request. F-16 is the missing cross-block budget across the three
knowledge sources; F-17 is the compaction decision that budgets stored history instead of
the next request. Both are corrected by the same "size the whole payload, then distribute
a knowledge budget by precedence" change, so they are specced together.

## 1. Summary

When an Agent takes a room turn, the engine appends the File RAG, Concept Map, and
Knowledge Map blocks to the system prompt without any combined size limit (F-16), and it
decides whether to run `/compact` from the stored history token sum alone — before the
system prompt, retrieved knowledge, and tools even exist (F-17). The result is two
user-visible failures on the same code path: an Agent that combines knowledge sources can
overflow the provider context limit and fail the turn, and a `compact`-mode Agent with a
large prompt/knowledge/tool prefix silently sails past its configured safety cap and hits
an avoidable provider limit error. Both stem from the engine never counting the request it
is about to send.

## 2. Observed vs Expected

### F-16 — no combined knowledge budget

- **Observed** — `_run_locked` appends all three knowledge blocks to `system_parts`
  unconditionally (`backend/contexts/agents/application/runtime/turn_engine.py:942-950`),
  which are joined into one `system_text`
  (`turn_engine.py:1032`) and sent as the payload `system` field
  (`turn_engine.py:1605`). The File RAG block has **no** size cap: `_format_rag_block`
  renders every selected chunk's full body
  (`backend/contexts/knowledge/application/rag_context_provider.py:143-148,183-200`), and
  `cfg.top_k` is validated only `gt=0, le=100`
  (`backend/app/api/v1/rag.py:78,83`) — up to 100 full chunks. The two graph blocks each
  get an **independent** 2 KB byte cap (`_cap_to_2kb`,
  `backend/contexts/knowledge/domain/graphrag.py:298-316`; entry via
  `as_system_message`, `graphrag.py:265-272`). No code measures or bounds the three blocks
  together, and nothing enforces the narrow-scope precedence at allocation time.
- **Expected** — [R11.19]: "When several knowledge blocks (File RAG, Knowledge Map, and
  Concept Map layers) are injected in one turn, their combined size is bounded with
  narrow-scope precedence." [R10.09]/[R11.14] fix the blocks as system-role context
  injected each turn, so the bound must live at turn assembly.

### F-17 — compaction budgets history, not the next request

- **Observed** — `_assemble_history` computes `projected = sum(h.token_count for h in
  history)` (`turn_engine.py:1460`) and passes only that to `ctxmod.should_compact`
  (`turn_engine.py:1466-1471`; `backend/contexts/agents/application/context.py:95-108`).
  It is called at `turn_engine.py:924`, before the base/dynamic system blocks
  (`turn_engine.py:928-938`), retrieved knowledge (`:942-950`), and tools (`:960-968`) are
  assembled. The final payload is built at `turn_engine.py:1603-1613` and dispatched at
  `turn_engine.py:1622` with **no** re-count of `system_text` + `messages` + `tools`.
- **Expected** — [R9.10]: compaction runs "when the running token count of the *next*
  request would exceed `context_token_cap`". The *next request* is the assembled payload
  (system prompt + knowledge + tools + history + response reserve), not the stored history
  in isolation.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Package F-16/F-17 as one dossier or separate? | One dossier (F-18 separate). | Both are fixed by one "measure the assembled request, distribute a knowledge budget" change in `turn_engine.py`; splitting would duplicate the payload-accounting design. |
| Q-2 | F-16 scope: full allocator or cap File RAG only? | Full token-aware allocator with precedence. | Matches [R11.19] narrow-scope precedence; capping only File RAG leaves the graph blocks unbounded-in-aggregate and gives no precedence guarantee. |
| Q-3 | When the assembled request still exceeds the cap, what gives? | Compact history first, then trim knowledge via the allocator. | Preserves recent conversation turns and sheds least-specific knowledge last; reuses the existing compaction mechanism. |

Precedence order (narrowest scope wins budget): **Concept Map (`_graphrag_context`) >
Knowledge Map (`_knowmap_context`) > File RAG (`_rag_context`)**. [R11.19] pins only the
*principle* (narrow-scope precedence) and the audit establishes File RAG as the least
specific — "File RAG consumes the space that the documented narrow-scope precedence
reserves for more specific knowledge" — so File RAG yielding first is SRS-anchored. The
relative Concept Map vs Knowledge Map order is a design decision recorded here, not an SRS
mandate; it is low-stakes because each graph block is individually ≤2 KB, so the dominant
budget lever is capping File RAG. Note the finding text swaps the "graph"/"concept" labels
relative to the method names; the code names are authoritative — `_graphrag_context` is the
room-scoped Concept Map (`turn_engine.py:1720-1742`) and `_knowmap_context` is the Axis-1
Knowledge Map (`turn_engine.py:1744-1755`, [R11.14]).

## 4. Reproduction

**F-16** (deterministic, unit-level): configure an Agent attached to a File RAG config
with `top_k=100` over a corpus of ~512-token chunks, plus a Concept Map and a Knowledge
Map that each return a full 2 KB block. Drive a room turn; the assembled `system_text`
plus history exceeds the provider context limit and the turn fails, or File RAG crowds out
the higher-precedence graph blocks. Observable via the token estimate of the assembled
`system_text` (`estimate_tokens`, `backend/contexts/agents/application/runtime/transcript.py:96-113`).

**F-17** (deterministic, unit-level): set an Agent to `context_mode=compact` with
`context_token_cap=96000`; seed history summing to ~90k `token_count` and a base
prompt + knowledge + tools prefix estimating ~20k. `should_compact` sees 90k < 96k and
skips compaction (`turn_engine.py:1466-1471`), but the assembled request is ~110k+reserve,
exceeding the cap and the provider limit.

## 5. Root Cause Analysis

Both symptoms share one root: **the engine sizes inputs in isolation and never sizes the
request it assembles.**

1. History is projected and the compaction decision is made at `turn_engine.py:924,1460`,
   using only `sum(h.token_count)` — a value that excludes the base system block, the three
   knowledge blocks, tools, and the response reserve (`_DEFAULT_MAX_TOKENS = 4096`,
   `turn_engine.py:83`). This is the F-17 root: compaction is gated on the wrong quantity.
2. The three knowledge providers are queried and appended independently
   (`turn_engine.py:942-950`); each caps only its own block (File RAG not at all; graph
   blocks at 2 KB each via `_cap_to_2kb`). No component owns the *aggregate* knowledge
   budget or the precedence ordering. This is the F-16 root: there is no allocator.
3. Because (1) decides compaction before (2) has run, and neither re-counts the final
   `system_text` + `messages` + `tools` before dispatch (`turn_engine.py:1603-1622`), the
   assembled request can exceed both `context_token_cap` and the provider hard limit.

Root cause (both): the compaction/budget decision is not derived from a single estimate of
the fully-assembled payload, and knowledge assembly has no budget to consume. Aggravating
factor: the shared estimator is a coarse heuristic (CJK=1 token, Latin `len//4`;
`transcript.py:96-113`), so any budget must carry a safety margin.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every room turn for an Agent that (a) combines knowledge sources
  (F-16) or (b) runs `context_mode=compact` with a non-trivial system/knowledge/tool
  prefix (F-17). Headless turns (`run_input_turn`, `turn_engine.py:404-487`) do not append
  knowledge today (that omission is F-15, specced separately) but *do* reuse the same
  history-only compaction seam, so F-17 affects them once knowledge assembly is added
  there; this fix should make the headless path benefit automatically.
- **Sibling suspects**:
  - *Forced `/compact` branch* (`turn_engine.py:1463-1465`) caps at `projected // 2` from
    the history projection — **cleared**. This is a user-initiated "shed half of history
    now" action (the one-shot flag, G.10), semantically history-based and independent of
    whether the next request fits; it is not the F-17 defect and stays as-is.
  - *`context_mode=general`* ([R9.09]) intentionally does **not** compact history and
    surfaces provider context-limit errors to the UI — that history behavior is unchanged.
    But R11.19 has no mode qualifier: the **knowledge allocator (F-16) still applies in
    general mode** to bound the three blocks. **Confirmed** in scope for F-16, out of scope
    for the F-17 history-compaction change.
  - *The two graph blocks' internal 2 KB byte caps* — **cleared** as correct sub-caps; the
    allocator sits above them and may reduce their budget further, but the byte cap stays
    as a floor guard.
  - *Response reserve* — `_DEFAULT_MAX_TOKENS = 4096` (`turn_engine.py:83`) must be
    included in the payload estimate; today it is omitted from every count — **confirmed**
    contributor, fold into the estimate.

## 7. Fix Design

Introduce a single whole-payload accounting step in `_run_locked` and thread a token
budget into the knowledge providers. The fix corrects the root (measure the assembled
request) rather than masking either symptom.

**A. Budget model (new, in the agents runtime).** Add a small pure helper (e.g.
`context.py`, beside `should_compact`/`default_cap_from_limit`,
`backend/contexts/agents/application/context.py:88-108`) that returns the token budget
available for knowledge, given a **request ceiling**, the response reserve, and the
estimated token cost of the *fixed* turn context — that is **all non-knowledge
`system_parts`** (base prompt plus the observer, compact-summary, staged-file, notify, and
participant-label blocks appended around `turn_engine.py:928-1031`), the tools, and the
history. Counting only `base_system` would let those other system blocks silently consume
the knowledge budget and reintroduce overflow. The ceiling differs by mode:
`context_token_cap` (or its `default_cap_from_limit(provider_limit)` 75% default) in
`compact` mode; the provider hard limit (`context_limit`, `turn_engine.py:902`) in
`general` mode — so R11.19's knowledge bound holds in both modes without imposing R9.10's
compaction on `general`. Estimation uses the existing coarse `estimate_tokens`
(`transcript.py:96-113`) with a documented safety margin.

**B. F-17 — compact on the assembled request (compact mode only).** Restructure
`_run_locked` so assembly order becomes: base system + tools + history estimate →
compaction decision → knowledge budget → knowledge blocks. Feed the compaction decision the
whole-payload estimate (base + tools + reserve + history), not history alone. This changes
only the `compact`-mode branch of `_assemble_history` (`turn_engine.py:1466-1474`); the
`general`-mode return-history-unchanged path and the forced `/compact` half-shed
(`:1463-1465`) are untouched. If, after compaction, base + tools + history + reserve
already meets or exceeds the ceiling, the knowledge budget is zero and the
higher-precedence blocks are dropped first (see C). Add a final re-estimate of `system_text`
+ `messages` + tools + reserve immediately before the initial dispatch (round 1 of the tool loop;
mid-loop growth is FU-4) (`turn_engine.py:1603-1622`); if
it still exceeds the provider hard limit, run one additional compaction pass in `compact`
mode, or (in `general` mode) let the provider's own context-limit error surface to the UI
per [R9.09] — rather than silently dispatching a guaranteed-overflow request.

**C. F-16 — precedence allocator over the knowledge blocks (all modes).** In both context
modes, the engine distributes the knowledge budget from A across the three sources in
precedence order Concept Map >
Knowledge Map > File RAG, and passes a per-source `token_budget` into each provider query
(`_graphrag_context`/`_knowmap_context`/`_rag_context`,
`turn_engine.py:1712-1755`). Each provider truncates its **own** rendered block to its
budget — SoC-correct, since only the provider holds the structured chunks/triples:
- `RagContextProvider.query` gains a token budget and drops lowest-score chunks (and, as a
  last resort, truncates the final chunk body) instead of the current uncapped
  `_format_rag_block` (`rag_context_provider.py:143-200`). This is the block that most
  needs it.
- The two graph providers gain a token budget that overrides the hard-coded 2 KB when the
  allocator grants less; the existing `_cap_to_2kb` binary-search truncation
  (`graphrag.py:298-316`) is the model for source-preserving trimming.
- "Source-preserving" = prefer trimming within a block over dropping a whole source;
  higher-precedence sources are truncated only after lower ones are exhausted, and a source
  granted zero budget is omitted rather than sent empty.

**Data repair** — none; no persisted data is wrong. This is a runtime assembly defect.

## 8. Regression Test Plan

Tests are written first and must fail against current code.

- **F-16** — new `backend/tests/unit/test_turn_context_budget.py`: build a TurnEngine with
  fake providers returning oversized File RAG + Concept Map + Knowledge Map blocks under a
  small cap; assert (1) `estimate_tokens(system_text)` ≤ the knowledge budget, (2)
  precedence — the Concept Map block survives intact while the File RAG block is truncated
  first, (3) a source granted zero budget is absent, not empty. Fails today because
  `_run_locked` appends all blocks uncapped (`turn_engine.py:942-950`).
- **F-17** — extend `backend/tests/unit/test_context_compaction.py`: an Agent in
  `compact` mode whose history sums below the cap but whose base + knowledge + tools +
  reserve pushes the assembled estimate over the cap must trigger compaction. Fails today
  because `should_compact` sees only `sum(h.token_count)` (`turn_engine.py:1460-1471`).
- **F-17 guard** — assert the pre-dispatch estimate of the full payload never exceeds the
  provider hard limit for a pathological large-prefix Agent.

## 9. Risks and Rollback

- **Coarse estimator** — `estimate_tokens` is heuristic; an under-count could still
  overflow. Mitigation: carry a documented safety margin in the budget helper and keep the
  graph blocks' 2 KB byte cap as a floor. Real tokenizer parity is out of scope (FU).
- **Behavior change for existing Agents** — Agents that currently overflow will now see
  truncated knowledge or extra compaction instead of a hard error; this is the intended
  [R11.19]/[R9.10] behavior but is observable. Precedence truncation must be deterministic
  so results are reproducible.
- **SoC** — the allocator lives in the agents runtime and only distributes budgets;
  per-block truncation stays inside each knowledge provider. Do not move chunk/triple
  structures up into the agents layer.
- **Rollback** — revert the `_run_locked` reordering and the providers' `token_budget`
  parameters; the change is additive (new optional params default to today's behavior),
  so rollback is clean.

## 10. Acceptance Criteria

- [ ] AC-1: the F-16 regression test in §8 fails before the fix and passes after.
- [ ] AC-2: the F-17 regression test in §8 fails before the fix and passes after.
- [ ] AC-3: the assembled `system_text` token estimate for a multi-source turn is bounded
      by a knowledge budget derived from `context_token_cap`/provider limit minus base
      system + tools + history + response reserve.
- [ ] AC-4: the knowledge budget is distributed in precedence order Concept Map > Knowledge
      Map > File RAG in **both** context modes; File RAG is truncated before the graph
      blocks, and a zero-budget source is omitted (not sent empty).
- [ ] AC-5: `context_mode=compact` triggers compaction from the estimated *assembled*
      request (base + knowledge + tools + history + response reserve), not from stored
      history alone. The forced `/compact` half-shed (`turn_engine.py:1463-1465`) is
      unchanged.
- [ ] AC-6: a re-estimate before the initial dispatch guards the provider hard limit; a
      pathological large-prefix compact-mode Agent runs an additional compaction pass rather
      than dispatching a guaranteed-overflow request. (Mid-tool-loop growth is FU-4.)
- [ ] AC-7: `context_mode=general` keeps unbounded history and still surfaces provider
      context-limit errors to the UI ([R9.09] — no history compaction added), but the
      knowledge allocator (AC-3/AC-4) still bounds its three knowledge blocks per [R11.19].
- [ ] AC-8: `pytest -q`, `ruff check .`, `ruff format --check .`, and `mypy .` pass in
      `backend/`.

## 11. SRS Delta

None. The fix restores [R11.19] (combined bound with narrow-scope precedence) and [R9.10]
(compact on the *next request*), both already documented.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- FU-1: `estimate_tokens` is a coarse heuristic (CJK=1, Latin `len//4`,
  `transcript.py:96-113`). A tokenizer-backed estimator would let the safety margin shrink;
  out of scope here.
- FU-2: the three knowledge providers share no common protocol (return `RagContext` vs
  `str`; `rag_context_provider.py:62-70`, `graphrag_context_provider.py:108-115`,
  `knowmap_context_provider.py:139-146`). A shared `KnowledgeProvider` port carrying the
  budget parameter would reduce the per-provider wiring; consider after this fix.
- FU-3: File RAG `top_k` up to 100 (`rag.py:78,83`) remains a large default fetch even
  with budgeting; revisit whether the API ceiling should drop once aggregate budgeting
  lands.
- FU-4: this fix budgets the **initial** assembled request (round 1). `_stream_with_tools`
  then loops up to `MAX_TOOL_ROUNDS`, appending each round's assistant tool-use turn and
  tool results to `messages` and re-dispatching with the same `system_text`
  (`turn_engine.py:1600-1648`), so a turn that fit at round 1 can still exceed the provider
  limit once large tool outputs accumulate. Mid-loop tool-result growth is a distinct
  overflow vector (out of scope for R9.10/R11.19 as filed); a per-round guard or
  tool-result trimming should be scoped separately.
