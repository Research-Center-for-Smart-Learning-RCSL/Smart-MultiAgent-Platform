---
type: bugfix
status: draft
created: 2026-07-22
requirements: []
depends_on: []
---

# An agent's `model_hint` does not constrain routing, so it silently runs on another provider

## 1. Summary

A key group may legally hold keys from several chat providers, and the provider router has
no vocabulary for the caller's provider intent: `ProviderRequest` carries no provider field,
so eligibility is filtered on carry and capability only and the first serviceable member by
priority wins. The turn engine compounds this by shipping a *map* of three models rather than
one, so whichever provider the router picks finds a default model waiting for it. An agent
configured `model_hint=claude, model_id=claude-opus-4-8` against a group whose priority-1 key
is OpenAI runs every turn on the platform default `gpt-5.4`, silently, with nothing in the UI
showing a discrepancy.

Two further consequences follow from the same root. The context budget is derived from
`model_hint` before routing happens, so a claude-hinted agent budgets a 200 000-token window
and may hand that to a 128 000-token OpenAI call; the resulting 400 is classified
non-retryable, so the correctly-hinted sibling key is never tried. And a 429 on the
priority-1 key rotates to a different provider mid-conversation, changing the agent's model
between turns with no misconfiguration required at all.

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-2 (critical, confirmed).

## 2. Observed vs Expected

- **Observed.**
  - `ProviderRequest` (`backend/contexts/keys/application/provider_router.py:76-87`) has
    fields for capability, payload, agent id, parent agent id, chatroom id and usage context
    — nothing that can express "this call must run on provider X".
  - `_load_eligible` (`:736-754`) filters on `list_ordered_carried` plus
    `capability not in _CAPS[key.provider]`, returning every chat key in priority order;
    `call` (`:355-377`) and `call_stream` (`:421-441`) take the first serviceable member.
  - Mixed-provider groups are first-class, not an edge case: `add_member` asserts only
    `LLM_CHAT` capability (`backend/contexts/keys/application/group_service.py:151-152`), and
    `GroupOut.providers` is documented as a *list* precisely because a group can hold several
    (`backend/app/api/v1/key_groups.py:56-59`).
  - `_resolve_models` (`backend/contexts/agents/application/runtime/turn_engine.py:235-240`)
    seeds from `_DEFAULT_CHAT_MODELS` (`:192` ←
    `backend/contexts/agents/domain/models.py:34-38`) and overrides only
    `models[agent.model_hint.value]`. The payload ships the map (`:2654-2655`, `:2731-2732`),
    and `resolve_model` (`backend/contexts/keys/infrastructure/adapters/base.py:96-110`)
    reads `models[provider.value]` for whichever provider won — no error, no warning.
  - Validation is create/patch-time only and never re-checked at turn time:
    `backend/contexts/agents/application/agent_service.py:227-231`, called at `:399-402` and
    at `:513-517` — and the patch call only fires when `key_group_id` or `model_hint` is in
    the request body, so *removing a key from the group* never re-validates any agent.
  - Compounding: `_context_limit_for` (`turn_engine.py:196-203`) keys strictly off
    `model_hint` and is consumed at `:1769` and `:2595`, long before the router selects a
    key. The resulting 400 is in `_ABORT_STATUSES`
    (`backend/contexts/keys/application/router_policy.py:26`), `classify_http` returns
    `RotationReason.ABORT` (`:55-56`), and `call_stream` raises
    `KeyGroupExhausted(reason="request_rejected")` without trying siblings
    (`provider_router.py:444-447`).

- **Expected.** An agent's turn runs on the provider and model its configuration names. If
  the bound key group cannot serve that provider, the turn fails visibly rather than
  substituting a different model.

  **Intent source.** `docs/implement/K-agent-runtime.md:49` — "model ID comes from the
  agent's configured model, never hardcoded". No `[Rxx.yy]` entry states the routing
  invariant directly, so `requirements: []` is a positive claim and bounds what this dossier
  appeals to; see FU-1. The expected behaviour additionally rests on the plain semantics of a
  user-facing configuration field: a `model_id` the runtime may ignore is not a setting.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | When the bound group holds no carried key of the hinted provider, should the turn hard-fail or fall back to another provider with a warning? | **Hard fail**, with an explicit skip reason. | Fall-back reintroduces the entire defect under a different name: the agent still silently runs the wrong model, only with a log line nobody reads. Decided without asking — a "silently wrong" option is not a real alternative. The precedent for a visible hard fail already exists at `turn_engine.py:1731-1748`, which audits `agent.turn_skipped`, commits, and emits a room-visible error. |
| Q-2 | Should the provider preference live on `ProviderRequest`, or be passed as a bare parameter to `_load_eligible`? | On `ProviderRequest`, typed `ApiKeyProvider \| None`. | Keeps the concept in the keys context's own domain vocabulary (`ApiKeyProvider` is already imported at `provider_router.py:55`), so the router gains no knowledge of the agents context. A bare parameter would work but would not be visible to the two other router entry points that need auditing against it. |
| Q-3 | Should the fix also re-derive the context limit after key selection, as a defence in depth? | **No.** Delete that idea; fix the root only. | The budget code is correct *given the invariant it assumes* — that the serving provider equals `agent.model_hint`. It is only wrong because the root defect breaks that invariant. Once routing honours the hint, `CONTEXT_LIMITS[hint]` is right by construction. Re-deriving post-selection would also require a two-phase router API, and it cannot help `_assemble_history` (`turn_engine.py:2597`, `:1769`), whose compaction decisions are *persisted* — a fold made against the wrong limit cannot be un-folded. |
| Q-4 | Should the turn engine keep shipping a `models` map, or a single `model`? | Single `model`. | `resolve_model` already prefers an explicit `"model"` (`adapters/base.py:103-104`), so no adapter change is needed. It also closes the surface twice over: even if the provider filter regressed, `resolve_model` would raise (`base.py:109`) rather than silently substitute a default. The map remains legitimate for the two GraphRAG extractors, which have no hint to honour. |
| Q-5 | Does this depend on any open dossier? | No. `depends_on: []`. | Checked against `BOARD.md`: `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`. Neither touches `provider_router.py`, `summariser.py`, or `turn_engine.py`'s model-resolution and payload-construction regions (the artifacts dossier works at `turn_engine.py:1133`). |
| Q-6 | Does this overlap the same-day agent-to-agent orchestration audit's dossiers? | No. | That audit's `2026-07-22-turn-idempotency-and-locking/` touches `turn_engine.py`'s A2A entry and locking paths, not model resolution or payload construction. Recorded here because the two audits' areas do intersect elsewhere; see this audit's Hand-off section. |

## 4. Reproduction

**Setup.**

1. In project P, upload an OpenAI key and a Claude key, both carried into P.
2. Create key group G in P.
3. `POST /api/key-groups/{G}/members` with the **OpenAI** key first — `next_priority`
   (`group_service.py:159`) assigns priority 1.
4. Add the **Claude** key — priority 2.
5. Create agent A with `model_hint="claude"`, `model_id="claude-opus-4-8"`,
   `key_group_id=G`. Create-time validation passes: the group *does* hold a carried Claude
   key (`agent_service.py:399-402`).
6. Bind A to a chatroom with an `every_n_messages` wake-up and post a message.

**Observed:** `_load_eligible` returns `[openai, claude]` (`provider_router.py:745-754`),
`call_stream` picks openai (`:421`), and `resolve_model` returns `models["openai"]` =
`"gpt-5.4"` (`base.py:107` ← `turn_engine.py:2655` ←
`backend/contexts/agents/domain/models.py:36`). The turn succeeds on the wrong model.

**Where to confirm which provider actually served:**

| Surface | What it shows |
|---|---|
| `key_usage_events` (authoritative) | One row per provider call carrying `key_id`, `agent_id`, `chatroom_id`; join to `api_keys.provider`. Written at `provider_router.py:261-273`. |
| Prometheus `provider_call_total{provider=...}` | Labelled with the *serving* provider (`provider_router.py:274-275`, `:492-498`). A claude-hinted agent incrementing `provider="openai"` is the defect in one metric. |
| `GET /api/projects/{pid}/keys/{kid}/usage` | Confirms the wrong key was billed; does not name the model (`backend/app/api/v1/project_keys.py:152-173`). |
| Audit `agent.turn_finished` | Records nothing useful — `{"tool_rounds": n}` only (`turn_engine.py:2188`). |
| Frontend | `AgentListView.isProviderMismatch` (`frontend/src/slices/agents/views/AgentListView.vue:89-96`) warns only when the group holds *no* key of the hinted provider. This reproduction case shows **no** warning. |

**Compounding-variant (the 400).** Same setup, then drive the room past roughly 130k tokens
of history with `context_mode=general`. `_context_limit_for` returns 200 000
(`turn_engine.py:203`), history is assembled to that budget, the OpenAI adapter 400s,
`classify_http` returns ABORT, and `call_stream` raises
`KeyGroupExhausted(reason="request_rejected")` at `provider_router.py:447` without touching
the Claude key. Observable as `key_group_exhausted_total{reason="request_rejected"}`.

**No-misconfiguration variant.** A group whose priority-1 OpenAI key 429s: `call_stream:421`
rotates to the Claude sibling and the agent silently changes model between turns.

## 5. Root Cause Analysis

| # | Link | Evidence |
|---|---|---|
| 1 | A key group may hold several chat providers — deliberate | `group_service.py:151-152`; `backend/contexts/keys/domain/providers.py:47-53` |
| 2 | The agent stores a hint, enforced only at create/patch | `agent_service.py:227-231`, `:399-402`, `:513-517` |
| 3 | **`ProviderRequest` cannot express provider intent** | `provider_router.py:76-87` |
| 4 | **So `_load_eligible` filters on carry + capability only** | `provider_router.py:736-754` |
| 5 | `call` / `call_stream` take the first serviceable member | `:355-377`, `:421-441` |
| 6 | The turn engine ships a three-model map, so any winner finds a model | `turn_engine.py:235-240`, `:2654-2655`, `:2731-2732` |
| 7 | `resolve_model` returns the default for the winning provider, silently | `adapters/base.py:96-110` |

**Root cause: links 3 and 4** — the router has no vocabulary for the caller's provider
intent. Link 1 is deliberate design. Link 6 is a symptom-amplifier rather than the cause:
even if `_resolve_models` returned only `{hint: model_id}`, `resolve_model` would raise
inside the adapter for a wrong-provider key (`base.py:109`), producing a transport-error
rotation instead of a wrong model — better, but still not correct routing.

**The budget defect is nested, not independent.** `_context_limit_for` is correct given the
invariant it assumes; it is wrong only because the root defect breaks that invariant. Fixing
the root makes it correct by construction. This is why Q-3 rejects a defensive re-derive: it
would blunt a symptom while leaving the agent on the wrong model.

**Aggravating factor.** `_ABORT_STATUSES` (`router_policy.py:26`) makes the over-budgeted 400
precisely the class of error the router refuses to rotate on, so the correctly-hinted key at
priority 2 is never reached (`provider_router.py:444-447`, and `:379-383` for the unary
path).

**Residual, explicitly out of scope.** `CONTEXT_LIMITS` is keyed per *provider*, not per
*model* (`backend/contexts/agents/domain/models.py:42-46`), so a small-window model within
the right provider still over-budgets. Pre-existing approximation; see FU-2.

## 6. Blast Radius and Sibling Suspects

Every router caller, audited:

| Call site | Verdict | Evidence |
|---|---|---|
| Turn engine, tool rounds | **Confirmed affected** | `turn_engine.py:2654-2673` |
| Turn engine, final no-tools synthesis | **Confirmed affected** — a second, independently-built payload that can land on a *different* provider than the tool rounds did | `turn_engine.py:2731-2749` |
| Turn engine, headless A2A path | **Confirmed affected** — same defect, second entry point | `turn_engine.py:697`, `:874-880` |
| `RouterSummariser` | **Confirmed affected, and worse — see below** | `backend/contexts/agents/application/runtime/summariser.py:32-56` |
| Headless compaction (`run_compaction`) | **Confirmed affected** — third entry into the summariser defect | `turn_engine.py:2595-2597` |
| GraphRAG `LlmTripleExtractor` | **Cleared — by design.** The map exists so "the router can pick whichever key it rotates to" (`triple_extractor.py:30-33`), and `adapters/base.py:16-17` documents the map for exactly this case. No hint exists to violate. | `backend/contexts/knowledge/infrastructure/triple_extractor.py:86-100` |
| Knowledge Map `DocTripleExtractor` | **Cleared — by design**, same reasoning, shares `_DEFAULT_EXTRACTION_MODELS` | `knowmap_triple_extractor.py:26,82-93` |
| RAG embedders | **Cleared** — `call_single_key` with a pinned key and an explicit single model; rotation deliberately absent because vector dimensions depend on the model (`provider_router.py:589-596`) | `backend/contexts/knowledge/infrastructure/embedders.py:70-77` |
| Rerankers | **Cleared** — same shape | `backend/contexts/knowledge/infrastructure/rerankers.py:63-75` |
| Prompt assistant | **Cleared, and it is the correct pattern**: it resolves the default from the *pinned key's own provider* — the thing `_resolve_models` should have done. Its comment at `:80-84` claims to mirror `_resolve_models`; it in fact improves on it. | `backend/app/workers/tasks/prompt_assistant.py:85-125` |

**The summariser is the worst instance.** `RouterSummariser` is constructed with
`models=_resolve_models(agent)` (`turn_engine.py:2546-2551`, and transitively `:2597`), ships
`{"models": ...}` at `summariser.py:48-49`, and calls the **unary** path
(`provider_router.py:338`, iterating all members at `:355-377`). Three reasons it is worse
than the turn path:

1. The unary path has a retry budget and a quota queue-wait (`provider_router.py:390-398`),
   so it churns through providers more aggressively than the single-pass streaming path.
2. Its output is **persisted and irreversible** — `run_compact` →
   `replace_range_with_summary` (`backend/contexts/agents/application/runtime/transcript.py:182-192`)
   folds a range permanently. A summary produced by the wrong model at the wrong quality is
   baked into room history. This compounds the compaction findings F-5 and F-7 of the same
   audit.
3. Unlike the GraphRAG extractors, it runs against the agent's own key group with the agent's
   own `key_group_id`, so there genuinely *is* a hint to honour.

Constructing `RouterSummariser` with `models=` at all is the wrong shape; it should take
`provider` + `model`.

**Not a routing defect but fixed for free.** `effort` (`turn_engine.py:2662-2663`,
`:2737-2738`) is mapped differently by each adapter —
`backend/contexts/keys/infrastructure/adapters/anthropic.py:170-173` (`output_config.effort`),
`openai.py:166-170` (`reasoning_effort`, gated on a reasoning-family check),
`gemini.py:116-119` (`thinkingConfig.thinkingLevel`). A claude-hinted agent's `effort`
currently lands in whichever of the three the router picked. Correct routing fixes this with
no adapter change.

**Nothing was persisted incorrectly** except compaction summaries produced by the wrong model
(see §7).

## 7. Fix Design

**1. Router — add provider intent to the request vocabulary**
(`backend/contexts/keys/application/provider_router.py`).

- Add `provider: ApiKeyProvider | None = None` to `ProviderRequest` (`:76-87`).
  `ApiKeyProvider` is already imported at `:55`, so the router gains no dependency on the
  agents context.
- `_load_eligible` (`:736-754`) gains a provider filter beside the existing capability filter
  at `:751`. `None` must preserve today's behaviour **verbatim** — the GraphRAG and Knowledge
  Map extractors depend on it.
- `call` (`:348`) and `call_stream` (`:416`) pass `request.provider` through.
- When the filter empties the list, the existing
  `raise KeyGroupExhausted(group_id=group_id, reason="no_members")` at `:350`/`:418` fires.
  Use a distinct reason (`"provider_unavailable"`) so the metric distinguishes "empty group"
  from "hint unserviceable". Note both `no_members` raises currently bypass
  `KEY_GROUP_EXHAUSTED_TOTAL` while every other exhaustion path increments it (`:382`, `:388`,
  `:391`, `:446`, `:452`, `:454`) — a pre-existing observability gap worth closing while in
  the file.

**2. Turn engine — send one model, not three**
(`backend/contexts/agents/application/runtime/turn_engine.py`).

- Replace `_resolve_models` (`:235-240`) with a resolver returning `(provider, model)`:
  `ApiKeyProvider(agent.model_hint.value)` — total, since `AgentModelHint`'s values
  (`backend/contexts/agents/domain/models.py:12-15`) are a strict subset of `ApiKeyProvider`'s
  (`backend/contexts/keys/domain/providers.py:31-35`), so no mapping table is needed — and
  `agent.model_id or DEFAULT_CHAT_MODELS[agent.model_hint.value]`.
- Payloads at `:2654-2655` and `:2731-2732` carry `"model"` instead of `"models"`.
  `resolve_model` already prefers an explicit `"model"` (`adapters/base.py:103-104`), so **no
  adapter change is required**.
- `ProviderRequest(...)` at `:2665-2671` and `:2740-2746` gain `provider=`.
- Delete `_resolve_models` and `_DEFAULT_CHAT_MODELS` (`:192`) if nothing else reads them —
  do not leave a vestigial map-builder.

**3. Summariser** (`backend/contexts/agents/application/runtime/summariser.py:32-56`) — the
constructor takes `provider` + `model` instead of `models`; the payload at `:48-49` ships
`"model"`; `ProviderRequest` at `:46` gains `provider=`. Two construction sites to update:
`turn_engine.py:2546-2551` and transitively `:2597`.

**4. Turn-time precondition**, beside `_key_group_out_of_scope` (`turn_engine.py:1690-1700`).
Call `KeysFacade(self._db).has_carried_provider_in_group(agent.key_group_id, agent.model_hint.value)`
— the same facade port `agent_service.py:228` already uses, so no new query and no SoC
violation. Invoke at `:1731` (room path) and `:688` (headless path) with the shape those taps
already use: audit `agent.turn_skipped {reason: "model_hint_unserviceable", model_hint, key_group_id}`,
commit, then `emit_agent_finished_error(...)` or `_emit_observation_event` for observers
(`:1742-1747`). This converts an opaque `KeyGroupExhausted` into a diagnosable skip; it does
**not** on its own prevent cross-provider routing, so it complements step 1 rather than
replacing it.

**5. Do not touch `_context_limit_for`** (`:196-203`). Per Q-3, it becomes correct once the
invariant holds. Leaving it untouched is the point.

**Why this corrects rather than masks.** The defect is a missing invariant, not a missing
guard. After the fix, `serving provider == agent.model_hint` holds at the router chokepoint —
the single place every agent provider call passes through — and every downstream property
(model identity, `effort` mapping, context window, compaction budget, usage attribution)
becomes derivable from it.

**Data repair: none required, one caveat.** No schema migration and no rewrite: `model_hint`
and `model_id` already hold the correct intent, and the fix makes the runtime obey values
already present. The caveat is compaction summaries already produced by the wrong model —
these are persisted and cannot be un-folded (see F-5/F-7 of the same audit, whose dossier owns
transcript repair). Not repairable here; recorded as FU-3.

**Pre-deploy detection query** for the cohort that will start hard-failing — mirrors the join
in `has_carried_provider_in_group` (`backend/contexts/keys/interfaces/facade.py:171-173`):

```sql
SELECT a.id, a.name, a.project_id, a.model_hint, a.key_group_id
FROM agents a
WHERE a.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM key_group_members m
    JOIN api_keys k      ON k.id = m.key_id AND k.deleted_at IS NULL
    JOIN key_groups g    ON g.id = m.group_id
    JOIN key_projects kp ON kp.key_id = k.id AND kp.project_id = g.project_id AND kp.carried
    WHERE m.group_id = a.key_group_id AND k.provider::text = a.model_hint::text
  );
```

## 8. Regression Test Plan

**The failing test comes first** — `test_load_eligible_filters_to_requested_provider` in
`backend/tests/unit/test_provider_router_carry_gate.py`, whose `_FakeMembersRepo` /
`_FakeKeysRepo` / `_router()` harness (`:34-67`) is exactly what is needed. Carried =
`[openai_kid, claude_kid]`; call `_load_eligible(gid, LLM_CHAT, provider=CLAUDE)`; assert only
`claude_kid` returns. **Fails today**: `_load_eligible` takes no `provider` argument, so this
is a `TypeError`. Note every key in both existing tests in that file is `OPENAI` (`:79-81`,
`:97`) — provider selection is entirely untested today.

Then:

- `test_load_eligible_empty_when_provider_absent` — carried `[openai_kid]`, request CLAUDE,
  assert `[]`.
- `test_load_eligible_provider_none_preserves_current_behaviour` — pins the GraphRAG
  contract. Must pass **before and after**; this is the test that stops the fix from breaking
  the knowledge builders.
- `backend/tests/unit/test_provider_router_streaming.py` (harness `_make_router` at `:97-128`
  already accepts an adapters dict keyed by provider):
  `test_stream_does_not_route_to_unhinted_provider` — members `[openai(prio1), claude(prio2)]`
  both working, request CLAUDE, assert the claude adapter produced the tokens and the openai
  adapter was never entered. **Fails today** — this is the whole defect in one assertion.
  `test_429_rotation_stays_within_provider` — members `[claude_a(429), openai(200),
  claude_b(200)]`, request CLAUDE, assert the stream came from `claude_b`.
  `test_exhausted_when_no_member_matches_provider`.
- `backend/tests/unit/test_agent_turn_loop.py` — `_FakeRouter.requests` (`:30`, `:34`) is
  already captured and currently unasserted:
  `test_payload_carries_agent_model_and_provider` — assert
  `req.payload["model"] == "claude-opus-4-8"`, `req.provider is ApiKeyProvider.CLAUDE`, and
  `"models" not in req.payload`. **Fails today**: the payload carries the three-way map and no
  `provider` attribute exists.
  `test_final_no_tools_call_carries_same_provider` — assert `requests[-1]` matches
  `requests[0]`, pinning the second independently-built payload at `:2731-2746`.
- **New file** `backend/tests/unit/test_summariser_routing.py` — no `test_summaris*` file
  exists anywhere under `backend/tests/`. `test_summariser_pins_provider_and_model` asserts
  the `ProviderRequest` reaching a fake router carries `provider` and a scalar `"model"`.
  **Fails today**: `summariser.py:48-49` ships `"models"` and `:46` sets no provider.
- `backend/tests/unit/test_provider_adapters.py` (which already has
  `test_resolve_model_required` at `:526`) —
  `test_resolve_model_rejects_map_missing_the_serving_provider`, a characterization test
  documenting the belt-and-braces property the fix relies on (`base.py:108-109`).
- `backend/tests/unit/test_a2a_turn_dispatch.py` (already fakes `AgentsFacade` and
  `KeysFacade` for the `_key_group_out_of_scope` tap) —
  `test_turn_skipped_when_hint_unserviceable`: `has_carried_provider_in_group` returns False;
  assert `TurnResult(status="skipped", reason="model_hint_unserviceable")` and that **no
  router call was made**.

## 9. Risks and Rollback

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **R1** — agents whose group no longer holds the hinted provider hard-fail. They work today, wrongly, on a sibling. The create-time invariant rots silently because removing a key from a group never re-validates any agent (`agent_service.py:513-517` only fires when `key_group_id` or `model_hint` is in the PATCH body). | unknown but real | high | Run the §7 detection query before deploy — the count is the exact blast radius. `AgentListView.isProviderMismatch` (`AgentListView.vue:89-96`) already surfaces this cohort in the UI. The step-4 precondition makes the failure loud and self-diagnosing rather than an opaque stall. |
| **R2** — reduced rate-limit headroom. A mixed group currently gives any agent two keys' worth of headroom; after the fix a claude-hinted agent has only the Claude key. | moderate | medium | This is the defect correctly removed — cross-provider fallback that silently changes the model is undisclosed substitution, not headroom. It will be *experienced* as a regression, so the release note must say so and document the remedy (add a second key of the same provider). Note the streaming path has no quota queue-wait at all (`provider_router.py:411`), so a stream hitting quota fails immediately, while the unary summariser path does wait (`:390-398`). |
| **R3** — cohort-1 agents visibly change model, shifting output style, cost and latency. | certain for that cohort | low-medium | The fix working as intended. Enumerate with the detection query inverted (group has >1 distinct chat provider AND priority-1 provider ≠ hint). |
| **R4** — GraphRAG / Knowledge Map regression if `provider=None` does not exactly preserve current behaviour. Both extractors swallow `KeyGroupExhausted` and return `[]` (`triple_extractor.py:101-107`), so the failure is a **silent** knowledge-graph degradation. | low | high if it happens | The `provider_none_preserves_current_behaviour` test exists specifically to pin this. |
| **R5** — summariser signature change ripples. | low | low | Two call sites, both in `turn_engine.py`; `context.Summariser` is a Protocol. Verify with `mypy .`. |

**Rollback.** Clean. No migration, no schema change, nothing written differently —
`key_usage_events`, `compact_summary` rows and audit records keep their shapes; the only new
values are additional `reason` strings, which consumers treat as opaque text. Reverting
restores prior behaviour exactly. Compaction summaries produced during the fix window remain
valid, since they were produced by the *correct* model — the reverse is not true, which is an
argument for deploying rather than delaying.

**No feature flag.** A flag would mean shipping a configuration in which the agent's declared
model is advisory, and the flag becomes the thing nobody dares flip. If a staged rollout is
required, stage it by running the detection query and repairing cohort R1's key groups first —
the population that would break is knowable in advance.

## 10. Acceptance Criteria

- [ ] AC-1: `test_load_eligible_filters_to_requested_provider` (§8) fails against current code
      and passes after the fix.
- [ ] AC-2: an agent bound to a mixed-provider key group runs its turn on the provider named
      by `model_hint`, regardless of member priority order.
- [ ] AC-3: the provider payload carries a single `"model"` equal to the agent's `model_id`
      (or that provider's default when unset), and no `"models"` map.
- [ ] AC-4: the final no-tools synthesis call uses the same provider and model as the tool
      rounds of the same turn.
- [ ] AC-5: a 429 on one key rotates only to keys of the same provider; the agent's model
      never changes mid-conversation.
- [ ] AC-6: when the bound group holds no carried key of the hinted provider, the turn is
      skipped with `agent.turn_skipped {reason: "model_hint_unserviceable"}`, a room-visible
      error is emitted, and **no** provider call is made.
- [ ] AC-7: `RouterSummariser` issues its request with a pinned provider and a scalar model.
- [ ] AC-8: `provider=None` preserves current routing behaviour exactly — the GraphRAG and
      Knowledge Map triple extractors are unaffected, pinned by a test that passes both before
      and after.
- [ ] AC-9: the pre-deploy detection query in §7 runs and its result is recorded in the
      dossier's Deviation Log before rollout.
- [ ] AC-10: `pytest -q`, `ruff check .`, `ruff format --check .` and `mypy .` pass in
      `backend/`.

## 11. SRS Delta

None. This restores behaviour the platform's own implementation notes already claim
(`docs/implement/K-agent-runtime.md:49`). See FU-1: the absence of an `[Rxx.yy]` entry stating
the routing invariant is itself worth correcting, but that is a documentation change, not a
behavioural one, and does not belong in a bugfix delta.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — No `[Rxx.yy]` entry states that an agent's turn must run on its configured
  provider and model. The invariant lives only in `docs/implement/K-agent-runtime.md:49`. An
  SRS entry would give future audits something to judge against.
- **FU-2** — `CONTEXT_LIMITS` is keyed per provider, not per model
  (`backend/contexts/agents/domain/models.py:42-46`), so a small-window model within the
  correct provider still over-budgets. Pre-existing approximation, untouched by this fix.
- **FU-3** — Compaction summaries already produced by the wrong model are persisted and
  cannot be un-folded. Transcript repair belongs to the compaction dossier covering F-5 and
  F-7 of the same audit; recorded here so the connection is not lost.
- **FU-4** — `turn_engine.py` builds two near-identical payloads at `:2654-2664` and
  `:2731-2739` with duplicated `effort` and `_sampling_payload` handling. This fix touches
  both; extracting a single `_chat_payload(...)` helper would stop the next change having
  three copies to keep in sync.
- **FU-5** — `adapters/base.py`'s canonical-payload docstring (`:10-29`) documents `"model"`
  OR `"models"` as equally valid for `LLM_CHAT`. After this fix the map is legitimate only for
  the two knowledge-graph extractors; `:16-17` should say so, or the next author reintroduces
  it.
- **FU-6** — Both `no_members` raises (`provider_router.py:350`, `:418`) bypass
  `KEY_GROUP_EXHAUSTED_TOTAL` while every other exhaustion path increments it. Pre-existing
  observability gap; cheap to close while in the file.
- **FU-7** — `AgentListView.isProviderMismatch` warns only when the group holds *no* key of
  the hinted provider. Extending it to "your group holds other providers too" would pre-empt
  the R3 cohort's surprise before deploy rather than after.
</content>
