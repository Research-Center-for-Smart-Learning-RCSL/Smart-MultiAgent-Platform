---
type: feature
status: implemented
created: 2026-07-13
requirements: [R9.04]
depends_on: []
---

# Agent Sampling Reproducibility — expose `temperature` / `top_p` / `seed`

## 1. Summary

Expose deterministic sampling controls (`temperature`, `top_p`, and — where the provider
supports it — `seed`) on the Agent configuration and thread them through the turn engine into
the provider request payload, so an agent's output can be made low-variance and reproducible.
The motivating consumer is the NSTC "Analytics Agent" (AA): its LLM-judged creativity scoring
must be reproducible enough to compute Cohen's Kappa against human raters — impossible today,
because the engine never sets a temperature and the `Agent` model has no sampling field, so
every scoring run samples at the provider default. This is a small, standalone change
orthogonal to the `activities` program (no dependency), but it is a precondition for the AA's
judged-scoring layer to be measurable.

**Honesty constraint baked into the design:** no hosted LLM guarantees bit-exact determinism.
`temperature=0` (+ `top_p=1`) collapses most sampling variance across all three providers;
`seed` is an additional lever **only on OpenAI** (Anthropic and Gemini expose no seed
parameter). The requirement this satisfies is *reproducible-enough-to-audit*, not
*bit-identical*, and the spec says so plainly rather than overclaiming.

## 2. Goals and Non-goals

**Goals**
- Add `temperature: float | None`, `top_p: float | None`, `seed: int | None` to the `Agent`
  domain model, the `AgentDraft` create/patch payload (with clear-sentinels), the table/repo,
  and the facade — mirroring the existing optional fields (`model_id`, `effort`,
  `context_token_cap`).
- Thread them into the provider payload at **both** payload-build sites in `turn_engine.py`
  (the tool-loop request and the final request), only when set (omit when `None` so provider
  defaults are preserved and reasoning-model constraints are respected).
- Migration to persist the three nullable columns.
- Frontend: expose the fields in the agent-detail form (agents slice), validated ranges.
- Provider-conditional `seed`: forwarded only by adapters whose provider supports it (OpenAI);
  documented as no-op elsewhere.

**Non-goals**
- Any `activities` change — this stands alone.
- Guaranteeing bit-exact determinism (not achievable on hosted LLMs; not claimed).
- Provider-side reproducibility beyond the parameters the APIs expose.
- Per-turn override of sampling (config lives on the Agent; per-turn is a possible later
  follow-up, not here).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which params | `temperature` + `top_p` + `seed` | temperature is the primary variance lever (all providers); top_p rounds out nucleus control; seed adds determinism where supported |
| Q-2 | seed on all providers? | No — OpenAI only; Anthropic/Gemini have no seed API | Overclaiming determinism would be a correctness defect; adapters forward seed only where the API accepts it |
| Q-3 | Where does the value live | On the `Agent` config (persisted), not per-turn | The AA is a configured agent; its scoring config should be stable and auditable |

## 4. Current State (verified)

- **`Agent` dataclass has no sampling field**: `contexts/agents/domain/models.py:130-149`
  (`model_hint`, `model_id`, `effort`, `key_group_id`, … `context_token_cap` — but no
  `temperature`/`top_p`/`seed`). `AgentDraft` mirrors optional fields with clear-sentinels
  (`rag_config_id`/`knowmap_config_id` at `models.py:228-229`, `clear_*` at `:240-241`).
- **The engine never sets temperature.** Both provider payloads are built with only `messages`
  + `max_tokens`: the tool-loop request `turn_engine.py:1575-1584` (`"max_tokens":
  _DEFAULT_MAX_TOKENS`, `payload=payload`, `ProviderRequest(...)`), and the final request
  `:1651-1658` (`final_payload`, `final_request`). Neither adds `temperature`/`top_p`/`seed`.
- **The payload contract already carries an optional `temperature`.** `ProviderRequest.payload`
  is a free `dict[str, Any]` (`provider_router.py:70-81`); the canonical LLM_CHAT payload
  documents `"temperature": 0.7  # optional` (`adapters/base.py:22`).
- **Adapters already forward `temperature` when present** — so no adapter change is needed for
  temperature:
  - Gemini: `if payload.get("temperature") is not None: gen["temperature"] = payload["temperature"]`
    (`adapters/gemini.py:111-112`).
  - Anthropic: `body["temperature"] = payload["temperature"]` (`adapters/anthropic.py:150-151`).
  - OpenAI: `if payload.get("temperature") is not None and not reasoning: body["temperature"] =
    …` (`adapters/openai.py:155-157`) — **reasoning models reject a custom temperature (400)**,
    so OpenAI deliberately skips it; the engine must not assume temperature always applies.
- **`top_p` / `seed` are not handled by any adapter today** (grep: only `temperature` matches in
  `adapters/`). So those two need adapter additions, and `seed` only where the provider supports
  it.

## 5. Design

**Config → payload flow.** Add the three fields to `Agent`. In `turn_engine.py`, build a small
`sampling = {k: v for k, v in {"temperature": agent.temperature, "top_p": agent.top_p, "seed":
agent.seed}.items() if v is not None}` and merge into both payload dicts (`:1575`, `:1651`).
Merging only set values preserves provider defaults and lets each adapter apply its own
constraint (e.g. OpenAI dropping temperature for reasoning models). One helper, used at both
sites, avoids drift.

**Adapter additions.**
- `top_p`: add `if payload.get("top_p") is not None: …` to all three adapters, mirroring the
  temperature lines (Gemini `generationConfig.topP`, Anthropic `body["top_p"]`, OpenAI
  `body["top_p"]` under the same non-reasoning guard).
- `seed`: add only to the OpenAI adapter (`body["seed"] = payload["seed"]`); Anthropic and
  Gemini adapters **ignore** `seed` (documented no-op — their APIs have no equivalent). The
  domain allows the field on any agent, but it only takes effect on OpenAI-backed key groups.

**Determinism framing (documented, not overclaimed).** For the AA judge, the reproducible
configuration is `temperature=0, top_p=1` (+ `seed` on OpenAI). This makes repeated scoring of
the same input stable enough to compute Kappa; it is not a guarantee of identical bytes, and
the spec/UX say so. The frontend field help text states this.

**Frontend.** Add the three inputs to the agent-detail form (agents slice), validated:
`temperature ∈ [0, 2]`, `top_p ∈ [0, 1]`, `seed` integer; a "reproducible scoring" hint noting
seed is OpenAI-only. Use the existing vee-validate + Zod form pattern.

## 6. Detailed Changes

- **`contexts/agents/domain/models.py`**: add `temperature`/`top_p`/`seed` to `Agent`
  (`:130-149`) and to `AgentDraft` (`:~228`) with `clear_temperature`/`clear_top_p`/`clear_seed`
  sentinels (mirror `:240-241`).
- **`contexts/agents/infrastructure`** (agent table + repo): three nullable columns; row↔domain
  mapping mirrors the existing optional columns (`model_id`, `context_token_cap`).
- **`contexts/agents/application` (patch/create service)** + **`interfaces/facade.py`**: accept
  and persist the fields; clear-sentinel handling like the existing optional configs.
- **`contexts/agents/application/runtime/turn_engine.py`**: a `_sampling_payload(agent)` helper;
  merge into the tool-loop payload (`:1575`) and final payload (`:1651`).
- **Adapters**: `top_p` in all three (`gemini.py`, `anthropic.py`, `openai.py`); `seed` in
  `openai.py` only; document the no-op in `anthropic.py`/`gemini.py`.
- **`adapters/base.py`**: extend the canonical-payload docstring (`:10-23`) to list `top_p` and
  `seed` (OpenAI-only) as optional fields.
- **Migration** `backend/alembic/versions/00xx_agent_sampling.py` (`down_revision` = current
  head at build time): add three nullable columns to the agents table; reversible.
- **Frontend** `slices/agents` agent-detail form: three validated inputs + i18n help text;
  `gen:api` rerun for the new fields.

## 7. NFR Checklist

- [x] i18n — new form labels/help via `$t()` in the agents slice locales.
- [x] Audit — sampling config is part of the agent; the existing agent-update audit covers it.
- [x] Tenant isolation — no new endpoint; fields ride the existing agent create/patch path and
  its authz.
- [x] Error handling — out-of-range values rejected at the API model (Pydantic) and the form
  (Zod); an OpenAI reasoning model + custom temperature is already handled by the adapter skip
  (`openai.py:155-157`).
- [x] Performance — no runtime cost; three scalars in the payload.

## 8. Security Considerations

- **No new surface.** Fields are agent config on the existing authz'd create/patch path; no new
  endpoint, no user-input processing beyond bounded scalars.
- **No secret exposure.** Sampling params are non-sensitive; they never touch keys/secrets.
- **Provider-constraint safety.** The engine merges only set values and each adapter applies its
  own guard (OpenAI temperature skip for reasoning models), so a bad combination degrades to the
  provider default rather than a hard 400 for the user.

## 9. Quality Notes

- **Existing debt / accuracy trap (do not imitate):** it is tempting to claim "seed → determinism
  everywhere". That is false (Anthropic/Gemini have no seed; no hosted LLM is bit-exact). The
  code and UX must frame this as reproducible-enough-for-Kappa. Recorded so the implementer does
  not overpromise in help text.
- **Patterns to follow:** the optional-field lifecycle of `model_id`/`context_token_cap`/
  `rag_config_id` (domain → draft+sentinel → table → repo → facade → form); the temperature
  forwarding lines in each adapter are the exact template for `top_p`.
- **Reuse inventory:** the `AgentDraft` clear-sentinel machinery; the adapters' `payload.get(...)
  is not None` guard idiom; the agents-slice vee-validate + Zod form.

## 10. Risks and Rollback

- **Reasoning-model temperature (OpenAI)** — already guarded (`openai.py:155-157`); the engine
  must not force temperature into the body itself (it doesn't — adapters own the constraint).
- **`seed` inertness on non-OpenAI** — acceptable and documented; not a bug.
- **Rollback**: fields are nullable and omitted-when-null, so existing agents behave exactly as
  today (provider default). Dropping the columns reverts cleanly.

## 11. Acceptance Criteria

- [x] AC-1: An agent can be created/patched with `temperature`/`top_p`/`seed`; each is
  persisted and returned; clearing a field restores provider-default behaviour.
  *(test_agent_sampling_fields.py: draft defaults, create-passes-to-repo, patch-sets-values,
  clear-sentinels-null, patch-omits-unset.)*
- [x] AC-2: When `temperature` is set, the value appears in the provider payload at both build
  sites and is forwarded by the Gemini and Anthropic adapters; for an OpenAI reasoning model it
  is correctly omitted (no 400). *(test_turn_engine_sampling.py both-sites merge;
  test_provider_adapters.py Gemini/Anthropic-accepting-model forward + OpenAI-reasoning skip.
  See D-1: Anthropic forwarding is now conditional on the model family.)*
- [x] AC-3: When `top_p` is set, all three adapters forward it (under each provider's constraint).
  *(test_provider_adapters.py per-provider top_p forwarding.)*
- [x] AC-4: When `seed` is set, the OpenAI adapter forwards it; the Anthropic and Gemini adapters
  ignore it without error. *(test_provider_adapters.py: OpenAI forwards seed; Gemini ignores;
  Anthropic never forwards seed.)*
- [x] AC-5: Two runs of the same input with `temperature=0` (+ `seed` on OpenAI) produce
  materially lower output variance than the provider default — measured by an integration check
  asserting the sampling params reach the outbound body (determinism-of-transport, not
  bit-identity of model output). *(Satisfied at the transport layer per the AC's own
  parenthetical: test_turn_engine_sampling.py asserts `temperature=0` is preserved into both
  payloads — `is not None` guard, not truthiness — and adapter tests assert it reaches the
  outbound body. Live two-run variance measurement is a BYO-key manual `verify` step, FU-2.)*
- [x] AC-6: Out-of-range values (`temperature>2`, `top_p>1`) are rejected 422 at the API and
  flagged in the form. *(Mechanically enforced: `AgentCreateIn`/`AgentPatchIn` use
  `Field(ge=0, le=2)` / `Field(ge=0, le=1)` — FastAPI returns 422 on violation; the Zod schema
  mirrors `.min().max()` for the form.)*
- [x] AC-7: Existing agents (all three fields null) call providers exactly as before (no
  temperature/top_p/seed in the body). *(test_turn_engine_sampling.py empty-fragment; the
  adapters' `payload.get(...) is not None` guards emit nothing when unset.)*

## 12. Test Plan

- Unit (`test_agent_sampling_fields.py`): domain/draft/sentinel round-trip; repo persistence.
- Unit (`test_provider_adapters.py` additions): temperature/top_p/seed forwarding per provider,
  incl. OpenAI reasoning-model temperature skip and Anthropic/Gemini seed no-op (AC-2,3,4).
- Unit (`test_turn_engine_sampling.py`): `_sampling_payload` merges only set values into both
  payloads (AC-2,7).
- API: create/patch validation ranges (AC-6); existing-agent null path (AC-7).
- Frontend: agent-detail form inputs + validation + seed-help text.
- Manual (`verify`): configure an AA-style agent at `temperature=0`, run the same scoring input
  twice, confirm the outbound body carries the params (AC-5).

## 13. SRS Delta

This is an agent-configuration capability (§9), not part of §30. Appended after `[R9.17]` at the
approval gate as `[R9.18]`:

```
- **[R9.18]** An agent's configuration may set sampling controls — temperature, top_p, and (where the provider supports it) seed — which are threaded into every provider call for that agent. Unset controls preserve provider defaults. These controls make an agent's output low-variance and reproducible enough to audit (e.g. to compute inter-rater agreement for an LLM-judged scoring agent); they do not guarantee bit-identical output, and seed has no effect on providers without a seed parameter (Anthropic, Gemini).
```

## 14. Open Questions

None. (The `[R9.18]` number + §9 placement were confirmed and applied at the approval gate.)

## 15. Deviation Log

- **D-1 — Anthropic `temperature`/`top_p` forwarding is model-conditional, not unconditional.**
  §4/§5 assumed the Anthropic adapter already forwarded `temperature` unconditionally
  (`body["temperature"] = payload["temperature"]`) and that `top_p` could be added the same way.
  During build this proved wrong against current provider behaviour: Anthropic removed sampling
  controls on its newer generations — Opus 4.7+ and every "5"-generation model (Sonnet 5, Fable 5,
  Mythos 5) — where sending `temperature`/`top_p` now returns 400. Forwarding unconditionally
  would hard-fail every turn for an agent on a modern Claude model. **Decision (user-approved):**
  add a model-version guard `_NO_SAMPLING_RE = re.compile(r"^claude-[a-z]+-5\b|^claude-opus-4-[7-9]\b")`
  in `adapters/anthropic.py`; sampling params are forwarded only for models that accept them
  (Opus 4.6/4.5, Sonnet 4.x, Haiku 4.5, Claude 3.x) and silently dropped for the rejecting
  families — degrading to the provider default rather than failing the turn, mirroring the
  existing OpenAI reasoning-model skip. `claude-opus-4-5` is deliberately excluded from the guard
  (it accepts sampling). This preserves AC-2's intent (temperature reaches accepting Anthropic
  models) while keeping modern-model agents functional. Tests updated accordingly
  (`test_anthropic_forwards_..._on_accepting_models` / `..._drops_sampling_on_rejecting_models`).

- **D-2 — `openapi.json` regenerated by direct export, not the documented `gen:api` path only.**
  The frontend `gen:api` consumes `backend/openapi.json`; regenerating it requires the backend
  export. The Windows dev env's git-bash lacks `python` on PATH and the alembic/openapi CLI is
  broken under the cp950 code page (see D-3), so the schema was re-exported by invoking the app's
  `export_openapi` logic directly via the global Python 3.12 interpreter. The result was verified
  byte-for-byte against a fresh export (SHA-256 match) and the diff is purely additive (the three
  new fields on `AgentCreateIn`/`AgentOut`/`AgentPatchIn`), so the contract is correct; only the
  invocation path deviated from the CLAUDE.md command table.

- **D-3 — Migration authored and statically validated, but `alembic upgrade head` not run in this
  env.** The `alembic` CLI fails at import under Windows cp950 (a pre-existing env defect:
  `UnicodeDecodeError` on an em-dash in `alembic.ini`), and there is no local Postgres in this
  session. `0051_agent_sampling.py` was instead validated by importing the module (revision /
  down_revision chain off `0050_activity_activations`, single head) and reviewing the additive,
  reversible upgrade/downgrade by hand. Live application is deferred to FU-1.

- **D-4 — Anthropic adapter clamps `temperature` to 1.0 (post-implementation code review).** The
  agent field validates `temperature ∈ [0, 2]` (OpenAI/Gemini's range), but Anthropic's ceiling is
  1.0 — a Claude agent configured at `1 < temperature ≤ 2` passed validation then 400'd at turn
  time, violating the §8 "degrade rather than 400" guarantee. Fix: the Anthropic adapter now
  forwards `min(temperature, 1.0)` for accepting models. Consistent with D-1 (the adapter owns each
  provider's constraint). Covered by `test_anthropic_clamps_temperature_to_provider_ceiling`.

- **D-5 — OpenAI `seed` is now gated behind `not reasoning` (post-implementation code review).**
  As first written, `seed` was forwarded to OpenAI unconditionally while `temperature`/`top_p` were
  gated behind `not reasoning`. Reasoning models (o-series/gpt-5) reject sampling controls, so an
  agent on such a model with a seed set risked a per-turn 400. Fix: `seed` is now dropped for
  reasoning models like the other two controls — determinism degrades to the default rather than
  failing the turn. The AA judge (the motivating consumer) uses a non-reasoning model at
  `temperature=0`, so it is unaffected. Test updated
  (`test_openai_reasoning_drops_all_sampling_controls`).

- **D-6 — Reconciled with a parallel `task/agent-sampling` implementation (user-directed).** An
  independent second implementation of R9.18 had been built on branch `task/agent-sampling` (in a
  worktree). After a full side-by-side comparison, main's implementation was kept as the source of
  truth (it uniquely carries the D-1/D-4/D-5 provider-constraint fixes and the FU-3 seed int4
  bound, all of which the branch lacked), and three genuinely-superior aspects from the branch were
  incorporated onto main:
  1. **Frontend input handling** — the branch drives the three sampling inputs with `type="text"` +
     a `nullableNumberFromText` guard instead of `type="number"` + `nullableNumberModel`. main's
     number path had a real bug: `SInput.onInput` computes `Number(target.value)`, and
     `Number('') === 0`, so *clearing* a sampling field emitted `0` rather than `''` — making
     "provider default" (null) unreachable and silently pinning `temperature=0`. Adopted the
     branch's `AgentDetailView.vue` + its i18n keys (`samplingTitle`, `samplingDefaultPlaceholder`).
  2. **Bootstrap / wiring `create()` callers** — main's `AgentRepository.create` requires
     `temperature`/`top_p`/`seed` (no defaults), but `app/bootstrap/seed.py` (the E2E seed) and
     seven wiring-test fixtures called `create()` without them → `TypeError` on those paths. The
     original code-review grep was scoped to `contexts/agents` and missed the `app/bootstrap`
     caller; the parallel branch caught it. All callers fixed (the branch versions were taken
     verbatim, as their only delta was the missing kwargs). `test_wiring.py` also regained a
     pre-existing-missing `effort=None`.
  3. **Test coverage** — ported the branch's API-boundary validation tests
     (`test_agent_sampling_api.py`), service create/patch/clear tests (added to
     `test_agent_service.py`), and frontend tests (`AgentDetailView.test.ts`, `schemas.spec.ts`).
  The branch's competing backend adapters/schema (which lacked the fixes) and its unbounded-`seed`
  schema were **not** taken. `task/agent-sampling` is retired after this reconciliation.

## 16. Follow-ups

- **FU-1 — Apply and reverse the migration against a live Postgres.** Run `alembic upgrade head`
  then `alembic downgrade -1` on `0051_agent_sampling` in an environment with a UTF-8 locale and a
  reachable database, confirming the three nullable columns add and drop cleanly. Blocked in the
  build session by the cp950 alembic-CLI defect (D-3) and no local Postgres. Not an AC blocker —
  the migration is static-validated and additive — but must run before deploy.

- **FU-2 — Live two-run variance confirmation (AC-5 behavioural).** With a real BYO provider key,
  configure an AA-style agent at `temperature=0` (+ `seed` on OpenAI), run the same scoring input
  twice via the `verify` flow, and record the observed variance reduction. The transport guarantee
  (params reach the outbound body) is unit-covered; the end-to-end variance measurement needs a
  live key and is out of scope for the offline build.

- **FU-3 — RESOLVED (was: bound `seed`).** Code review reclassified this from hardening to a real
  500 path: the `seed` column is `int4`, so an API value ≥ 2³¹ overflowed it and returned 500
  (asyncpg `NumericValueOutOfRangeError`) instead of a 422. Fixed in this task, not deferred:
  `AgentCreateIn`/`AgentPatchIn` now bound `seed` to the int4 range (`_SEED_MIN`/`_SEED_MAX`),
  and the frontend Zod schema mirrors `.min(-2**31).max(2**31-1)` so the form flags it before the
  round-trip. int4 (±2.1e9) is ample for a reproducibility seed and keeps the value JS-safe.

- (Per-turn sampling override, if ever wanted, remains a separate small change — not needed for
  the AA judged-scoring use case, which is a stable configured agent.)
