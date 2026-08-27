---
type: feature
status: approved
created: 2026-08-27
requirements: [R9.03a, R9.09, R9.10, R9.10a]
depends_on: []
---

# Per-model provider capability table

## 1. Summary

The platform describes provider models with three parallel per-*provider* dictionaries and,
separately, with five per-*model* regular expressions scattered across three adapters. Neither
carries the fact a caller actually needs: what this specific model accepts. The consequences are
live in production. A Claude agent is granted a 200 000 token context window when the model it
runs on takes 1 000 000, so knowledge is withheld from turns that had room for it. An agent whose
model refuses a parameter the agent-config form freely offers fails every turn, with copy that
says only "the agent run failed". This task replaces both mechanisms with one per-model capability
record, refreshes all three providers' model lists against current provider documentation,
surfaces the capabilities through the existing model-catalog endpoint, and uses them to disable
the controls a chosen model does not accept.

Occasioned by a production incident on staging: agent `結書` (`be439d1e`) failed every turn for
two days with `provider_exhausted:request_rejected`. Root cause was `effort = 'low'` on an OpenAI
model that refuses `reasoning_effort` alongside function tools. Commit `e16bc90` stops the request
being built; this task stops the setting being offered.

## 2. Goals and Non-goals

**Goals**

- One authoritative per-model record holding context window and per-parameter acceptance, replacing
  `CHAT_MODEL_CATALOG`, `DEFAULT_CHAT_MODELS`, `CONTEXT_LIMITS` and the five adapter regexes.
- Correct context windows per model, so `_context_limit_for` stops answering by provider alone.
- Current model lists for all three providers.
- The agent-config form disables, and explains, a control the selected model will not accept.
- A conservative capability floor for a model id that is not in the table, so a BYO-key user may
  still name a model the platform has never heard of without that model failing every turn.
- `AgentEffort` widened to the values providers now accept.

**Non-goals**

- Migrating the OpenAI adapter to `/v1/responses`. That is
  `2026-08-27-openai-responses-api-migration`, which depends on this task.
- Restricting agents to catalogue models. A BYO-key user must be able to name a model released
  after the platform last shipped (Q-2).
- Any change to embedding models or the `embedding` half of the model-catalog response.
- Automatic capability discovery at runtime. Reconciliation against provider `models` endpoints is
  an operator tool, never a request-path call (Q-4).
- Repairing existing agent rows whose `effort` is set on a model that refuses it. `e16bc90` already
  makes those turns succeed; the setting is inert rather than harmful. Recorded as FU-3.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How many dossiers for capability table, UI honesty, and the Responses API migration? | Two: this one covers the table plus the UI; the Responses API migration is a separate dependent dossier. | The capability table is a prerequisite for the migration (the migration needs to know which models are Responses-only), and splitting lets the production-relevant half ship without waiting on an adapter rewrite and its streaming path. |
| Q-2 | What capabilities does a model id outside the table get? | A conservative floor: no `effort`, no sampling controls, and the provider's *lowest* catalogued context window. | The failure this task exists to prevent is a parameter the model refuses. Sending fewer parameters loses a setting; sending one too many loses every turn. The cost is that a genuinely capable new model is under-served until someone adds a row, which is a documentation task rather than an outage. |
| Q-3 | Widen `AgentEffort` (currently `low`/`medium`/`high`) in this task? | Yes, in the same migration. | The enum is a PostgreSQL type (`0039_agent_effort.py:27`), so widening it later is a second migration over the same column. The capability table is also the only place that can state which values a given model accepts, so the two land naturally together. |
| Q-4 | How is the table kept from going stale again? | Both: a data file with per-entry source URL and verification date, and an operator command that reconciles it against each provider's `models` endpoint. | The current lists carry `Verified against each provider's official model docs in 2026-06` (`domain/models.py:24-27`) and went stale anyway, because nothing re-reads a comment. The reconciler needs provider keys, so it is a `smap` CLI command, not a CI gate. |
| Q-5 | Does this depend on `2026-07-07-graphrag-two-axis-redesign`, which also edits `AgentDetailView.vue`? | No. | That blueprint edits the Knowledge tab (`:935-1000`) and the graphrag field on the General tab (`:820-855`); this task edits the model select (`:247-262`), the context-cap bound (`:476`) and the effort field (`:827-836`). Disjoint regions. Its own status is additionally in question (see `BOARD.md`, Ready now). |

## 4. Current State

### 4.1 Three per-provider dictionaries

`backend/contexts/agents/domain/models.py:28-46` defines `CHAT_MODEL_CATALOG`,
`DEFAULT_CHAT_MODELS` and `CONTEXT_LIMITS`, all keyed by provider string. Three module-level
asserts at `:48-56` keep their key sets and defaults consistent. `chat_model_catalog()` at
`:69-78` composes them into `ChatModelCatalogEntry` (`:59-66`), whose fields are
`provider`, `models`, `default`, `context_limit`.

The lists as shipped:

| Provider | Catalogued | Default | Context limit |
|---|---|---|---|
| claude | `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5` | `claude-sonnet-4-6` | 200 000 |
| openai | `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini` | `gpt-5.4` | 128 000 |
| gemini | `gemini-3.5-flash`, `gemini-2.5-pro`, `gemini-2.5-flash` | `gemini-3.5-flash` | 1 000 000 |

### 4.2 The context limit is wrong for most Claude models

`turn_engine.py:283` copies `CONTEXT_LIMITS` into `_CONTEXT_LIMITS`, and `_context_limit_for`
(`:286-293`) resolves it as `_CONTEXT_LIMITS.get(agent.model_hint.value, 128_000)`. It never reads
`agent.model_id`. Every Claude agent is therefore capped at 200 000 tokens.

That figure is correct only for `claude-haiku-4-5`. The current Claude generations carry
1 000 000: verified against the bundled `claude-api` skill's model table (cached 2026-06-24),
which lists 1M for Fable 5, Opus 5, Opus 4.8, Opus 4.7, Opus 4.6, Sonnet 5 and Sonnet 4.6, and
200K for Haiku 4.5 alone.

The value is load-bearing in six places, so the error is not cosmetic:

- `_request_ceiling` (`turn_engine.py:296-312`) clamps the agent's `context_token_cap` to it, and
  derives the default cap from it via `context.py:101-105` (75 %).
- The headless fixed-context check (`turn_engine.py:1302-1317`) abandons a turn whose fixed
  context exceeds it, reason `context_overflow`.
- The headless request check (`:1375-1390`) does the same for the assembled request.
- `:2546` and `:3865` resolve it for the room path, feeding `_assemble_history` (`:3868`) and the
  knowledge-grant budget (`:3709-3801`, parameter `provider_context_limit`).
- `:2862-2881` re-checks the assembled payload.

A five-fold under-grant therefore surfaces as `knowledge_starved` and `context_overflow` on turns
that had room, which is exactly the copy added for those kinds in
`frontend/src/slices/conversation/constants/agentErrors.ts:14`.

### 4.3 Capability rules live as regexes inside three adapters

Each adapter shapes its request from a prefix guess at the model id:

- `adapters/openai.py:34` `_REASONING_MODEL_RE = ^(?:o\d|gpt-5)` gates `max_completion_tokens`
  versus `max_tokens`, suppression of `temperature`/`top_p`/`seed`, and `reasoning_effort`
  (`_chat_body`, `:147-177` before `e16bc90`).
- `adapters/openai.py:39` `_VISION_MODEL_RE` gates whether an image is sent or replaced by a
  filename note, with the comment at `:36-38` recording that a non-vision model 400s and that a
  400 aborts the key group.
- `adapters/openai.py` `_NO_EFFORT_WITH_TOOLS_RE` (added by `e16bc90`) suppresses
  `reasoning_effort` when tools are present on gpt-5.4 and later.
- `adapters/anthropic.py:35` `_NO_SAMPLING_RE` suppresses `temperature`/`top_p` on the families
  that reject them.
- `adapters/anthropic.py` `_SUPPORTS_EFFORT_RE` (added by `e16bc90`) gates `output_config.effort`.

`adapters/gemini.py:116-119` has no guard at all: `thinkingConfig.thinkingLevel` is sent whenever
`effort` is set, with the comment "An unsupported model rejects it as a normal error". It is not a
normal error. `router_policy.py:43-44,55-56` classifies 400/404/422 as `ABORT`, and
`provider_router.py` raises `KeyGroupExhausted(reason="request_rejected")` rather than rotating,
so the whole key group is abandoned and the failure repeats on every turn.

These regexes have been wrong in production once already. `^gpt-5` matched `gpt-5.4`, which is a
reasoning model, so `reasoning_effort` was sent, and OpenAI refuses that parameter alongside
function tools from gpt-5.4 onwards. Every agent turn sends tools.

### 4.3a Dropping the parameter may not be sufficient, and is not uniform within a turn

Two consequences of `e16bc90` were found by a code review of that commit and are unverified
against the live endpoint. Both bear on this task's design.

**Omitting is not the remedy the provider named.** The error text offers two remedies: use
`/v1/responses`, or set `reasoning_effort` to `'none'`. It does not say to omit the field. If a
gpt-5.4+ model applies a non-none server-side default (OpenAI's own documentation states gpt-5.5
"defaults to medium reasoning effort"), a toolful request that simply leaves the field out may
still be "function tools with reasoning_effort" and 400 identically. `AgentEffort` has no `none`
member today, so sending it explicitly is not currently expressible; Q-3's widening makes it so.
The adapter tier is driven by `respx` fakes, so the suite is green either way. **This must be
verified against the real endpoint before the capability table records `effort_conflicts_with_tools`
as "omit" rather than "send none".** Circumstantial evidence points at omission being sufficient:
the three example-pack agents run `gpt-5.4` with `effort` unset and completed turns on
2026-08-24 (`agent.turn_finished`, trigger `every_n_messages`), and `結書` began completing turns
the moment its `effort` was nulled. Whether those requests carried a non-empty `tools` array was
not established: `ToolRegistry.specs()` (`tool_registry.py:271-272`) returns whatever is
registered, and `_chat_request` (`turn_engine.py:207-208`) omits the key when that list is empty.

**A single turn now runs at two different effort levels.** `_chat_request`
(`turn_engine.py:183-219`) builds both the tool rounds and the final no-tools synthesis call. Its
own docstring at `:196-199` warns of exactly this: "which is how a control comes to be applied to
one of the two calls and not the other". After `e16bc90` the tool rounds drop `effort` (tools
present) while the synthesis call keeps it (tools absent), so an agent's reasoning happens at the
provider default and only its final composition at the configured level. This is one more reason
the control has to be disabled rather than silently degraded: "partly applied" is not a state any
UI copy can honestly describe.

### 4.4 The agent-config form offers every control for every model

`AgentDetailView.vue:294-298` builds `effortOptions` as a constant list, rendered at `:827-836`
with no reference to the selected model. `:247` resolves the catalogue entry for the *provider*
only (`chat.find((c) => c.provider === modelHint.value)`), and `:261-262` uses it solely to decide
whether the typed `model_id` is a catalogue member or a custom value. `:476` reads
`currentChatEntry.value?.context_limit ?? 128_000` to bound the context-token-cap input, so that
bound inherits §4.2's error.

Sampling controls have the same shape: `temperature`, `top_p` and `seed` are offered for every
model, and `adapters/anthropic.py:162-169` silently drops the first two on models that reject
them, so a value the user set has no effect and nothing says so.

### 4.5 API and frontend blast radius

`app/api/v1/model_catalog.py:28-32` declares `ChatModelProviderOut(provider, models, default,
context_limit)`, composed into `ModelCatalogOut` at `:46-48` and served from
`GET /api/model-catalog` (`:25`, `:51-52`) to any authenticated principal. The generated client
mirrors it at `frontend/src/shared/api-client/models/ChatModelProviderOut.ts:6`. Consumers:

- `AgentDetailView.vue:122,245-262,476` (chat half).
- `RagConfigListView.vue:72,163-179` (embedding half only, untouched by this task).
- `app/workers/tasks/prompt_assistant.py:102` calls `AgentsFacade(db).chat_model_catalog()`
  server-side.

Changing the shape requires `pnpm run gen:api` and `pnpm run check:openapi-drift`.

### 4.6 `model_id` and `effort` are absent from the SRS

`REQUIREMENTS.md:422-436` lists the agent's fields. Neither `model_id` nor `effort` appears,
though both are columns (`contexts/agents/infrastructure/tables.py:36`, `:37-41`) and `model_id`
is what `_resolve_provider_and_model` (`turn_engine.py:392-395`) sends to the provider. `[R9.09]`
and `[R9.10]` (`REQUIREMENTS.md:448-453`) both say "provider's context limit", which is the
framing this task replaces. `[R9.10a]` (`:454-458`) fixes the platform-wide upper bound at
1 000 000 and attributes it to Gemini; the number survives this change, the attribution does not.

## 5. Design

### Options considered

**Option A: keep the per-provider dictionaries, add a per-model override map.** Smallest diff.
Leaves two sources of truth for the same question and leaves the adapter regexes in place, so the
next model family that changes a parameter contract produces the same class of incident. Rejected.

**Option B: a per-model capability record, provider entries derived from it.** One
`ChatModelSpec` per model id, holding the context window and per-parameter acceptance. The adapters
consult it instead of matching prefixes; the catalogue endpoint serves it; the form reads it. A
model id absent from the table resolves to a conservative floor (Q-2).

**Option C: discover capabilities at runtime from each provider's `models` endpoint.** Always
current. Requires a provider key to answer a question asked while rendering a form, adds a network
dependency to the config UI, and each provider reports a different and partial capability shape.
Rejected as a runtime mechanism; adopted as the reconciliation tool in Q-4.

### Decision

Option B, with Option C demoted to an operator command.

What is consciously given up: a new model is under-served until a person adds its row. Q-2 makes
that the deliberate direction of the failure. The alternative direction, guessing generously from
the model id, is the mechanism that produced the incident this task came from, and its failure mode
is not degraded service but an agent that cannot answer at all while telling the user only that
"the agent run failed".

The table is a data file rather than a Python literal (Q-4) so that a capability row carries its
own provenance: source URL and verification date per entry. A comment claiming the whole block was
verified once, which is what `domain/models.py:24-27` does today, cannot say which of its rows a
later reader should distrust.

The capability record is domain data, so it lives in `contexts/agents/domain/`. The adapters are in
`contexts/keys/infrastructure/`, and `shared_kernel` may not import from a context, so the adapters
cannot reach the agents domain directly. The capability facts the adapters need therefore travel on
the `ProviderRequest` payload, which the turn engine already populates
(`turn_engine.py:1401` passes `provider=`, and the payload already carries `effort`, `temperature`,
`top_p`, `seed`). This preserves the existing direction of dependency: the agents context knows
about models, the keys context shapes whatever request it is handed.

## 6. Detailed Changes

**Backend**

- New `contexts/agents/domain/model_specs.py` (or a data file plus a thin loader; the loader lives
  in `domain/`, the data beside it) defining `ChatModelSpec`: `model_id`, `provider`,
  `context_limit`, `accepts_effort`, `effort_values`, `accepts_sampling`, `accepts_vision`,
  `uses_completion_token_field`, `effort_conflicts_with_tools`, `source_url`, `verified_on`.
- `domain/models.py`: `CHAT_MODEL_CATALOG`, `DEFAULT_CHAT_MODELS` and `CONTEXT_LIMITS` derive from
  the specs. The three asserts at `:48-56` become assertions over the derived views, plus a new one
  that every provider has at least one spec and a declared default.
- `AgentEffort` widened per Q-3. The union across providers is `none`, `minimal`, `low`, `medium`,
  `high`, `xhigh`, `max`; which subset a model accepts is a spec field, not an enum concern.
- `turn_engine._context_limit_for` resolves by `(model_hint, model_id)` with the conservative floor
  for an unknown id.
- The turn engine puts the resolved spec's shaping facts onto the provider request payload.
- The five adapter regexes are deleted; `openai.py`, `anthropic.py` and `gemini.py` read the
  payload's capability fields. `gemini.py:116-119` gains the guard it never had.
- New `smap` CLI command reconciling the table against provider `models` endpoints (Q-4), reading
  keys the way the existing rotation and maintenance commands do.
- Migration: yes, one, widening the `agent_effort` PostgreSQL enum. Reversibility in §10.

**API contract**

`ChatModelProviderOut.models` becomes a list of objects rather than strings, each carrying the
per-model capabilities the form needs; `context_limit` moves onto the model. The provider entry
keeps `provider` and `default`. `gen:api` rerun: yes, plus `check:openapi-drift`.

**Frontend**

`agents` slice. `AgentDetailView.vue`: `currentChatEntry` gains a per-model resolution;
`effortOptions` derives from the selected model's `effort_values`; the effort field, and the
sampling fields, are disabled with an explanatory help string when the model does not accept them;
`:476`'s bound reads the model's context limit. New i18n keys in both locales for the disabled
explanations. `shared/composables/useModelCatalog.ts` and its test fixture follow the shape change.

**Deploy/config**

None.

## 7. NFR Checklist

- [ ] i18n: the disabled-control explanations are new user-facing strings and go through `$t()` in
      `en.json` and `zh-TW.json`. The vue-i18n literal `@` rule applies (see
      `reference_i18n_literal_at`): no `@` in the new copy, or escape it.
- [ ] Audit log: no new domain event. The turn's existing `agent.turn_failed` row already carries
      `provider_detail` as of `1d9a3da`, which is what names a refused parameter.
- [ ] Tenant isolation: N/A. No new endpoint; `/api/model-catalog` is global non-tenant data and
      its AuthZ is unchanged (`model_catalog.py:9-10`).
- [ ] Error handling UX: the disabled state is the loading-sensitive one. `AgentDetailView.vue:261`
      already treats `modelCatalogQuery.isError` as a case; a control must not be disabled merely
      because the catalogue has not answered yet, or a user on a slow link cannot edit an agent.
- [ ] Performance: the table is a few dozen static rows resolved by dict lookup. The reconciler is
      operator-invoked and never on a request path.

## 8. Security Considerations

The task touches provider key handling indirectly: the capability table decides what is sent to a
provider on the user's own key.

- The reconciler command reads provider keys and must follow the existing envelope-decrypt path
  rather than accepting a key on the command line. It must never log a key, and its output is model
  ids only.
- Capability data is platform-authored, not user-authored, so no user input reaches request
  shaping through this table. `model_id` itself remains user-supplied and continues to be sent as
  an opaque string; nothing in this task interpolates it into a URL or a query.
- The conservative floor for unknown models is the security-relevant default: it means a user
  cannot induce the platform to send a parameter combination it has no record of by naming a model
  id that pattern-matches a capable family.

## 9. Quality Notes

**Existing debt in touched files**

- `turn_engine.py` resolves the context limit in four separate places (`:1285`, `:2546`, `:3865`,
  and inside `_request_ceiling`) rather than once per turn. Do not add a fifth; do not silently
  refactor the four either (FU-1).
- The three adapters each carry a near-identical "an unsupported model rejects it as a normal
  error" comment (`gemini.py:116-117`, and the pre-`e16bc90` `anthropic.py:170-171`). That
  reasoning is the defect; the replacement must not restate it anywhere.
- `domain/models.py:48-56` uses module-level `assert` for invariant checking. `assert` is stripped
  under `python -O`. Preserve the existing style for consistency, and record the concern as FU-2
  rather than changing it here.

**Patterns to follow**

- Derived views over one source: `contexts/orchestration/domain/models.py:210-216`
  (`EXPLICIT_TRIGGERS`) is the in-repo exemplar of a single constant that two call sites share
  specifically so they cannot diverge, with the reason written down.
- Degrade rather than fail on a refused parameter: `adapters/openai.py:152-165` (the
  `max_tokens`/`temperature`/`top_p`/`seed` branches) is the shape every new capability guard
  should match.
- UI honesty about a setting that does not apply: `shared/ui/SWakeupEditor.vue` gained exactly this
  treatment in `e321fd4`/`6627410`, including the lesson that the help text must name the precise
  scope of what the control governs.

**Reuse inventory**

- `AgentsFacade.chat_model_catalog()` (`interfaces/facade.py:96-98`) is the existing seam; extend
  it rather than adding a second catalogue accessor.
- `contexts/agents/application/context.py:101-105` (`default_cap_from_limit`) already derives the
  75 % default; feed it the per-model limit rather than recomputing.
- `useModelCatalog` (`shared/composables/`) is the frontend's single read of this endpoint.
- `SFormField`'s existing `help` and disabled handling; no new form primitive is needed.
- `smap/` already hosts CLI commands (bootstrap, maintenance, rotation) with an established
  key-reading pattern for the reconciler to follow.

## 10. Risks and Rollback

- **The migration widens a PostgreSQL enum.** `ALTER TYPE ... ADD VALUE` cannot be reversed in
  PostgreSQL without recreating the type. The downgrade must recreate `agent_effort` with the
  original three values, which fails if any row holds a new value. The downgrade therefore has to
  null those rows first, and must say so; a silently failing downgrade is worse than a documented
  lossy one.
- **Raising the Claude context limit changes turn behaviour immediately.** Agents currently capped
  at 200 000 will start assembling larger requests and granting more knowledge on the user's own
  provider key. This is the intended correction, but it is a cost change on real keys and belongs
  in the release note, not only in the diff.
- **The API shape change is breaking for any client on the old shape.** In-repo there is one, and
  `check:openapi-drift` catches a partial update. The risk is a deployment where the frontend build
  and the backend image move independently.
- **A wrong capability row is worse than a missing one.** A row asserting a model accepts a
  parameter it refuses reproduces the original incident with more confidence behind it. The
  reconciler (Q-4) is the mitigation, and AC-11 requires it to have been run.

Rollback: revert the commit and run `alembic downgrade -1`, accepting the enum caveat above.

## 11. Acceptance Criteria

- [ ] AC-1: `CHAT_MODEL_CATALOG`, `DEFAULT_CHAT_MODELS` and `CONTEXT_LIMITS` no longer exist as
      independently authored constants; each is derived from the per-model specs, and a test
      asserts every provider's default is a catalogued model of that provider.
- [ ] AC-2: `_context_limit_for` returns 1 000 000 for an agent on `claude-sonnet-4-6` and 200 000
      for one on `claude-haiku-4-5`, both verified by unit test.
- [ ] AC-3: an agent whose `model_id` is not in the table resolves to the conservative floor: no
      `effort`, no sampling, and the provider's lowest catalogued context limit. Unit test asserts
      the request body built for such a model contains none of `reasoning_effort`,
      `output_config`, `thinkingConfig`, `temperature`, `top_p`, `seed`.
- [ ] AC-4: `_REASONING_MODEL_RE`, `_VISION_MODEL_RE`, `_NO_EFFORT_WITH_TOOLS_RE`,
      `_NO_SAMPLING_RE` and `_SUPPORTS_EFFORT_RE` are gone from the three adapters, and a test
      asserts each shaping decision they made is still made, driven by the table.
- [ ] AC-5: `adapters/gemini.py` no longer sends `thinkingConfig` for a model whose spec does not
      accept effort. Unit test.
- [ ] AC-6: the model lists for all three providers match §4.1's replacement table, each row
      carrying a source URL and a verification date.
- [ ] AC-7: `GET /api/model-catalog` returns per-model capability objects; `pnpm run gen:api` has
      been rerun and `pnpm run check:openapi-drift` passes.
- [ ] AC-8: selecting a model that refuses `reasoning_effort` in the agent form disables the effort
      control and shows an explanation naming the model. Component test.
- [ ] AC-9: the same for `temperature` and `top_p` on a Claude model whose spec sets
      `accepts_sampling: false`. Component test.
- [ ] AC-10: the context-token-cap bound in the form follows the selected model, not the provider.
      Component test asserts the bound differs between `claude-sonnet-4-6` and `claude-haiku-4-5`.
- [ ] AC-11: the reconciler command has been run against all three providers and its output is
      recorded in this dossier; every discrepancy is either fixed in the table or recorded as an
      FU with the reason it was left.
- [ ] AC-12: `AgentEffort` accepts the widened value set, the migration applies and reverses per
      §10, and a `pytest.mark.db` test asserts the enum's values (the unit tier renders enums as
      inline literals and cannot see a PostgreSQL enum mismatch, per `backend/CLAUDE.md`).
- [ ] AC-13: an agent configured exactly as `結書` was (`model_hint: openai`, `model_id: gpt-5.4`,
      `effort: low`, tools bound) produces a request body with no `reasoning_effort` and completes
      a turn against the fake provider.
- [ ] AC-15: §4.3a's first question is answered against the live endpoint, and the table records
      the verified remedy. A toolful `gpt-5.4` request with `reasoning_effort` omitted either
      succeeds (omission is sufficient) or 400s (the spec must send `"none"`, which Q-3's widened
      enum makes expressible). The answer, and how it was obtained, is recorded in this dossier.
      No mock can close this criterion.
- [ ] AC-16: an agent whose model refuses effort produces the *same* request shape on its tool
      rounds and on its final synthesis call, so §4.3a's split-effort turn cannot occur. Unit test
      driving `_chat_request` both with and without `tools`.
- [ ] AC-14: full Definition of Done green: `pytest -q`, `ruff check`, `ruff format --check`,
      `mypy`, `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-2, AC-3 | unit | `backend/tests/unit/test_agents_domain_models.py` (new or existing), `test_turn_engine_*` |
| AC-4, AC-5, AC-13 | unit | `backend/tests/unit/test_provider_adapters.py`, extending the request-body assertions added in `e16bc90` |
| AC-6, AC-11 | manual, recorded in this dossier | reconciler output pasted into §14 or a new §17 |
| AC-7 | gate | `pnpm run check:openapi-drift` |
| AC-8, AC-9, AC-10 | component | `frontend/src/slices/agents/__tests__/AgentDetailView.test.ts` |
| AC-12 | db | `backend/tests/` under `pytest.mark.db` |
| AC-14 | gate | project commands per `CLAUDE.md` |

AC-13 uses `fake_provider.py`, which per `2026-08-24-agent-readable-live-drafts`'s §17 cannot
produce a real agent turn; the criterion is therefore about the request body reaching the adapter,
not about a model's reply.

## 13. SRS Delta

To apply to `REQUIREMENTS.md` on approval.

Add two rows to the agent field table at `REQUIREMENTS.md:422-436`, after `model_hint`:

```
| `model_id` | string nullable | The provider model this agent runs on. `NULL` means the platform default for `model_hint`. Not restricted to the catalog: a Project Owner may name any model id the provider accepts. |
| `effort` | enum nullable | Cross-provider reasoning-effort level, mapped per provider at the adapter boundary. `NULL` means the provider's own default. A value the selected model does not accept is not sent. |
```

Amend `[R9.09]` (`:448`), replacing "provider's hard context limit" with "the selected model's hard
context limit":

```
- **[R9.09]** `context_mode = general`: unbounded growth. The system sends the entire chat history (subject to the selected model's hard context limit, at which point the provider will error; this is surfaced to the UI).
```

Amend `[R9.10]` (`:449`), replacing "provider's context limit" with "the selected model's context
limit".

Amend `[R9.10a]` (`:454-458`), removing the attribution of the 1 000 000 bound to Gemini alone:

```
- **[R9.10a]** An Agent's `context_token_cap` override is bounded above by the widest model
  context window the platform supports (currently 1 000 000; see the model catalog). A
  value above it is rejected at the API with a 422 and by a DB constraint: it cannot be honoured by
  any provider, and in compact mode it would suppress compaction entirely and guarantee a rejected
  request. `NULL` continues to mean the provider-derived default.
```

Add a new requirement in §9.1, after `[R9.03]`:

```
- **[R9.03a]** **Model capability table.** The platform holds one capability record per catalogued
  provider model: its context window and, per request parameter (reasoning effort and its accepted
  values, sampling controls, vision input), whether that model accepts it. The record is the single
  source for the agent-config UI, which does not offer a control the selected model refuses, and
  for request shaping at the adapter boundary, which does not send one. A model id outside the
  table resolves to a conservative floor: no optional parameter is sent, and the provider's lowest
  catalogued context window applies. Each record carries the source and date of its verification.
```

## 14. Open Questions

- The Gemini lineup could not be fully verified: the release notes list `gemini-3.7-flash`,
  `gemini-3.6-flash`, `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite`, and do not confirm
  whether the currently-shipped default `gemini-3.5-flash` (as distinct from `-lite`) still exists,
  nor whether `gemini-2.5-pro`/`gemini-2.5-flash` are still served after the 2.0 shutdown on
  2026-06-01. AC-11's reconciler run settles this; the table must not be authored from the release
  notes alone.
- Gemini's `thinkingConfig.thinkingLevel` value set was not verified against current docs, only its
  existence in the adapter (`gemini.py:119`). The widened `AgentEffort` union is drawn from
  OpenAI's documented set and Anthropic's `output_config.effort`; Gemini's accepted values are an
  AC-11 output.
- Whether `claude-fable-5` belongs in the catalogue at all is a product decision, not a technical
  one: it carries a 30-day data-retention requirement that a self-hosted BYO-key deployment may not
  meet, and a request from an org that does not meet it returns 400. Left out of the proposed table
  pending that decision.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

- FU-1: `turn_engine.py` resolves the context limit at four call sites per turn. Consolidating is
  out of scope here; doing it inside this change would mix a structural refactor into a correctness
  fix.
- FU-2: `domain/models.py:48-56` enforces its invariants with module-level `assert`, which is
  removed under `python -O`. Whether the deployment runs optimised was not established.
- FU-3: existing agent rows may hold an `effort` value their model refuses. After this task the
  value is inert rather than harmful, and the form will show it as disabled. Whether to null those
  rows in a data migration is deferred.
- FU-4: the same "offered but silently ignored" problem applies to `seed`, which
  `adapters/gemini.py:115` documents as a no-op on Gemini and `adapters/anthropic.py:34` as never
  forwarded on any Claude model. The capability table makes this expressible; wiring the UI for it
  is not in AC-9's scope.
