---
type: bugfix
status: approved
created: 2026-07-27
requirements: [R15.01, R15.02, R15.04, R15.07, R15.09, R28.12]
depends_on: [2026-07-27-wakeup-config-key-preservation]
---

# A wrong-typed number in one agent's wakeup_config silently kills every_n_messages for the whole room

## 1. Summary

From `docs/audits/2026-07-27-wakeup-subsystem/findings.md` F-2 (major, confirmed). Every numeric
field in `WakeupConfig.from_dict` is coerced with a bare `int(...)`
(`backend/contexts/orchestration/domain/models.py:193,197-201,203,208,213,224`). The API accepts
`wakeup_config` as free-form `BoundedConfig`, so `{"n": null}` — the shape any serializer that emits
explicit nulls for absent optionals produces — is persisted with a 200. The parse then raises
`TypeError` inside the per-agent loop of `on_message_created`
(`backend/contexts/orchestration/application/wakeup_service.py:88-116`), aborting the whole loop, and
the caller swallows it as a best-effort dispatch failure
(`backend/app/api/v1/messages.py:266-271`). Result: no agent in that room is ever woken by
`every_n_messages` again — including agents whose own configs are perfectly valid — with no error
surfaced anywhere. The prior dossier's C2 made the parse layer total against out-of-range values but
not against wrong-typed ones, and its FU-3 (a typed boundary) was deliberately deferred; this
dossier closes both halves.

## 2. Observed vs Expected

- **Observed**
  - `backend/contexts/orchestration/domain/models.py:193` (`n`), `:197-201` (`t_minutes`),
    `:203,208,213` (the three autostop fields) and `:224` (`refresh_every_hours`) each call
    `int(...)` on a raw JSONB value before handing it to the clamp helpers at `:117-124`. The clamps
    receive an already-converted `int` and cannot defend against the conversion itself. `None` raises
    `TypeError`; a non-numeric string raises `ValueError`; a `bool` silently becomes `0`/`1`.
  - `backend/app/api/v1/agents.py:89,123` types the field as `BoundedConfig`
    (`backend/shared_kernel/validation.py:90`) — byte size, depth and node count only. No type or
    range check reaches the numeric fields, so the write returns 200.
  - `backend/contexts/orchestration/application/wakeup_service.py:88-93` parses inside
    `for agent_id in agent_ids:` with no per-agent guard, so one agent's exception ends the loop and
    discards `wake_list` for every agent already appended (`:86,114`).
  - `backend/app/api/v1/messages.py:266-271` wraps the dispatch in a best-effort `try/except`
    ("a Redis / dispatch hiccup must never fail the user's send"), which swallows the exception with
    no per-agent attribution.
  - `backend/app/workers/tasks/orchestration.py:96` parses the same way in `wakeup_agent` and fails
    the arq job.
- **Expected**
  - `models.py:108-109`: "Hard caps applied at parse time so they're enforced regardless of how the
    JSONB was written (designer UI, direct DB edit, migration, etc.)". A parse layer that raises on
    a value the API accepted does not enforce anything; it relocates the failure.
  - `backend/CLAUDE.md` Security Constraints: "All user input must be validated at the API boundary
    (Pydantic models)". `wakeup_config` is user input reaching a scheduler and, downstream, a paid
    provider call; it is not validated there today.
  - R15.07 documents `n ∈ [1, 1000]` and `t_minutes ∈ [1, 1440]`; R15.04 and R28.12 document the
    autostop defaults and hard cap; R15.09 documents `refresh_every_hours`. A value outside those
    types is not a value the SRS contemplates, and
    `docs/tasks/2026-07-22-wakeup-trigger-state-and-bounds/spec.md` Q-1 already fixed the resolution
    rule for values the SRS does not contemplate: they resolve exactly as an omitted field does.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Fix the parser, the dispatch loop, the API boundary, or some subset? | All three. The parser becomes total (never raises), the dispatch loop isolates per agent, and the API boundary rejects wrong-typed input with 422. | They answer different questions. The boundary stops new bad data and tells the designer their value was refused instead of silently rewritten — the gap the prior dossier's FU-3 and FU-5 both named. The parser must still be total because rows written before this fix, by migration, or by direct DB edit bypass the boundary entirely. The loop isolation is the difference between one misconfigured agent and a dead room, and is required regardless of the other two. |
| Q-2 | What resolution does the tolerant parser apply to a wrong-typed value? | The same one Q-1 of `2026-07-22-wakeup-trigger-state-and-bounds` chose for out-of-range values: it resolves exactly as an omitted field does (`n`→1, `t_minutes`→1, `autostop_rounds`→5, `observer_autostop_rounds`→50, `autostop_max_default`→100, `refresh_every_hours`→24). | One rule for "the stored value is not usable", not two. That dossier's §7 table is the specification; this change widens what counts as unusable from "out of range" to "out of range or wrong type" without changing any outcome. Note `n`'s and `t_minutes`' documented floors are 1, so their "omitted" resolution and their floor coincide by construction. |
| Q-3 | Does the typed boundary model forbid unknown keys? | No — `extra="allow"`. | Forced by `2026-07-27-wakeup-config-key-preservation`, which makes `wakeup_config` writes additive precisely so unmodelled designer keys survive. A model with `extra="forbid"` would reject the payload that dossier is designed to preserve. The two decisions must agree, which is why this dossier declares that one in `depends_on`. |
| Q-4 | Partial PATCH: what is persisted? | The fully normalized config. Order is fixed: merge the submitted fragment over the stored value (the key-preservation dossier's C1, including its `null` tombstones), *then* normalize the merged result to a complete config, then persist. | The merge must run first or normalization would materialize defaults for fields the caller omitted and write them over the stored values — turning a partial PATCH into a silent reset, which is a worse defect than the one being fixed. With the order fixed, the stored row becomes self-describing: an operator reading the JSONB sees the effective config rather than a fragment whose meaning depends on knowing the parser's defaults. |
| Q-5 | Does normalization also apply on create? | Yes, and to `wakeup_authored_snapshot` as well, since create mirrors the config into it (`agent_service.py:588-592`). | Otherwise the two write paths persist different shapes for the same input, and the refresh path (`wakeup_service.py:388-399`) restores a fragment over a normalized config. One shape, both paths. |
| Q-6 | Does the loop isolation swallow the failure silently, like `messages.py` does today? | No. Per-agent `except` that logs with the agent id, mirroring `evaluate_silence` (`app/workers/tasks/orchestration.py:285-291`), and continues with the remaining agents. | The audit's severity rests on silence, not on the exception. `evaluate_silence` already solved this exact problem in the same subsystem with a logged per-item guard; copying it keeps one idiom and makes the misconfigured agent identifiable in logs. |
| Q-7 | `bool` is a subclass of `int` — is `{"n": true}` valid? | No. The typed model rejects `bool` for every numeric field (`strict` on those fields), and the tolerant parser resolves it to the default. | `int(True) == 1` is the one wrong-typed input that does not raise today, so it silently becomes "wake on every message" — the most expensive possible misreading, on the user's own key. Left untyped it would be the only survivor of this fix. |
| Q-8 | Does this dossier fix the same class in `workflow_capabilities`? | No. Recorded as FU-1. | `workflow_capabilities` is read by nothing today — that is the whole subject of `2026-07-22-workflow-capability-enforcement`, which owns its validation posture. Adding a second typed model for it here would collide with that dossier's open Q-8 on migration posture. |

## 4. Reproduction

**Preconditions** a project with agents A, B, C all bound to room R with
`every_n_messages: {enabled: true, n: 3}`; a project member able to send messages; the Arq worker
running.

1. Confirm the baseline: send three messages in R and observe A, B and C each receive a
   `wakeup_agent` job (`messages.py:272-279`).
2. `PATCH /api/agents/{A}` with
   `{"wakeup_config": {"triggers": {"every_n_messages": {"enabled": true, "n": null}}}}`.
   Returns 200. `GET /api/agents/{A}` echoes the null back.
3. Send three more messages in R.
4. Observe: no `wakeup_agent` job for A, B or C. The API logs the swallowed exception from
   `messages.py:268` with no agent attribution; no audit row, no notification, no user-visible
   symptom. Every subsequent message behaves identically.
5. `PATCH` A with `{"wakeup_config": {"triggers": {"every_n_messages": {"n": "many"}}}}` —
   `ValueError`, same outcome. With `{"n": true}` — no exception, but A now wakes on *every* message
   (`int(True) == 1`, and `_clamp` at `:117-118` accepts 1 as in range).
6. Silence triggers keep working throughout, because `evaluate_silence` guards per binding
   (`app/workers/tasks/orchestration.py:285-291`) — which is what makes the failure hard to
   attribute in production.

Deterministic. Whether B and C are also lost depends only on iteration order producing A before the
loop finishes, and the swallowed exception discards `wake_list` regardless, so in practice all three
are lost on every message.

## 5. Root Cause Analysis

**Root cause: `wakeup_config` is accepted as free-form JSON at the API boundary and coerced with
unguarded `int()` in the domain parser, so a write that the boundary should have refused becomes a
parse-time exception on a hot read path.**

Causal chain:

1. `app/api/v1/agents.py:89,123` accept any JSON object within size bounds. **This is the earliest
   link whose correction prevents the symptom for new writes**, and it is the one that can tell the
   designer their value was wrong.
2. `models.py:193,197-201,203,208,213,224` coerce with bare `int()`. **This is the earliest link
   whose correction prevents the symptom for data that already exists**, which is why the fix needs
   both links and not only link 1. `models.py:108-109` already claims this layer is total.
3. `wakeup_service.py:88-93` parses per agent with no per-agent guard, so the blast radius jumps from
   one agent to the room. Aggravating factor, not the root cause: with links 1 and 2 corrected this
   loop cannot raise from parsing — but it can still raise from Redis or a facade read, so the guard
   is warranted independently.
4. `messages.py:266-271` swallows the result. Correct as designed (a wake-up dispatch must not fail
   a committed user send) and deliberately unchanged; it converts the defect from loud to silent,
   which is a property of the failure, not its cause.

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- Every `every_n_messages` wake-up in any room containing one agent with a wrong-typed numeric field,
  indefinitely and silently. Reachability requires a write that bypasses the editor —
  `SWakeupEditor.vue:105-112` clamps to integers and `normalizeWakeupConfig` never emits nulls — so
  the exposure is API clients, direct DB edits, and migrations.
- `wakeup_agent` (`orchestration.py:96`) raises the same way, failing the arq job for a `mention` or
  `release` wake even when the trigger that fired was not `every_n_messages`.
- `evaluate_silence_trigger` (`wakeup_service.py:204`) raises too, but is caught per binding
  (`orchestration.py:285-291`), so silence degrades to one dead agent rather than a dead room.
- `{"n": true}` is the fail-open member of the family: no exception, `n` becomes 1, and the agent
  wakes on every message in the room — an unbounded provider spend on the user's own key. This is the
  only variant whose failure direction is cost rather than silence.
- Data already written: any row carrying a wrong-typed value keeps it. After the fix the parser
  resolves it to the documented default, so behavior self-corrects without a migration; the row stays
  visibly wrong until its next write, at which point Q-4's normalization rewrites it.

**Sibling suspects**

Unguarded coercion of free-form JSONB in a read path:

- **CONFIRMED, in scope** all six sites in `models.py` listed above; they are one helper's worth of
  change and share one test.
- **CONFIRMED, in scope** `models.py:179-188`, `WakeupSoftBounds` parsing, passes
  `soft_raw.get(...)` through untouched. A non-numeric `n_min` does not raise in `from_dict` but
  raises `TypeError` later in `_clamp_n` (`wakeup_service.py:462-466`) during a self-modification,
  and an inverted range (`n_min > n_max`) makes `_clamp_n` return a value outside the advertised hard
  range. This is the prior dossier's FU-7, pulled in because the same coercion helper closes it and
  leaving it open would mean two rounds of the same fix on the same eight lines.
- **CLEARED** `WakeupConfig.from_dict`'s boolean fields (`:192,198,219,222` via `bool(...)`).
  `bool()` is total for every JSON value; a non-boolean simply resolves truthily. No exception path.
- **CLEARED** `models.py:86-101`, `A2AEnvelope.from_dict`. Also coerces with `int()`, but its input
  is an envelope this codebase serialized itself (`:70-84`), not user JSON, and a malformed envelope
  is already handled by the consumer's DLQ path.
- **CLEARED** `contexts/knowledge` GraphRAG trigger config. Typed field by field in its own Pydantic
  models; no free-form numeric coercion.

Best-effort `except` blocks that could hide a whole-loop abort:

- **CONFIRMED, in scope** `wakeup_service.py:88-116` — the finding.
- **CLEARED** `app/workers/tasks/orchestration.py:285-291` (`evaluate_silence`) and `:313-318`
  (`wakeup_refresh`) already guard per item. `wakeup_refresh`'s guard has a separate defect —
  it does not roll back — which belongs to `2026-07-27-wakeup-sweep-failure-isolation`, not here.
- **CLEARED** `app/api/v1/observations.py:29` calls `evaluate_message_wakeups` on the same terms;
  once the loop is guarded, that call site inherits the fix with no change of its own.

**Existing debt in the touched files** (record, do not silently fix): `models.py` mixes five
unrelated value-object families in one module (A2A envelopes, wake-up config, approvals, instruct
chains, sub-agents) — 490 lines. This change adds a private helper near the wake-up section rather
than splitting the module, because splitting it would move code four other contexts import.

**Patterns to follow**: the tolerant helper belongs next to `_clamp` and `_default_below_one`
(`models.py:117-124`) and must stay framework-free — `contexts/*/domain/` may not import Pydantic
(`backend/CLAUDE.md`), and `mypy` strict applies to `contexts.*.domain.*`. The typed boundary model
belongs in `app/api/v1/agents.py` beside `AgentCreateIn`/`AgentPatchIn`, following the
`Literal[...]` + `Field(ge=..., le=...)` idiom already used at `:82-87`.

**Reuse inventory**: `_clamp` and `_default_below_one` (`models.py:117-124`) already encode the
resolution rules — the new helper feeds them rather than duplicating them. `N_MIN`/`N_MAX`/
`T_MINUTES_MIN`/`T_MINUTES_MAX`/`AUTOSTOP_HARD_CAP` (`:110-114`) are the single source for every
bound and must be imported by the API model, not restated. The per-item guard idiom is
`orchestration.py:285-291`. `WakeupConfig.to_dict()` (`:230-262`) is the normalizer Q-4 needs — no
second serializer.

## 7. Security Considerations

`wakeup_config` is user-controlled input that drives a scheduler which spends the user's own provider
key, so the relevant dimension is resource exhaustion rather than injection or AuthZ.

- **Amplification**: `{"n": true}` resolving to `n = 1` turns every message in a room into a provider
  call per bound agent. Q-7's rejection of `bool` closes the only silent path to that state; the
  clamp at `_clamp(..., N_MIN, N_MAX)` bounds the rest.
- **Denial of the feature** is the finding itself: one tenant's misconfigured agent disabling
  wake-ups for co-bound agents in the same room. All bindings are within one project, so this is not
  a cross-tenant boundary crossing — the AuthZ check at the endpoint is unaffected — but it is a
  cross-agent availability impact within the project, which Q-6's isolation contains.
- **No new logging of secrets**: the per-agent log line records the agent id and the exception type
  only. `wakeup_config` carries no credentials, but the guard must not log the config body, since
  the column is free-form and an operator could have written anything into it.
- **Boundary rejection message** must name the offending field and the accepted range without echoing
  the submitted value back verbatim, following the existing 422 shape from Pydantic.

## 8. Regression Test Plan

**T-1 (the failing test, write this first)**
`backend/tests/unit/test_message_wakeup_dispatch.py::test_one_unparseable_config_does_not_stop_the_room`

Three agents bound; the middle one's `wakeup_config` carries `{"triggers": {"every_n_messages":
{"enabled": true, "n": None}}}`. Assert `on_message_created` returns the wake list for the other two
and does not raise. Fails today: `wakeup_service.py:93` raises `TypeError` and the call propagates it.

**T-2** `backend/tests/unit/test_wakeup_service.py::test_wakeup_config_resolves_wrong_typed_fields_to_defaults`

Extends the clamp coverage added by the prior dossier's T-4 (`:160-200`). For each of `n`,
`t_minutes`, `autostop_rounds`, `observer_autostop_rounds`, `autostop_max_default` and
`refresh_every_hours`, assert `from_dict` returns the Q-2 default for `None`, `"abc"`, `[]`, `{}`,
`True` and `False`, and that `to_dict()` round-trips the result. Fails today on every case except the
booleans, which fail on the assertion rather than by raising.

**T-3** `backend/tests/unit/test_wakeup_service.py::test_soft_bounds_tolerate_wrong_typed_values`

`soft_bounds: {"n_min": "five", "n_max": None, "t_minutes_min": true}` parses to bounds that
`_clamp_n` and `_clamp_t` accept, and an inverted `{"n_min": 900, "n_max": 3}` yields a clamp result
inside `[N_MIN, N_MAX]`. Fails today: `models.py:179-188` stores the raw values and
`wakeup_service.py:464` raises `TypeError` on the comparison.

**T-4** `backend/tests/unit/test_agents_api_models.py::test_patch_rejects_wrong_typed_wakeup_fields`
(the existing home for `AgentCreateIn` / `AgentPatchIn` model-level assertions)

`PATCH` with `{"wakeup_config": {"triggers": {"every_n_messages": {"n": null}}}}` returns 422 naming
`n`; the same for `"many"`, `true`, `0` and `5000`. A payload carrying an unmodelled root key
(`designer_note`) plus valid numbers returns 200 and keeps the key (Q-3). Fails today: all of these
return 200.

**T-5** `backend/tests/unit/test_agent_service.py::test_wakeup_config_is_normalized_before_persisting`

Stored config has `n = 8` and `soft_bounds`; patch with `{"triggers": {"silence_minutes":
{"t_minutes": 9}}}`. Assert the persisted dict is a complete config in which `n` is still 8 (merge
ran first, Q-4), `t_minutes` is 9, every other documented field is present at its default, and
`soft_bounds` survives. Fails today: the fragment is persisted as-is.

**T-6, guard against over-fixing**
`backend/tests/unit/test_wakeup_self_modification.py` and the prior dossier's T-4/T-7 assertions must
keep passing: normalization must not overwrite a designer's stored value with a default, and the
self-modification clamp must still see `soft_bounds`. Passes today and must pass after.

**T-7** `frontend/` — after `pnpm run gen:api`, `pnpm run check:openapi-drift` and `pnpm typecheck`
must pass with the regenerated `wakeup_config` type. No new frontend test; the existing
`AgentDetailView.test.ts` and `workflow.test.ts` are the regression surface.

## 9. Risks and Rollback

- **The 422 is a breaking change for existing API clients** that today write nonsense and get a 200.
  That is the point of the fix, but it must be in the release note, and the §4 query from the
  key-preservation dossier's §7 has a counterpart here worth running before deploy:
  `SELECT count(*) FROM agents WHERE deleted_at IS NULL AND jsonb_typeof(wakeup_config #> '{triggers,every_n_messages,n}') NOT IN ('number','null');`
- **Q-4's normalization rewrites every config on its next write.** Rows currently storing
  `{"triggers": {}}` become complete configs. Behavior is unchanged by construction (the parser
  resolves omitted fields to the same values), but the stored bytes change for nearly every agent,
  and any consumer that diffs `wakeup_config` — including the drift check at
  `wakeup_service.py:392-394` — sees a one-time change. That check compares `wakeup_config` against
  `wakeup_authored_snapshot`; because Q-5 normalizes both on the same write, they stay equal and no
  spurious refresh is triggered. **This is the single most important thing for /build to verify**;
  T-5 and T-6 pin it.
- **The typed model changes the OpenAPI schema**, so `pnpm run gen:api` must be re-run and
  `check:openapi-drift` will fail until it is. `AgentOut.wakeup_config` stays
  `dict[str, Any]` (`agents.py:147`) deliberately — typing the response too would force every stored
  legacy shape through the model on read, which is a read-path failure mode this fix exists to avoid.
- **Tolerant parsing hides misconfiguration.** After this change a wrong-typed value written by
  migration or direct DB edit resolves silently to a default, with no audit — the prior dossier's
  FU-5 in a new place. Accepted deliberately: the boundary now rejects the reachable path, and
  emitting audit from a frozen domain dataclass would break the layer boundary. Recorded as FU-2.
- **Rollback** the parser, loop-guard and boundary changes are independently revertable. One
  constraint: do not revert the parser tolerance while the boundary model is live and normalizing —
  normalization depends on `from_dict` not raising for rows written before the boundary existed.

## 10. Acceptance Criteria

- [ ] **AC-1** T-1 fails before the fix and passes after: one agent's unparseable config does not
      suppress wake-ups for the other agents in the room, and the failure is logged with that agent's
      id.
- [ ] **AC-2** T-2 passes: `from_dict` never raises for any JSON value in any numeric field, and
      resolves each to the Q-2 default.
- [ ] **AC-3** T-3 passes: `soft_bounds` tolerates wrong-typed and inverted values, and `_clamp_n` /
      `_clamp_t` always return a value inside the hard range.
- [ ] **AC-4** T-4 passes: the API returns 422 for a wrong-typed or out-of-range numeric field,
      naming the field, and still returns 200 for a payload carrying unmodelled root keys.
- [ ] **AC-5** T-5 passes: a partial PATCH is merged first and normalized second, so no omitted field
      is reset and the persisted config is complete.
- [ ] **AC-6** T-6 passes: no self-modification or key-preservation assertion regresses, and the
      refresh drift check does not fire spuriously after normalization.
- [ ] **AC-7** `pnpm run gen:api` has been re-run and `check:openapi-drift` passes.
- [ ] **AC-8** The §4 reproduction no longer reproduces: step 2 returns 422, and with the bad value
      injected directly into the database, step 3 still wakes B and C.
- [ ] **AC-9** Definition of Done: `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` in
      `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` in `frontend/`. `mypy`
      strict applies to `contexts.orchestration.domain`, so the parser helper must type-check under
      strict mode.

## 11. SRS Delta

None. R15.01, R15.04, R15.07, R15.09 and R28.12 already document the types and ranges; the code
accepted values outside them. The resolution rule for invalid values was added to R15.04 and R28.12
by `2026-07-22-wakeup-trigger-state-and-bounds`'s SRS Delta and needs no further amendment — Q-2
applies the same rule to a wider class of invalid input.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** `workflow_capabilities` is the same free-form `BoundedConfig` shape
  (`agents.py:90,124`) with the same absence of validation. Deliberately excluded per Q-8;
  `2026-07-22-workflow-capability-enforcement` owns it and must decide its validation posture
  together with its migration posture.
- **FU-2** Parse-time resolution of an invalid value is silent — no audit row, no API signal for
  values that arrive by migration or direct DB edit. This is the prior dossier's FU-5 restated for
  wrong-typed input. A digest-level audit on parse-time correction would close both.
- **FU-3** `AgentOut.wakeup_config` remains `dict[str, Any]` (`agents.py:147`), so the generated
  client still sees an untyped record on read. Typing the response requires deciding what to do with
  a legacy row that does not fit the model, which is a read-path availability question, not a
  validation one.
- **FU-4** `messages.py:266-271` swallows dispatch failures with no metric. After this fix the loop
  attributes failures per agent in logs, but there is still no counter an operator could alert on.
  `WAKEUP_FIRES` (`orchestration/infrastructure/metrics.py`) is the natural home for a companion
  failure counter.
