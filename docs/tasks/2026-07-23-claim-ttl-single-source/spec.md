---
type: refactor
status: approved
created: 2026-07-23
requirements: []
depends_on: []
---

# Claim-key TTL: one source of truth for `key life >= consumer budget`

## 1. Summary

The resume claim keys (`wf:approval` / `wf:instruct` / `wf:wait` / `wf:subagent_callback`)
are created with a TTL of `timeout_seconds + <grace>` at four independent producer sites,
where `<grace>` is a bare magic number (`300`, `300`, `60`, `60`), while the consumer-side
retry budget that the grace has to reconcile with lives as separate constants in the
worker layer. Nothing states the relationship between the two, so a future change to the
retry budget or a grace value can silently break the "a claim key must not expire inside
its consumer's retry budget" invariant that the just-landed F-32 fix
(`2026-07-22-approval-resume-claim-reliability`, commit `10a50cc`) enforces on the
consumer side. This refactor extracts the constants and the invariant into one
dependency-free module in the workflow **domain** layer, imported by both the producers
(same context) and the consumers (downward), with the numbers preserved exactly.

Source: FU-3 of `docs/tasks/2026-07-22-approval-resume-claim-reliability/spec.md`.

## 2. Motivation

**Dimension 12 (DRY / magic numbers) + an un-encoded cross-file invariant.**

The producer grace is a bare literal at four sites — five uses, since `wait_for_event`
uses it twice:

- `contexts/workflow/application/executors/approval_gate.py:131` — `ex=int(timeout_seconds) + 300`
- `contexts/workflow/application/executors/instruct.py:93` — `ex=timeout_seconds + 300`
- `contexts/workflow/application/executors/wait_for_event.py:65` — `ex=timeout_seconds + 60`
  (the claim key) and `:78` — `index_ttl = timeout_seconds + 60` (the by-event index,
  which must track the same window)
- `contexts/workflow/application/executors/subagent_spawn.py:92` — `ex=timeout_seconds + 60`

The consumer budget that the grace exists to reconcile with is a *different* set of
constants, in a *different* layer, with no cited link back to the producers:

- `app/workers/tasks/workflow_common.py:27-28` — `_RESUME_RETRY_DELAY_S = 3`,
  `_RESUME_RETRY_MAX_ATTEMPTS = 210` (⇒ 630s budget), and (post-F-32) `:31`
  `_CLAIM_RESTORE_TTL_S = _RESUME_RETRY_MAX_ATTEMPTS * _RESUME_RETRY_DELAY_S` plus the
  `_remaining_budget_ttl` helper (`:34-38`).
- `app/workers/tasks/workflow_approvals.py:29-30` — `_APPROVAL_RESUME_DELAY_S = 3`,
  `_APPROVAL_RESUME_MAX_ATTEMPTS = 210` (a numerically identical duplicate of the pair
  above).

F-32's §5 stated the invariant — *key life >= consumer budget* — and §12 FU-3 flagged
that it is "scattered across four producers and two consumers with no single place stating
[it]. Encode it once ... rather than adjusting four numbers." This is that encoding. The
defect is not a bug (F-32 already made the runtime safe by extending keys on the consumer
side); it is the absence of a single readable statement of the relationship, which is what
lets the next edit reintroduce a mismatch.

## 3. Non-goals

- **No externally observable behavior change.** Every claim key is created with the exact
  same TTL it has today (`+300` for approval/instruct, `+60` for wait/subagent, including
  the `wait_for_event` index TTL). No API, no schema, no Redis-key-name change, no change
  to the F-32 consumer-side extension logic (`_restore_claim`, `_remaining_budget_ttl`,
  the pending-poll `EXPIRE`). Q-2.
- **No new consumer for `wf:subagent_callback`.** It participates in the constant
  extraction only; it still has no claim/restore/retry consumer. Adding one stays FU-6
  (`2026-07-22-approval-resume-claim-reliability` §12). Q-3.
- **No unification of the two consumer-budget constant pairs into one name.** `_RESUME_RETRY_*`
  and `_APPROVAL_RESUME_*` both become aliases of the shared domain constants, but their
  local names are preserved so the diff stays mechanical and their existing docstrings
  keep their context.
- **No change to the retry budget or grace values themselves** — only where they are
  defined and how they cite each other.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does the canonical module live, given executors are application-layer and workers are app-layer? | **`contexts/workflow/domain/claim_ttl.py`** (new). | Domain is pure Python with no intra-project imports, so both the executors (same context, sideways) and the worker tasks (app layer, downward) can import it with no upward dependency. `application/` would also work but domain is the natural home for pure constants + a pure helper. `shared_kernel` was rejected: these keys are workflow-specific and do not belong on a surface every context sees. |
| Q-2 | Unify the producer grace to one value, or preserve the existing per-class values? | **Preserve exactly** as two named constants (`GATE_CLAIM_GRACE_S = 300`, `WAIT_CLAIM_GRACE_S = 60`). | This is a refactor: no observable behavior change (§3). The single source of truth is the module plus its docstring stating the invariant, not a single collapsed number. Unifying would change wait/subagent (or approval/instruct) TTLs — a behavior change out of scope. |
| Q-3 | Does `subagent_spawn` participate, given it has no restore/retry consumer (FU-6)? | **Include the extraction, defer the consumer to FU-6.** | Its `timeout+60` is the same producer shape and routing it through `WAIT_CLAIM_GRACE_S` completes the single-source goal (all four producers). The module docstring notes it currently has no consumer budget to satisfy; adding a real restore/retry consumer remains FU-6. |
| Q-4 | This touches the same `ex=` lines as four still-`draft` dossiers (`wait-for-event-timer-and-join-ports`, `instruct-terminal-state-guard`, `subagent-spawn-fail-fast`, `workflow-dispatch-reliability`). Depend on them? | **`depends_on: []`; land independently, recommended first.** | The change is a mechanical extraction touching only the isolated `ex=`/grace lines; it does not depend on anything those dossiers introduce, and building it first lets them rebase onto the named constants instead of re-touching magic numbers. Textual adjacency is recorded in §5 and §8 so a reviewer isn't surprised, but an overlap of unrelated edits on the same file is not a build-ordering dependency (README "overlap prerequisite"). |

## 5. Current vs Target Structure

**Current.** Two disconnected constant clusters, no shared citation:

```
contexts/workflow/application/executors/   (producers)
  approval_gate.py   ex = int(timeout) + 300     ┐  grace = magic literal
  instruct.py        ex = timeout + 300          │  at each site; no link to
  wait_for_event.py  ex = timeout + 60  (x2)     │  the consumer budget
  subagent_spawn.py  ex = timeout + 60           ┘

app/workers/tasks/                          (consumers)
  workflow_common.py   _RESUME_RETRY_DELAY_S=3, _RESUME_RETRY_MAX_ATTEMPTS=210,
                       _CLAIM_RESTORE_TTL_S=630, _remaining_budget_ttl()
  workflow_approvals.py _APPROVAL_RESUME_DELAY_S=3, _APPROVAL_RESUME_MAX_ATTEMPTS=210
```

**Target.** One domain module owns the numbers and the invariant; both sides import it:

```
contexts/workflow/domain/claim_ttl.py   (NEW — pure, no intra-project imports)
  # module docstring: states invariants I1 (grace covers resolution->first-consumer
  # latency) and I2 (consumer extends key >= remaining budget, F-32), and why grace
  # need NOT equal the budget.
  CLAIM_RESUME_DELAY_S = 3
  CLAIM_RESUME_MAX_ATTEMPTS = 210
  CLAIM_CONSUMER_BUDGET_S = CLAIM_RESUME_MAX_ATTEMPTS * CLAIM_RESUME_DELAY_S   # 630
  GATE_CLAIM_GRACE_S = 300      # approval_gate, instruct
  WAIT_CLAIM_GRACE_S = 60       # wait_for_event, subagent_spawn (see FU-6)
  def initial_claim_ttl(timeout_seconds: int, grace_s: int) -> int: ...
  def remaining_budget_ttl(max_attempts: int, delay_s: int, attempt: int) -> int: ...

        ▲ imported sideways by                    ▲ imported downward by
        │ (same context, executors)               │ (app layer, workers)
  executors/*.py                            app/workers/tasks/workflow_common.py
    ex = initial_claim_ttl(timeout,           _RESUME_RETRY_DELAY_S   = CLAIM_RESUME_DELAY_S
                           GATE|WAIT grace)    _RESUME_RETRY_MAX_ATTEMPTS = CLAIM_RESUME_MAX_ATTEMPTS
                                               _CLAIM_RESTORE_TTL_S    = CLAIM_CONSUMER_BUDGET_S
                                               _remaining_budget_ttl   = remaining_budget_ttl
                                          app/workers/tasks/workflow_approvals.py
                                               _APPROVAL_RESUME_*      = CLAIM_RESUME_*
```

**Dependency direction check (CLAUDE.md layer order).** `domain/` imports nothing from
`application/`, `infrastructure/`, or `app/`. Executors (`contexts/workflow/application/`)
importing `contexts/workflow/domain/` is a normal downward, same-context edge. Worker
tasks (`app/workers/`) importing `contexts/workflow/domain/` is a normal downward
cross-layer edge (app → contexts). No upward dependency is introduced; the executor →
`app/workers/tasks/` edge that would have been a violation is exactly what this design
avoids by placing the module in domain (Q-1).

`remaining_budget_ttl` moves from `workflow_common.py` to the domain module. To keep the
tree green with a small blast radius, `workflow_common.py` re-binds `_remaining_budget_ttl
= remaining_budget_ttl` so its current importers (`workflow_approvals.py`,
`workflow_signals.py`) keep working unchanged; the import lines may optionally be
repointed at the domain module in the same step, but that is not required for correctness.

## 6. Characterization Test Plan

The behavior to pin **before** moving anything is the exact initial TTL each producer
writes and the exact consumer-floor values — these must be byte-identical after the move.

**Existing coverage (already green, must stay unmodified):**
- `backend/tests/unit/test_workers.py` — `test_restore_claim_floors_to_min_ttl`,
  `test_restore_claim_keeps_longer_original_ttl`, `test_restore_claim_default_ttl_covers_budget`
  pin the consumer floor and that `_CLAIM_RESTORE_TTL_S >= budget`.
- `backend/tests/unit/test_workflow_k4.py` — `test_resume_approval_extends_claim_ttl_across_pending_retries`,
  `test_resume_{approval,instruct}_restores_claim_with_budget_floor`,
  `test_event_resume_restores_claim_with_budget_floor`,
  `test_claim_ttl_never_expires_before_next_retry` pin `remaining_budget_ttl`'s behavior
  through the consumers.

**Coverage gap this refactor must close first (the producer TTLs are unpinned today).**
The `_FakeRedis` in `test_workflow_k4.py` already tracks `ex` (`self.ttls`), so each
executor's initial TTL is now assertable. `/build` writes these **before** the move:
- `test_approval_gate_sets_claim_ttl_timeout_plus_gate_grace` — assert the executor writes
  `wf:approval:{id}` with `ex == timeout_seconds + 300`.
- `test_instruct_sets_claim_ttl_timeout_plus_gate_grace` — `wf:instruct` `ex == timeout + 300`.
- `test_wait_for_event_sets_claim_and_index_ttl_timeout_plus_wait_grace` — both
  `wf:wait:*` (`:65`) and the by-event index (`:78`) at `ex == timeout + 60`.
- `test_subagent_spawn_sets_callback_ttl_timeout_plus_wait_grace` — `wf:subagent_callback`
  `ex == timeout + 60`.
- A pure-unit test of the new module: `initial_claim_ttl(t, GATE_CLAIM_GRACE_S) == t + 300`,
  `initial_claim_ttl(t, WAIT_CLAIM_GRACE_S) == t + 60`, `CLAIM_CONSUMER_BUDGET_S == 630`,
  and `remaining_budget_ttl` matches the values the moved function produced.

## 7. Migration Steps

Each step leaves the tree green (pytest/ruff/mypy).

1. **Add the failing-safe characterization tests** (§6) against the current code; they
   pass against today's literals, so they are the green baseline the move must preserve.
2. **Create `contexts/workflow/domain/claim_ttl.py`** with the constants, `initial_claim_ttl`,
   and `remaining_budget_ttl` (copied verbatim from `workflow_common._remaining_budget_ttl`),
   plus the invariant docstring. Add its own unit test. Nothing imports it yet — tree stays green.
3. **Repoint the consumers.** In `workflow_common.py`, define the local `_RESUME_RETRY_*`,
   `_CLAIM_RESTORE_TTL_S`, and `_remaining_budget_ttl` as bindings to the domain values;
   delete the now-duplicated helper body. In `workflow_approvals.py`, bind `_APPROVAL_RESUME_*`
   to the domain constants. Run the full consumer test set (`test_workers.py`,
   `test_workflow_k4.py`) — values unchanged, still green.
4. **Repoint the producers**, one executor per commit if desired: replace each `ex=timeout+N`
   with `ex=initial_claim_ttl(timeout_seconds, GATE_CLAIM_GRACE_S | WAIT_CLAIM_GRACE_S)`,
   including `wait_for_event`'s `index_ttl` (`:78`). The characterization tests from step 1
   pin each unchanged. mypy confirms the import direction.
5. **Full gate**: `pytest -q` (unit), `ruff check . && ruff format --check .`, `mypy .`.

## 8. Risks and Rollback

| Risk | Mitigation |
|---|---|
| A producer is accidentally rewired to the wrong grace constant (e.g. `wait_for_event` to `GATE` grace), silently changing a TTL | The step-1 characterization tests assert each producer's exact `ex`; a wrong constant fails them. This is the whole point of writing them first. |
| `wait_for_event`'s **second** grace use (the by-event index TTL, `:78`) is missed, leaving one magic literal and a subtly divergent index/key TTL | §2 and §6 call it out explicitly; the wait test asserts both `ex` values equal `timeout + 60`. |
| Moving `remaining_budget_ttl` breaks an importer | `workflow_common.py` keeps the `_remaining_budget_ttl` name as a binding; `workflow_signals.py` / `workflow_approvals.py` imports are unchanged. mypy catches any stragglers. |
| Textual adjacency with four `draft` dossiers on the same files | Q-4: `depends_on: []`, recommended to land first; the change is confined to the `ex=`/grace lines, so a later rebase of those dossiers is trivial. Recorded here so a reviewer of the untouched-looking executors isn't surprised. |

**Rollback** — `git revert` per step; the module is additive and the consumer/producer
edits are independent. No schema, no migration, no persisted state, no API change.

## 9. Acceptance Criteria

- [ ] AC-1: no externally observable behavior change — every claim key is still created
      with `timeout_seconds + 300` (approval, instruct) or `timeout_seconds + 60` (wait
      incl. its index, subagent); all pre-existing consumer tests pass unmodified.
- [ ] AC-2: the four producers (`approval_gate.py`, `instruct.py`, `wait_for_event.py`
      both uses, `subagent_spawn.py`) contain **no** bare `+ 300` / `+ 60` grace literal —
      each derives its TTL via `initial_claim_ttl(..., <named grace>)` from
      `contexts/workflow/domain/claim_ttl.py`.
- [ ] AC-3: the consumer budget (`_RESUME_RETRY_*`, `_APPROVAL_RESUME_*`,
      `_CLAIM_RESTORE_TTL_S`) and `remaining_budget_ttl` resolve to the single domain
      source; `CLAIM_CONSUMER_BUDGET_S == 630` and equals `_CLAIM_RESTORE_TTL_S`.
- [ ] AC-4: `contexts/workflow/domain/claim_ttl.py` imports nothing from `application/`,
      `infrastructure/`, or `app/` (verified by reading its imports and by `mypy .`);
      no executor imports from `app/workers/`.
- [ ] AC-5: the module docstring states the invariant in one place — grace covers
      creation→first-consumer latency (I1), the consumer extends the key ≥ its remaining
      budget once retries begin (I2, F-32), and therefore grace need not equal the budget.
- [ ] AC-6: `pytest -q` (unit), `ruff check .`, `ruff format --check .`, `mypy .` all pass
      in `backend/`.

## 10. SRS Delta

None. Behavior is unchanged by definition; no `[Rxx.yy]` constrains the TTL constants.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- **FU-1** — `wf:subagent_callback` still has a TTL but no claim/restore/retry consumer
  (owned by FU-6 of `2026-07-22-approval-resume-claim-reliability`). Once that consumer
  exists, its budget should be sourced from `claim_ttl.py` too, closing the loop for the
  fourth key.
