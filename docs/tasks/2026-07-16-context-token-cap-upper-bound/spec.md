---
type: feature
status: approved
created: 2026-07-16
requirements: [R9.10, R11.19]
---

# Bound `context_token_cap` above, the way its neighbour in the same table already is

## 1. Summary

Split from `2026-07-16-agent-skills`' FU-10. `agents.context_token_cap` is unbounded above at every
layer: the API accepts any positive integer (`app/api/v1/agents.py:82` on create, `:116` on patch —
`Field(default=None, gt=0)`, no `le=`), the DB CHECK is `context_token_cap IS NULL OR
context_token_cap > 0` (`alembic/versions/0011_agents.py:96-99`), and the domain model is a bare
`int | None` (`contexts/agents/domain/models.py:143`).

In compact mode that value becomes the compaction ceiling verbatim —
`ceiling = agent.context_token_cap or ctxmod.default_cap_from_limit(context_limit)`
(`contexts/agents/application/runtime/turn_engine.py:1237`). Set it to 5 000 000 on an OpenAI agent
whose real window is 128 000 and compaction never triggers: `[R11.19]`'s knowledge assembly fills to
a ceiling no provider can accept, the turn pays to retrieve and assemble it, and the provider rejects
the request at the end.

**The premise was checked rather than assumed, because it looks like it should be false.** Both
`should_compact` and `run_compact` receive the cap *and* `provider_context_limit` side by side
(`turn_engine.py:1878-1879`, `:1901-1902`, `:1918-1919`), which reads as though one clamps the other.
It does not: `should_compact` (`contexts/agents/application/context.py`) resolves
`cap = context_token_cap if context_token_cap is not None else default_cap_from_limit(provider_context_limit)`
and returns `projected_tokens > cap` — `provider_context_limit` is consulted **only** to derive the
default when the cap is `NULL`. An explicit cap overrides the provider window instead of being
bounded by it, so `projected_tokens > 5_000_000` is never true and compaction is silently disabled.
The `knowledge_budget(ceiling=...)` docstring in the same file confirms the other half: `ceiling` *is*
`context_token_cap` in compact mode, so `[R11.19]`'s assembly targets it directly.

**This is a `feature`, not a bugfix, and the distinction is the point.** No spec text states an upper
bound for `context_token_cap`, so the code is doing exactly what it was told. A fix *introduces* a
user-visible limit and a 422 that do not exist today, which is why FU-10's wording ("unbounded",
which reads as a defect) is misleading and why this needs a spec rather than a patch. §11 writes the
bound down.

The neighbouring field in the same table already solved this. `skill_index_token_cap` is bounded at
the API (`agents.py:83`, `le=MAX_SKILL_INDEX_TOKEN_CAP`), at the DB (`0056_skills.py:62`), and
documented at `models.py:144-146` — whose comment names `context_token_cap` as the unbounded
counterexample, and `0056_skills.py:62`'s comment cites `0011_agents.py:97` by line. This task copies
that shape.

## 2. Goals / Non-goals

**Goals.**
- `context_token_cap` has one stated upper bound, enforced at the API (422) and the DB (CHECK), with
  the constant in the domain — the exact three-layer shape `skill_index_token_cap` uses.
- Existing rows above the bound are handled explicitly by the migration, not left to fail on next
  write.
- The self-referential comment at `models.py:144-146` stops describing a defect and starts
  describing a rule.

**Non-goals.**
- **Per-provider validation.** Q-1 chose a global ceiling. Cross-checking `context_token_cap`
  against `CONTEXT_LIMITS[model_hint]` is explicitly out (see Q-1's cost).
- **Changing compaction behaviour.** `turn_engine.py:1237` is not a defect — it faithfully honours a
  config value. Only the config's range is wrong. This task does not touch the turn path.
- **`general` mode.** The cap does not apply there (`turn_engine.py:1236-1239` uses `context_limit`),
  so nothing changes for those agents.
- **A lower bound or a default.** `gt=0` and `NULL`-means-default both stay.
- FU-11's separate half (tool results counted in no budget) — different entry, different task.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What is the bound? | **A global `MAX_CONTEXT_TOKEN_CAP = 1_000_000`** — `max(CONTEXT_LIMITS.values())`. | 1 000 000 is Gemini's window (`contexts/agents/domain/models.py:42-46`: claude 200 000, openai 128 000, gemini 1 000 000). Above it the value cannot help *any* provider, so it is the highest number that is ever meaningful and the lowest that rejects nothing legitimate. It mirrors `MAX_SKILL_INDEX_TOKEN_CAP` exactly (constant + `le=` + CHECK), which is the smallest possible change and the shape a reviewer already knows. Accepted cost: an OpenAI agent may still set 500 000 — meaningless, but harmless and self-inflicted, and catching it needs Q-2's machinery. |
| Q-2 | Why not validate per provider (`le = CONTEXT_LIMITS[model_hint]`)? | **Rejected.** | It ties two fields together in a Pydantic model, and it does not hold: `PATCH` can change `model_hint` alone (`agents.py:116` and its siblings are all optional), which would strand a now-illegal cap that was legal when written — so it needs a re-validation on every `model_hint` write and a decision about what to do with existing rows that a provider change invalidates. That is a materially bigger feature for a footgun the user aims at their own agent. If it is ever wanted, Q-1's global bound is a prerequisite, not a competitor. |
| Q-3 | What happens to rows already above 1 000 000? | **Clamp to the bound in the migration, and log the count.** Do not fail the migration; do not null them. | Nulling silently changes an agent's behaviour to "provider default", which is a different turn shape than the operator configured. Failing the upgrade blocks a deploy on data the operator cannot see beforehand. Clamping preserves intent as closely as the new rule allows, and it is the same value the agent would effectively have got anyway — every ceiling above the provider window already behaves as the window. Realistically the count is zero: the feature has a UI (`AgentDetailView.vue`) but no reason to enter a 7-digit number. |
| Q-4 | Bound the frontend too? | **Yes — `INPUT_LIMITS` gets the number, and the field gets a `max`.** | `inputLimits.ts:1-10` declares itself the single source of truth for the UI hard cap and counter, and its whole purpose (`:5-9`) is that the user is told *before* a 422. Shipping a backend 422 without the UI bound is exactly the drift that file exists to prevent — the same lesson `2026-07-16-agent-skills` FU-8 turned out to teach. |
| Q-5 | Is this urgent? | **No.** | The failure is self-inflicted, per-agent, inside the user's own project, and only in compact mode. It costs the user's own BYO-key spend and their own turn. There is no cross-tenant edge (§7). It is hygiene, not a leak — sequence it behind anything with a security or data-loss face. |

## 4. Acceptance Criteria

- [ ] AC-1: `POST /api/agents` with `context_token_cap = 1_000_001` returns 422; `1_000_000` succeeds;
      `null` succeeds and still means "provider default".
- [ ] AC-2: `PATCH /api/agents/{id}` enforces the same bound (`agents.py:116`), and clearing to `null`
      still works (`:339` — `clear_context_token_cap`).
- [ ] AC-3: the DB CHECK rejects an over-bound value written outside the API, mirroring
      `agents_skill_index_cap_bounded`. A direct `UPDATE` to 2 000 000 raises `IntegrityError`.
- [ ] AC-4: the migration's upgrade clamps pre-existing rows above the bound and logs how many; a
      downgrade drops the CHECK and leaves the clamped values (the data change is not reversible and
      the downgrade must not pretend otherwise).
- [ ] AC-5: `MAX_CONTEXT_TOKEN_CAP` lives in `contexts/agents/domain/models.py` beside
      `MAX_SKILL_INDEX_TOKEN_CAP`, is derived from `CONTEXT_LIMITS` rather than typed as a literal,
      and a test asserts `MAX_CONTEXT_TOKEN_CAP == max(CONTEXT_LIMITS.values())` so a future provider
      with a larger window cannot silently make the bound wrong.
- [ ] AC-6: `models.py:144-146`'s comment no longer describes `context_token_cap` as "unbounded
      everywhere"; both caps are described as bounded, and FU-10 is no longer cited as open.
- [ ] AC-7: `INPUT_LIMITS.CONTEXT_TOKEN_CAP` exists and the agent form's field carries it, so the
      counter/`max` matches the backend (Q-4).
- [ ] AC-8: compact-mode behaviour is unchanged for every legal value — `turn_engine.py:1237` is not
      touched, and the existing turn tests stay green.
- [ ] AC-9: gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck`; `alembic upgrade head` applied and the downgrade
      round-tripped.

## 5. Detailed Changes

**Backend.**
1. `contexts/agents/domain/models.py`, beside `MAX_SKILL_INDEX_TOKEN_CAP` (`:84`):
   `MAX_CONTEXT_TOKEN_CAP = max(CONTEXT_LIMITS.values())`, with a comment saying *why* that is the
   bound (above the widest provider window the value cannot help any provider) and naming the CHECK
   that mirrors it — the same sentence shape `:81-83` already uses.
2. `app/api/v1/agents.py:82` and `:116`: add `le=MAX_CONTEXT_TOKEN_CAP`; import it beside the
   existing `MAX_SKILL_INDEX_TOKEN_CAP` import at `:25`.
3. `contexts/agents/domain/models.py:143-147`: rewrite the comment per AC-6.
4. A new Alembic migration: drop and recreate the `0011` CHECK as
   `context_token_cap IS NULL OR (context_token_cap > 0 AND context_token_cap <= 1000000)`, named to
   match `agents_skill_index_cap_bounded`'s convention, preceded by the Q-3 clamp
   (`UPDATE agents SET context_token_cap = 1000000 WHERE context_token_cap > 1000000`) with a
   `rowcount` log.

**Frontend.**
5. `shared/constants/inputLimits.ts`: `CONTEXT_TOKEN_CAP: 1_000_000` with a comment naming
   `contexts/agents/domain/models.py`'s constant as the mirror — following the
   `PROMPT_ASSISTANT_SYSTEM_PROMPT` entry added by `77a44bc`, which exists because that lesson was
   learned the hard way.
6. The agent form's `context_token_cap` input gets `:max`/`maxlength` from it (`AgentDetailView.vue`
   — the implementer should confirm the exact control; FU-10 cites `:928` for the system-prompt
   counter as the neighbour).

**Reuse inventory** — nothing new is invented here:
- `MAX_SKILL_INDEX_TOKEN_CAP` (`models.py:84`) — the constant pattern, including its comment shape.
- `0056_skills.py:62` — the CHECK pattern and the cross-referencing comment.
- `agents.py:83` — the `le=` pattern on the sibling field, in the same two Pydantic models.
- `INPUT_LIMITS` (`inputLimits.ts:11`) — the UI mirror.
- `CONTEXT_LIMITS` (`models.py:42-46`) — the source of the number; do not retype 1_000_000 as a
  literal anywhere but the migration's SQL, where a literal is unavoidable.

## 6. Security Considerations

Thin, and worth stating so the implementer does not inflate it. `context_token_cap` is per-agent,
set by a caller who already holds edit rights on that agent in that project, and spends only that
project's own BYO keys. There is no cross-tenant path: the value never leaves the agent's own turn,
and `[R11.19]`'s knowledge assembly is already scoped server-side. The consequence of an absurd value
is that the agent's own turns fail at the provider after doing the retrieval work — the user's own
spend and their own latency.

The one real class is **resource exhaustion on the shared process**: a compact-mode turn assembles
knowledge up to the ceiling before dispatch, so a 50 000 000 cap makes one turn materialise a large
block in a worker shared with other tenants. That is the same shape as `2026-07-16-agent-skills`
FU-24 (the per-turn skills snapshot with no LIMIT) and is why an upper bound is worth having at all
rather than being dismissed as a footgun. It is bounded in practice by `[R11.19]`'s own retrieval
limits and by what the RAG store actually returns, so it is a MEDIUM at most — but it is the reason
this task exists, and the bound is the fix.

No new endpoint, no AuthZ change, no new field. The 422 leaks nothing.

## 7. Risks and Rollback

- **Q-3's clamp is a data write in a migration and is not reversible.** The downgrade drops the CHECK
  but cannot restore a clamped value. AC-4 makes that explicit rather than letting a reviewer assume
  symmetry. Expected rowcount is zero; the log line is how the operator finds out otherwise.
- **A future provider with a window above 1 000 000 silently makes the bound wrong.** AC-5's
  derivation (`max(CONTEXT_LIMITS.values())`) plus its test is the guard: adding such a provider
  changes the constant, and the migration's SQL literal would then disagree with it. Worth a comment
  in the migration saying the literal is a snapshot of the constant at that revision — which is true
  of every CHECK in this repo and is why `0056_skills.py:62` cross-references by line.
- **Rollback:** the API `le=` and the constant are revertible with no data implication. The CHECK
  needs a migration. The clamp does not roll back.

## 8. SRS Delta

`[R9.10]` (`REQUIREMENTS.md:370-375`) describes the compact-mode budget without stating a range for
the per-agent override, which is why nothing was violated. Add the bound to §9, beside `[R9.10]`:

> - **[R9.10a]** An Agent's `context_token_cap` override is bounded above by the widest provider
>   context window the platform supports (currently 1 000 000 — Gemini; see the model catalog). A
>   value above it is rejected at the API with a 422 and by a DB constraint: it cannot be honoured by
>   any provider, and in compact mode it would suppress compaction entirely and guarantee a rejected
>   request. `NULL` continues to mean the provider-derived default.

`docs/traceability.csv` gains a row for `[R9.10a]`. Note the file currently has zero rows for six
sections (`2026-07-16-agent-skills` FU-3) — this task adds its own row and does not fix that.

## 9. Follow-ups

- **FU-1: `general` mode has no per-agent ceiling at all.** `turn_engine.py:1238-1239` uses
  `context_limit` — the provider's hard window — so a general-mode agent cannot be given a *lower*
  budget than its provider allows. That is deliberate per `[R9.10]`'s scope ("R11.19 bounds the
  knowledge blocks in both modes without imposing R9.10 on general", `turn_engine.py:1233-1235`), but
  it means a cost-conscious operator has no lever there. Not a defect; a product gap worth naming.
- **FU-2: the migration's CHECK literal and the domain constant can drift.** Every bounded column in
  this repo has the same property (`0056_skills.py:62` cross-references `models.py:84` by comment,
  not by construction). Nothing tests that a migration's CHECK agrees with the constant it mirrors. A
  single test that reads both — or a convention of asserting the bound against the DB in an
  integration test — would close a class of bug, not just this instance.
