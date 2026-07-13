---
type: feature
status: approved
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

- [ ] AC-1: An agent can be created/patched with `temperature`/`top_p`/`seed`; each is
  persisted and returned; clearing a field restores provider-default behaviour.
- [ ] AC-2: When `temperature` is set, the value appears in the provider payload at both build
  sites and is forwarded by the Gemini and Anthropic adapters; for an OpenAI reasoning model it
  is correctly omitted (no 400).
- [ ] AC-3: When `top_p` is set, all three adapters forward it (under each provider's constraint).
- [ ] AC-4: When `seed` is set, the OpenAI adapter forwards it; the Anthropic and Gemini adapters
  ignore it without error.
- [ ] AC-5: Two runs of the same input with `temperature=0` (+ `seed` on OpenAI) produce
  materially lower output variance than the provider default — measured by an integration check
  asserting the sampling params reach the outbound body (determinism-of-transport, not
  bit-identity of model output).
- [ ] AC-6: Out-of-range values (`temperature>2`, `top_p>1`) are rejected 422 at the API and
  flagged in the form.
- [ ] AC-7: Existing agents (all three fields null) call providers exactly as before (no
  temperature/top_p/seed in the body).

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

Appended by /build.

## 16. Follow-ups

None. (Per-turn sampling override, if ever wanted, is a separate small change — not needed for
the AA judged-scoring use case, which is a stable configured agent.)
