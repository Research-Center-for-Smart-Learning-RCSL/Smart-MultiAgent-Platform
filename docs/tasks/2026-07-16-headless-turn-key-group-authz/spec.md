---
type: bugfix
status: draft
created: 2026-07-16
requirements: [R7.04, R7.05, R11.11]
---

# A deleted Key Group keeps spending provider keys on the headless turn path

## 1. Summary

Split from `2026-07-16-agent-skills`' FU-7, which recorded that `run_input_turn` has no
key-group AuthZ tap where `_run_locked` has one. Verification on 2026-07-17 confirmed the gap and
found the reachable defect is narrower and deeper than the entry claimed.

When a Project Owner deletes a Key Group, the room turn path stops the agent
(`turn_engine.py:1110-1128`) but **the headless path does not**: `run_input_turn` never reads the
group and hands `agent.key_group_id` straight to the provider router. The router does not
compensate — its eligibility join reaches `key_groups` only to read `project_id` and never filters
`deleted_at` (`group_repository.py:163-179`), and group deletion is a bare soft-delete that leaves
members and carries intact (`group_service.py:113-132`). So an agent bound to a deleted Key Group
keeps issuing billable calls on its owner's BYO keys, indefinitely, over A2A `call`/`instruct` and
the approval-vote worker — while the same agent in a chatroom is correctly stopped. The room path's
own guard is the evidence this is unintended.

FU-7's three other claims (uncapped headless knowledge budget, no file staging, block order held by
a comment) are **not** in scope; the first is owned by
`2026-07-17-headless-knowledge-token-budget`.

## 2. Observed vs Expected

**Observed.** `run_input_turn` (`backend/contexts/agents/application/runtime/turn_engine.py:574-665`)
resolves the agent at `:603`, and the next thing it touches is `models = _resolve_models(agent)`
(`:606`). `KeyGroupRepository` is never constructed on this path; `agent.key_group_id` is first read
when it is passed to `ProviderRouter.call_stream` (`:2033`, `:2109`). The router takes `group_id` on
trust (`provider_router.py:400-418`) and resolves members via `_load_eligible` (`:736-753`), whose
own comment (`:739-744`) says it filters withdrawn carries and soft-deleted **keys** — it says
nothing about a soft-deleted **group**, because that check is `_run_locked`'s job.

`_run_locked` does have it (`turn_engine.py:1108-1128`): `get_active(agent.key_group_id)` then
`group is None or group.project_id != agent.project_id` → audit `agent.turn_skipped`
`{"reason": "key_group_scope"}` → commit → emit → `TurnResult(status="skipped")`.

**Expected.** No requirement states this directly — see Q-3. The governing intent is `[R7.04]`
(`REQUIREMENTS.md:274`): when key material is revoked, "Active outbound calls using those keys
complete but **no new calls are issued**." The codebase already treats that as the binding principle
for the router: `provider_router.py:601` cites R7.04 by name for exactly this property on the pinned
path ("so a withdrawn BYO key is never billed"). Deleting a Key Group is a revocation of the group;
no new calls should issue through it. `[R7.05]` (`:275`) states the project-isolation half, and
`[R11.11]` (`:524`) is the one place the SRS says a key-group reference is project-membership
validated. §11 drafts the missing requirement.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Fix the turn-engine tap, the router, or both? | **Both.** Add `kg.deleted_at IS NULL` to `list_ordered_carried`'s join, **and** add the tap to `run_input_turn`. | They do different jobs. The router filter is what actually stops the money and fixes every present and future caller at the layer that spends it; the tap is what makes the stop *actionable* (a `key_group_scope` audit and reason instead of an opaque `KeyGroupExhausted` that is indistinguishable from "all keys exhausted"). A tap alone patches one instance of a systemic mistake — §6 lists the others. |
| Q-2 | The GraphRAG/knowmap builder has the same shape (validated on write, unverified at dispatch). In scope? | **In scope.** The router filter fixes its embedding path for free; add a dispatch-time re-check for its extraction path. | `embed_resolution.py:107` — the resolver shared by the GraphRAG builder *and* RAG retrieval — also goes through `list_ordered_carried`, so Q-1's one join condition closes it without touching the knowledge context. The extractor reaches the router by group id (`graphrag_builder.py:308-312`), so it is covered too, but only as "no eligible members"; a pre-flight re-check turns that into a named failure. |
| Q-3 | The SRS never states that a live, in-project key group is required. Cite what? | **Cite `[R7.04]`'s principle and add the missing requirement** (§11). Also correct the `[R7.02]` mis-citation at `agent_service.py:6`. | A bugfix needs an intent source or it is a guess. The intent here is real but unwritten: three hand-rolled copies of the rule exist in code (§6) and `[R7.04]`/`[R7.05]`/`[R11.11]` circle it without ever saying it. `agent_service.py:6` cites `[R7.02]` for this rule; `[R7.02]` (`REQUIREMENTS.md:254`) is about per-provider validation endpoints — the code itself hedges with "R7.02 *spirit*". |
| Q-4 | Keep the `project_id != agent.project_id` arm, given no API can produce that state? | **Keep it.** | It is free, it is what `_run_locked` already does, and dropping it would re-introduce the divergence this task exists to remove. Verified unreachable today (§5), so it is defence-in-depth and §1 does not claim it as the exploit. |
| Q-5 | The approvals worker ignores `TurnResult.status` entirely. Fix here? | **No — FU.** | It is a pre-existing gap on a different axis (it swallows *every* skip reason, including today's `agent_gone`), and fixing it means deciding what an approver that cannot vote should do to the gate. That is its own decision. Recorded as FU-1. |

## 4. Reproduction

Deterministic, no timing dependency.

1. Project P has Key Group G with one carried, working provider key. Agent A (in P) has
   `key_group_id = G`.
2. Confirm A answers in a chatroom, and answers over A2A `call`.
3. As Project Owner, delete G: `DELETE /api/v1/key-groups/{G}` → `KeyGroupService.delete`
   (`group_service.py:113-132`) soft-deletes the row only; `key_group_members` and `key_projects`
   are untouched.
4. Trigger A in a chatroom → **turn is skipped**, `agent.turn_skipped{reason: key_group_scope}` is
   audited, the room shows `key_group_scope` (`turn_engine.py:1111-1128`). Correct.
5. Trigger A over A2A `call` (or `instruct`, or an approval vote) → **the turn runs to completion
   and bills the key.** `_load_eligible` still returns G's members because
   `list_ordered_carried` never filters `key_groups.deleted_at`.

Step 5 is the defect. Steps 4 and 5 differ only in the entry point.

## 5. Root Cause Analysis

The causal chain, earliest link first:

1. **`list_ordered_carried` does not require the group to be live** —
   `backend/contexts/keys/infrastructure/group_repository.py:163-179`. The statement joins
   `key_groups` (`:166`) solely to reach `kg.c.project_id` for the carry predicate (`:171`). It
   filters `kp.carried` (`:172`) and `ak.deleted_at` (`:175`). It never filters `kg.deleted_at`.
   **This is the root cause**: it is the earliest link whose correction prevents the symptom, and it
   is the only one at the layer that actually selects a key to spend.
2. **`_load_eligible` inherits it** — `provider_router.py:736-753`. It re-checks the *key*
   (`get_active(m.key_id)`, `:748`) but never the *group*. Its comment (`:739-744`) is precise about
   what it guarantees and correctly does not claim group liveness.
3. **`KeyGroupService.delete` is a bare soft-delete** — `group_service.py:113-132`. No member
   removal, no carry withdrawal, no fanout. Nothing else degrades the group, so link 1 has no
   backstop behind it. (Aggravating, not causal: a cascade would mask link 1 rather than fix it.)
4. **`run_input_turn` has no tap** — `turn_engine.py:603-610`. This is what makes the defect
   *reachable in production* rather than theoretical, since `_run_locked` catches it on the only
   other path.

**Why the cross-project arm is defence-in-depth, not the exploit (Q-4).** FU-7 framed this as
"spends another project's keys". That state is **not reachable today**, and the dossier does not
claim it. A Key Group cannot move: `key_groups.project_id` is written only at INSERT
(`group_repository.py:59`), no UPDATE of that column exists anywhere, `KeyGroupService` exposes only
create/rename/soft-delete/member operations (`group_service.py:61-262`), and `[R7.05]`
(`REQUIREMENTS.md:275`) forbids transferring a key to another project outright. Nor can an agent be
pointed out-of-project: both write paths validate via `_assert_key_group_in_project`
(`agent_service.py:395-398` on create, `:506-509` on patch). So the reachable arm is `group is None`
— deletion — and that is what §4 reproduces. The project arm stays because it costs nothing, it is
what `_run_locked` already does, and removing it would re-open the divergence this task closes.

**Why the omission at link 4 survived review.** `run_input_turn`'s docstring (`:584-592`) explains
that this path deliberately has "no room binding check (the A2A scope check already authorised the
caller)". That justification is correct for the *room binding* tap and covers one of `_run_locked`'s
two AuthZ taps — which makes the key-group tap's absence read as part of the same deliberate
decision. It is not. The two taps are on different axes: room binding asks *may this caller trigger
this agent* (A2A does check that); key-group scope asks *whose keys may this turn spend*. Authorising
the caller implies nothing about the agent's key group still being live.

**The systemic mistake behind all of it:** the rule "this key group is live and belongs to this
project" is a **predicate no one owns**. The *read* is shared — `KeysFacade.get_key_group`
(`contexts/keys/interfaces/facade.py:63-70`) exists and its docstring names this very purpose — but
it returns the group and leaves the comparison to the caller, so the rule itself is written out
three times: `agent_service.py:222-225` (through the facade, correctly),
`turn_engine.py:1111` (bypassing the facade for `KeyGroupRepository`, imported at `:65`), and
`graphrag_config_service.py:107-121` (raw `sa.select` against the keys context's tables from inside
the knowledge context). Three copies, three call sites that could each forget it, and one that did —
`run_input_turn`. A rule with no single owner is a rule that gets omitted; that is the shape of this
defect. FU-2.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every headless turn for an agent whose Key Group was deleted: A2A `call` and
`instruct` (`contexts/orchestration/application/a2a_handler.py`), and the approval-vote worker
(`app/workers/tasks/approvals.py:83-94`). Financial and correctness, not confidentiality — the keys
spent are the owning project's own BYO keys, so no cross-tenant material is reachable (Q-4). No bad
data is persisted; there is nothing to repair. Duration is unbounded: nothing expires a
soft-deleted group's members, so the spend continues until the agent is retargeted or deleted.

**Sibling suspects.**

| Site | Verdict |
|---|---|
| `turn_engine.py:2033`, `:2109` reached via `_run_locked` | **cleared** — `:1110-1128` |
| `turn_engine.py:2033`, `:2109` reached via `run_input_turn` | **CONFIRMED** — the reported defect |
| `embed_resolution.py:107` (`resolve_embed_key`, shared by the GraphRAG builder **and** RAG retrieval) | **CONFIRMED** — same root cause, reached through `list_ordered_carried`. Fixed for free by Q-1's join condition; pinned by its own test (§8). |
| `triple_extractor.py:100`, `knowmap_triple_extractor.py:93` (`group_id=cfg.builder_key_group_id`) | **CONFIRMED** — validated on write (`graphrag_config_service.py:107-121`, which *does* filter `deleted_at` at `:112`), never re-checked at dispatch (`graphrag_builder.py:308-312`). Q-2 puts the pre-flight re-check in scope. |
| `summariser.py:56` (`call(group_id=...)`, R9.10 compaction) | **cleared by position** — reached only from `_assemble_history` (`turn_engine.py:1229`), downstream of the `:1110` gate. `run_input_turn` assembles no history, so it never reaches this. |
| `subagent_service.py:264` (inherits `parent.key_group_id`, `[R15.18]`) | **cleared** — no independent read; inherits the parent turn's posture, which the tap now establishes. |
| `app/workers/tasks/prompt_assistant.py:57-59` | **cleared** — resolves a pinned `key_id` via `ConfigService.resolve_for_project(project_id=...)`; no group id, and the project is the subject of the query rather than an unchecked input. |
| `embedders.py:70`, `rerankers.py:63` (`call_single_key`) | **not this pattern** — pinned `key_id` with `KeyProjectScopeError` enforced at `provider_router.py:612-613`. Out of scope, not audited in depth. |

**Guards `_run_locked` has that `run_input_turn` lacks** (the parity question FU-7 actually asked):
key-group scope (**this task**); knowledge budget (**owned** by
`2026-07-17-headless-knowledge-token-budget`); the per-`(agent, room)` turn rate bucket
(`turn_engine.py:1130`) — structurally N/A without a room, but note `a2a_service.py` has no
rate or quota guard of any kind, so the headless path has no equivalent backstop (FU-3). Room
binding, compaction, and observer routing are genuinely N/A. Skills (`:635`) and notification drain
(`:639`) are already at parity.

## 7. Fix Design

Three changes, smallest first.

**(a) The root cause — `group_repository.py:163-179`.** Add `kg.c.deleted_at.is_(None)` to the
`m.join(kg, ...)` condition, and extend the docstring (`:148-158`) to state group liveness alongside
the carry guarantee it already documents. This is the same class of fix as SEC-H3, which that
docstring records: SEC-H3 moved eligibility from bare membership to *carried* membership; this adds
the third condition the join was always missing — the group itself must exist. One line, and it
fixes the headless path, `embed_resolution`, the triple extractors, and every future caller at the
layer that spends money. It does not mask the symptom because it is not a check *near* the dispatch;
it is the query that chooses the key.

**(b) The actionable stop — `turn_engine.py`.** Extract the `:1110-1111` predicate into a small
private helper on `TurnEngine` (`_key_group_out_of_scope(agent) -> bool`) and call it from both
paths.

**The helper must read through `KeysFacade.get_key_group`, not `KeyGroupRepository`.** Today
`turn_engine.py:65` imports `KeyGroupRepository` from `contexts.keys.infrastructure` and calls it
directly at `:1110` — the agents context's application layer reaching into another context's
infrastructure, which `backend/CLAUDE.md:26` forbids ("`application/` … never on SQLAlchemy
directly"; cross-context access goes through the facade). The facade method already exists and its
docstring names this exact caller: `contexts/keys/interfaces/facade.py:63-70` — "Return the active
Key Group (or None if missing / soft-deleted). **Used by the agents context (E.1) to validate that
an attached `key_group_id` belongs to the agent's project**". `AgentService` already uses it
correctly (`agent_service.py:223`, via `_assert_key_group_in_project`); only `turn_engine` bypasses
it. Routing the extracted helper through the facade means this fix **removes** an SoC break instead
of adding a second instance of one — and `get_active` is what the facade calls anyway (`:70`), so
the behaviour is identical.

In `run_input_turn`, place the guard between the `agent is None` check (`:603-605`) and
`models = _resolve_models(agent)` (`:606`) — before the `try` at `:609`, mirroring `_run_locked`
where every guard precedes the `agent.turn_started` audit. On failure: audit
`agent.turn_skipped{"reason": "key_group_scope", "key_group_id": ...}` via `self._audit(agent, None,
...)`, commit, and return `TurnResult(status="skipped", reason="key_group_scope")`. **Emit nothing**
— there is no room and both `emit_agent_finished_error` and `_emit_observation_event` require a
non-optional `chatroom_id`. This follows `_resolve_skills`' established precedent for a tap serving
both paths (`turn_engine.py:933-984`): audit unconditionally (`_audit` already accepts
`chatroom_id=None` and omits the key, `:2299-2300`), emit only when there is a room.

`_resolve_skills`' docstring declines the turn-skip semantics for skills and, in doing so, states
why they are right here: "an agent cannot run without a key, but it runs perfectly well without one
of twenty skills."

**(c) The builder pre-flight — `graphrag_builder.py`.** Before the extraction loop
(`:299-312`), re-verify `cfg.builder_key_group_id` is live and in the config's project, and fail the
build with the existing `GraphRagBuilderKeyGroupProjectMismatch` rather than letting (a) surface it
as an empty eligible-member list mid-window. Reuse `graphrag_config_service.py:107-121`'s predicate
rather than writing a fourth copy.

**Data repair: none.** Nothing incorrect was persisted; the defect is unauthorised spend, which is
not reversible from here and is out of scope. Operators who need to quantify it can query
`audit_logs` for `agent.turn_finished{mode: a2a}` against agents whose `key_groups.deleted_at` is
non-null.

**Deliberately not done:** a shared cross-context `assert_group_in_project` helper (FU-2), and a
cascade on group deletion (§5 link 3 — it would mask (a) rather than fix it).

## 8. Regression Test Plan

Failing tests first; each must fail against current code for the documented reason.

1. **`tests/unit/test_provider_router_carry_gate.py`** — the file that already pins SEC-H3, so the
   group-liveness condition belongs beside the carry condition. New test: `_load_eligible` returns
   `[]` when the group is soft-deleted, using the same `_FakeMembersRepo` shape. *Fails now*: the
   fake bypasses the join, so this test must instead exercise the real statement — see 2.
2. **`tests/unit/` — a new repository-level test for `list_ordered_carried`.** The join is the fix,
   and every existing test fakes the repo, so no current test can see it. This needs the DB. Mark it
   `integration` and assert: a live group returns its carried member; the same group soft-deleted
   returns `[]`. *Fails now*: returns the member. **This is the only test that pins the root cause**
   — the rest pin its consequences.
3. **`tests/unit/test_a2a_turn_dispatch.py`** — `run_input_turn` returns
   `TurnResult(status="skipped", reason="key_group_scope")` and calls no provider when the group is
   deleted, and again when `group.project_id != agent.project_id`. Assert the audit payload with an
   `AsyncMock` rather than the file's `_noop_audit`. *Fails now*: the turn completes.
   **Harness change required:** `_wire_engine` (`:114-137`) does not stub `te.KeyGroupRepository`; a
   stub defaulting to match must be added, mirroring `_wire_locked`'s `group=` kwarg
   (`test_no_response_notices.py:82-95`), or every existing headless test hits the real repo against
   `_FakeDB`. This is a shared-harness change — see §9.
4. **`tests/unit/test_embed_resolution.py`** — `resolve_embed_key` selects nothing when the builder
   key group is deleted. This file's docstring already frames it as the SEC-H3 regression file, so
   the group arm belongs there. *Fails now*: resolves the key.
5. **`tests/unit/test_no_response_notices.py`** — unchanged, and must stay green: it pins the room
   path's behaviour (`test_key_group_scope_emits_on_any_trigger:161-167`) and is the proof that (b)'s
   extraction is behaviour-preserving for `_run_locked`.

Note 3 fills coverage the room path never had: `_wire_locked` stubs `engine._audit` with a no-op, so
**no existing test asserts the key-group audit payload at all**.

## 9. Risks and Rollback

- **(a) changes behaviour for one unrelated caller.** `KeyGroupService.patch_member`
  (`group_service.py:190-202`) uses `list_ordered_carried` as an existence check and is **not**
  gated by `get_active` first — unlike `get_with_members` (`:84-87`), which is. After (a), patching
  a member's rotation/limit settings in a deleted group raises `KeyNotFound` where it previously
  succeeded: an API response changes from success to 404. The direction is right (a deleted group's
  members should not be editable) but it is observable and must be an explicit AC, not a surprise.
- **(a) is a fail-closed change to the money path.** If any legitimate flow depends on a
  soft-deleted group still routing, it breaks. None was found (§6), but the blast radius is every
  provider call that goes through a group, so the integration test in §8.2 is load-bearing.
- **(b) touches a function another draft dossier restructures.**
  `2026-07-17-headless-knowledge-token-budget` rewrites `run_input_turn`'s body at `:611-665` (its
  §7.1 reorders skills/tools before knowledge; its §7.5 extracts a shared request planner). This
  task's region is `:603-610`, which that dossier declares stable — no semantic collision, but a
  trivial textual one at the head of the same function. **Land this first**: it is ~10 lines in a
  stable region, and it strictly reduces the surface their planner must budget for by
  short-circuiting before any knowledge work. The real coupling is the §8.3 harness change to
  `_wire_engine`, which their new tests (their §8.1, `test_a2a_turn_dispatch.py:227-318`) will
  silently depend on.
- **That dossier also adds a second headless skip site** (its AC-5, fixed-only overflow). With this
  task's guard, `run_input_turn` will have two — a fourth and fifth instance of the audit → commit →
  return skip shape. `2026-07-16-agent-skills` FU-20(b) evaluates a `_skip_turn` helper for exactly
  this and concludes `key_group_scope`/`rate_limited` are true duplicates. Do **not** pre-emptively
  extract it here; note the duplication and let the helper land when the fourth site exists.
- **Exception propagation.** The guard adds a DB read outside the `try`. `_handle_call` wraps the
  turn (`a2a_handler.py:85-90`); `_handle_instruct` (`:130`) and the approvals worker do not, so a
  DB error there reaches arq. This exposure already exists for `AgentsFacade.get_agent` at `:603` —
  pre-existing, not a regression, but stated so a reviewer does not have to find it.
- **Rollback.** Three independent changes, each revertible alone. (a) is one join condition; (b) is
  additive; (c) is a pre-flight.

## 10. Acceptance Criteria

- [ ] AC-1: §8.2's integration test fails before the fix and passes after — `list_ordered_carried`
      returns `[]` for a soft-deleted group and its carried member while live.
- [ ] AC-2: §8.3's tests pass — `run_input_turn` returns `skipped`/`key_group_scope`, audits
      `agent.turn_skipped` with `reason` and `key_group_id`, and reaches no provider, for both a
      deleted group and a cross-project group.
- [ ] AC-3: `run_input_turn` emits no room event on the skip (there is no room), verified by the
      absence of any emit call in the §8.3 test.
- [ ] AC-4: §8.4 passes — `resolve_embed_key` selects nothing for a deleted builder key group.
- [ ] AC-5: a GraphRAG build whose `builder_key_group_id` was deleted fails with
      `GraphRagBuilderKeyGroupProjectMismatch` before the first extraction window, not with an
      empty-eligible-members error mid-build.
- [ ] AC-6: `patch_member` on a member of a deleted group raises `KeyNotFound` — the §9 behaviour
      change, pinned deliberately rather than discovered.
- [ ] AC-7: `test_no_response_notices.py` is unchanged and green — `_run_locked`'s behaviour is
      identical after the predicate extraction.
- [ ] AC-8: the room and headless paths call one shared predicate; `grep` shows no second
      hand-rolled `group.project_id != agent.project_id` inside `turn_engine.py`.
- [ ] AC-9: `turn_engine.py` no longer imports `KeyGroupRepository` from
      `contexts.keys.infrastructure` (`:65`); the predicate reads through `KeysFacade.get_key_group`,
      and `test_no_response_notices.py`'s stub is retargeted accordingly.
- [ ] AC-10: `agent_service.py:6`'s `[R7.02]` citation is corrected to the requirement added in §11,
      and the "R7.02 spirit" hedge is removed.
- [ ] AC-11: backend gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`.

## 11. SRS Delta

The rule this task restores is implemented three times in code and stated nowhere in the SRS
(Q-3). Add it to §7.4, after `[R7.09]`, and renumber nothing:

> - **[R7.09a]** A Key Group reference held by any resource (an Agent's `key_group_id`, a Knowledge
>   Map or GraphRAG config's `builder_key_group_id`) must resolve to a **live** Key Group in the
>   **same project** as the referring resource, and this is re-verified at **dispatch time**, not
>   only when the reference is written. A deleted Key Group issues no new provider calls (the
>   `[R7.04]` principle applied to the group rather than the carry); in-flight calls complete. A
>   turn or build that cannot satisfy this is skipped or failed with an actionable reason and an
>   audit record — never silently, and never by falling through to "no keys available", which is
>   indistinguishable from exhaustion.

Also correct `backend/contexts/agents/application/agent_service.py:6`, which cites `[R7.02]` for
this rule; `[R7.02]` (`REQUIREMENTS.md:254`) is about per-provider validation endpoints. That
mis-citation is why the rule looked documented when it was not.

`docs/traceability.csv` gains a row for `[R7.09a]`.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1: the approvals worker ignores `TurnResult.status` entirely.**
  `app/workers/tasks/approvals.py:90-103` consumes `result.status` for a log line and returns it;
  nothing branches on `skipped`. So an approver that cannot vote is silent and the gate falls to its
  timeout port — which the module's own docstring (`:1-10`) says the task exists to prevent. This
  predates and outlives this task: it swallows every skip reason including today's `agent_gone`.
  A2A `call` and `instruct` both handle skips correctly (`a2a_handler.py:92-108`, `:130-142`), so
  this is the one caller with no error surface. Fixing it means deciding what an approver that
  cannot vote should do to the gate — its own decision, its own dossier.
- **FU-2: the *predicate* "live key group in my project" is hand-rolled three times; only the read
  is shared.** `KeysFacade.get_key_group` (`contexts/keys/interfaces/facade.py:63-70`) is the
  shared read and its docstring names the use case — but it returns the group, so every caller still
  writes `group is None or group.project_id != project_id` itself:
  `agent_service.py:222-225` (correctly, through the facade), `turn_engine.py:1111` (bypassing it —
  fixed by §7(b)), and `graphrag_config_service.py:107-121`. The last is the worst of the three and
  is an **SoC break this task does not fix**: it builds a raw `sa.select` against
  `keys_t.key_groups` from inside the *knowledge* context's application layer, where
  `backend/CLAUDE.md:26` says application code must not touch SQLAlchemy directly and cross-context
  reads go through a facade — so it silently re-implements the facade method next door, including
  the `deleted_at` filter. The collapse is `KeysFacade.assert_group_in_project(group_id,
  project_id)` raising a shared error, replacing all three predicates. Out of scope here: it touches
  three contexts and would drag this bugfix into a refactor, and §7(a) makes the money safe without
  it. §7(b) reduces the count from three to two on the way past.
- **FU-3: the headless path has no rate or quota backstop.** `_run_locked` has a per-`(agent, room)`
  turn bucket (`turn_engine.py:1130`, `:1735`); it is not portable to a path with no room, and
  `a2a_service.py` has no rate, quota, or concurrency guard of any kind. So an A2A trigger loop
  spends provider keys with nothing but `MAX_TOOL_ROUNDS` bounding a single turn. Not this task's
  axis (that defect bills a *live* group), but it is the other half of "the headless path is missing
  the room path's spend controls".
- **FU-4: audit action names and skip reasons are bare string literals with no registry.**
  `AuditEvent.action` is a plain `str` (`shared_kernel/audit.py:105`), the column is `sa.Text`
  (`:48`), and `key_group_scope` appears 4× as a literal in `turn_engine.py` plus once in
  `frontend/src/slices/conversation/constants/agentErrors.ts:11`. Nothing validates a typo, and no
  test asserts the set of known actions. This task adds a fifth literal.
