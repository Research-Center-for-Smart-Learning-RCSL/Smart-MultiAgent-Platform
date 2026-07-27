---
type: bugfix
status: implemented
created: 2026-07-28
requirements: [R15.07, R15.08, R15.09, R15.18]
depends_on: []
---

# Wake-up config type-tolerance gaps and a stale duplicate-normalize/duplicate-clamp pair

## 1. Summary

Seven findings from an independent `/code-review` of the branch (2026-07-27), all in the
`wakeup_config` / `workflow_capabilities` PATCH-and-parse surface last touched by
`2026-07-27-wakeup-config-type-validation` and `2026-07-27-wakeup-config-key-preservation`
(both `status: implemented`). Verified against current `main` before being accepted as real
(one directly, by reading the cited code; the other four via an independent verification pass) —
none is trusted from the review's own wording alone.

**F-1 (major).** `WakeupConfig.from_dict` (`backend/contexts/orchestration/domain/models.py:208-269`)
raises an unhandled `AttributeError` when `triggers`, or any of its three sub-keys, is a truthy
non-dict value (e.g. `{"triggers": "x"}`), because only `soft_bounds` is `isinstance`-guarded before
`.get()` is called. `_validate_wakeup_config` (`backend/app/api/v1/agents.py:93-146`) only validates
`triggers`' numeric leaves when `isinstance(triggers, dict)` is already true, so a non-dict `triggers`
skips validation silently and reaches the domain parser unguarded — a 500 on the PATCH endpoint, or a
failed arq job from `wakeup_agent` (`app/workers/tasks/orchestration.py`, no try/except around its own
`WakeupConfig.from_dict` call). This is exactly the crash class `2026-07-27-wakeup-config-type-validation`
was built to eliminate, in a spot that dossier's validation coverage did not reach.

**F-2 (major).** The four `enabled` trigger flags (`every_n_messages`, `silence_minutes`, `call_only`)
plus `allow_self_open` are coerced with a bare `bool(...)` at `models.py:233,237,260,263`, with no type
check at the API boundary. `bool("false")` is `True` in Python, so submitting the string `"false"`
where a boolean is expected silently *enables* what the caller meant to disable — the opposite of
intent, with no error raised anywhere.

**F-3 (moderate).** `_validate_wakeup_config` only checks `soft_bounds`' numeric leaves when
`soft_bounds` itself is already a dict (`agents.py:132`); a non-dict `soft_bounds` is silently accepted.
`WakeupConfig.from_dict` then resolves `soft_bounds=None` for that input (`models.py:227-229`), so
`to_dict()` omits the `soft_bounds` key entirely (`models.py:271-291` has no `else` branch writing it).
`merge_json_config`'s rule — a key absent from the patch keeps the stored value — then means the
original garbage string is never overwritten by any subsequent PATCH; it persists indefinitely,
defeating R15.08's per-agent soft-bounds feature.

**F-4 (minor, confirmed already reachable in normal use — not just a contrived input).**
`AgentService.patch`'s `workflow_capabilities` merge (`agent_service.py:853-858`) uses the same
`merge_json_config` (`shared_kernel/json_merge.py`) as `wakeup_config`'s additive merge, whose rule is
that an explicit `null` in the patch **deletes** that key rather than storing a literal `null`.
`frontend/src/slices/agents/views/AgentDetailView.vue:424-431`'s `assemblePayload()` sends
`max_alive_subagents: null` whenever subagent creation is toggled off, and that payload reaches the
PATCH call at `AgentDetailView.vue:499` — so every such save already exercises delete-the-key semantics
in production, with no test pinning that this is the intended behavior.

**F-5 (minor).** `agent_service.py:850`'s `values["wakeup_authored_snapshot"] = merged_snapshot or None`
is dead code: `_merge_and_normalize_wakeup`'s non-`replace` path (`agent_service.py:394-404`) always
re-merges `WakeupConfig.from_dict(merged).to_dict()` (`_normalize_wakeup_config`, `:368-391`), which
unconditionally emits `triggers`/`allow_self_open`/`refresh_every_hours` — `merged_snapshot` can never
be falsy. A side effect of the same additive-merge design: `wakeup_config: {}` used to reset an agent's
config to blank (`merge_json_config` on an empty patch has nothing to iterate, so it is a no-op against
the stored value) — the shorthand silently stopped working when the merge became additive.

**F-6 (minor, performance only).** When `current.wakeup_authored_snapshot is None`,
`snapshot_base` (`agent_service.py:838-843`) falls back to `current.wakeup_config` — the exact same
`base` already passed to the `values["wakeup_config"] = _merge_and_normalize_wakeup(...)` call five
lines above (`:820-825`), with the same `patch` and the same `replace` flag. `_merge_and_normalize_wakeup`
therefore runs its merge → normalize → merge → bounds-check chain twice on byte-identical inputs. This
is the same class of redundant computation already fixed once in this file for a different call site
(commit `f5751fc`, `WakeupService.update_wakeup`'s `_build_new_dict`), left unaddressed here because
that fix's own reasoning ("safe because this call is always the system actor, so it never touches the
snapshot branch") explicitly does not reach the snapshot branch, which only human (non-system) actors
exercise.

**F-7 (minor, DRY only).** `WakeupService._clamp_n`/`_clamp_t` (`wakeup_service.py:494-504`) each end in
`return max(lo, min(hi, value))` — a literal copy of `_clamp`'s body
(`models.py:119-120`, `def _clamp(value, minimum, maximum): return max(minimum, min(maximum, value))`).
`_clamp` is private and not `__all__`-exported, so reuse requires either exporting it or accepting the
duplication; `_clamp_n`/`_clamp_t`'s `lo`/`hi` derivation (combining hard bounds with per-agent
`WakeupSoftBounds`) is not itself duplicated — only the final clamp formula is.

**Do these share a root cause?** F-1/F-2/F-3 share one: the tolerant-coercion pattern
`2026-07-27-wakeup-config-type-validation` established for numeric fields (`_tolerant_int`,
`_tolerant_soft_bound`, `_check_wakeup_int`) was never extended to `triggers`'s dict-ness or to the
boolean leaves — the same class of gap, three instances. F-4/F-5 share a root cause too: both are
consequences of `2026-07-27-wakeup-config-key-preservation`'s additive-merge redesign reaching further
than that dossier's own analysis covered (`workflow_capabilities` inheriting semantics it didn't ask
for; the `{}`-reset shorthand silently breaking). F-6 and F-7 are independent, unrelated performance/DRY
observations bundled here because they were found by the same review pass over the same files, per the
user's explicit choice to combine all seven into one dossier.

## 2. Observed vs Expected

**F-1**

- **Observed** — `models.py:211`: `triggers_raw = raw.get("triggers") or {}`. A truthy non-dict
  `raw["triggers"]` (e.g. `"x"`) survives the `or {}` (only falsy values are replaced), so
  `triggers_raw.get("every_n_messages")` at `:212` raises `AttributeError: 'str' object has no
  attribute 'get'`. `agents.py:100-101`: `triggers = value.get("triggers"); if isinstance(triggers, dict):`
  — the entire validation block, including the `n`/`t_minutes`/autostop checks, is skipped for a
  non-dict `triggers`, so Pydantic accepts the payload and the crash reaches `AgentService.patch` /
  `WakeupConfig.from_dict` unguarded. `app/workers/tasks/orchestration.py`'s `wakeup_agent` task calls
  `WakeupConfig.from_dict(agent.wakeup_config)` with no surrounding try/except, so any row already
  carrying a bad `triggers` value (written before this fix, or written by a client that predates
  API-level validation entirely) fails that arq job outright, every time it runs.
- **Expected** — the API boundary rejects a malformed `wakeup_config` with a 422 naming the field
  (`_check_wakeup_int`'s own stated contract, `agents.py:78-90`: "refused with 422 naming the field,
  instead of being silently persisted"), and the domain parser tolerates whatever is already stored,
  matching `_tolerant_int`'s contract (`models.py:129-141`: "Anything that is not a genuine value ...
  resolves to [a safe default]").

**F-2**

- **Observed** — `models.py:233,237,260,263`: `bool(enm.get("enabled", False))` and its three
  siblings. `bool("false") == True`, `bool("0") == True`, `bool([]) == False` — any truthy non-bool
  silently resolves to `True` regardless of the submitter's intent, and no site in `agents.py` checks
  these leaves' types.
- **Expected** — same 422-on-wrong-type contract F-1 expects, applied to the boolean leaves: the
  precedent is `_check_wakeup_int`'s explicit callout that `bool` itself must be rejected where an int
  is expected (`agents.py:80`, "including `bool`, Q-7") — the mirror-image gap (a non-`bool` where a
  bool is expected) was never closed.

**F-3**

- **Observed** — `agents.py:131-137`: `soft_bounds = value.get("soft_bounds"); if isinstance(soft_bounds, dict): ...` skips silently for a non-dict value. `models.py:215-229`: `soft_raw = raw.get("soft_bounds")`, and `soft_bounds = WakeupSoftBounds(...) if isinstance(soft_raw, dict) else None` — a non-dict `soft_raw` resolves to `None`. `to_dict()` (`models.py:271-296`, per the earlier read) has no unconditional `"soft_bounds"` key when `self.soft_bounds is None`. `merge_json_config`'s additive rule (a key the normalize step doesn't re-emit is left as whatever the stored value already was) means the original bad `soft_bounds` value is never touched by a later PATCH, however many times the config is subsequently edited.
- **Expected** — R15.08 ("Platform Admin can also set soft per-agent bounds") implies `soft_bounds` is
  meaningful admin-set data; a wrong type should be refused at write time (matching F-1/F-2's expected
  contract), not silently frozen into the stored config forever.

**F-4**

- **Observed** — confirmed reachable in normal use (§1). No test asserts what `null` does to
  `workflow_capabilities` under the additive merge.
- **Expected** — per the user's decision (Q-1 below), this *is* the intended behavior; the gap is
  test coverage, not code.

**F-5**

- **Observed** — confirmed dead code and a workaround-only reset (§1).
- **Expected** — per the user's decision (Q-2 below), the workaround is accepted; the gap is the dead
  fallback and an undocumented behavior change, not missing functionality.

**F-6 / F-7**

- **Observed** — confirmed redundant computation / duplicated formula (§1).
- **Expected** — no functional expectation violated; these are hygiene fixes with no user-visible
  effect either way.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is `workflow_capabilities`' null-deletes-key semantic (F-4) intentional? | Yes — keep it, add a regression test pinning it. | It matches the additive-merge design already established for `wakeup_config` via the same `merge_json_config`, and the user confirmed no divergent semantics are wanted for `workflow_capabilities`. |
| Q-2 | Restore a `wakeup_config: {}` reset shorthand (F-5), or accept the per-key-null workaround? | Accept the workaround. Remove the dead `or None` fallback and document the behavior change; no new reset mechanism. | The workaround (explicit per-key nulls) already achieves a full reset; adding a second reset mechanism for the same outcome is complexity the user did not ask for. |
| Q-3 | One combined dossier for all seven findings, or split by severity? | One combined dossier. | User's explicit choice, made after severity was presented per-finding (F-1/F-2 major, F-3 moderate, F-4-F-7 minor) — recorded here so the grouping reads as a decision, not an oversight. |

## 4. Reproduction

**F-1** (deterministic)

1. `PATCH /api/agents/{id}` with `wakeup_config: {"triggers": "x"}`.
2. Observe: 500 (unhandled `AttributeError` inside `AgentService.patch` → `_merge_and_normalize_wakeup`
   → `_normalize_wakeup_config` → `WakeupConfig.from_dict`).
3. Separately: seed an agent's `wakeup_config` column directly with `{"triggers": "x"}` (bypassing the
   API, e.g. via a fixture or a pre-fix write), then run the `wakeup_agent` arq task for that agent.
   Observe: the job raises and fails, every time it is retried, until the row is corrected out-of-band.

**F-2** (deterministic)

1. `PATCH /api/agents/{id}` with `wakeup_config: {"triggers": {"call_only": {"enabled": "false"}}}`.
2. `GET` the agent. Observe: `wakeup_config.triggers.call_only.enabled` reads back as `true`.

**F-3** (deterministic)

1. `PATCH /api/agents/{id}` with `wakeup_config: {"soft_bounds": "not-a-dict"}`. Observe: 200, no error.
2. `GET` the agent's stored `wakeup_config` (or inspect the DB row). Observe: `soft_bounds` is the
   literal string `"not-a-dict"`.
3. `PATCH` again with any unrelated `wakeup_config` field. Observe: `soft_bounds` is still
   `"not-a-dict"` — untouched by the merge.

**F-4 / F-5 / F-6 / F-7** — no user-visible reproduction; see §1's citations for the direct code paths.

## 5. Root Cause Analysis

**F-1/F-2/F-3, root cause: the tolerant-coercion pattern this file already uses for numeric leaves
(`_tolerant_int`, `_tolerant_soft_bound`) was never extended to dict-shaped containers or boolean
leaves, and the API-boundary validator's `isinstance` guards fail open (skip validation) instead of
failing closed (reject) when the guarded value is the wrong type.**

1. `2026-07-27-wakeup-config-type-validation` added `_check_wakeup_int`/`_tolerant_int`/
   `_tolerant_soft_bound` for every *numeric* leaf, and `isinstance(soft_raw, dict)` for the one
   dict-shaped container it happened to touch (`soft_bounds`).
2. `triggers` and its three sub-objects (`every_n_messages`, `silence_minutes`, `call_only`) never
   received the same dict-ness guard in the domain parser, and `agents.py`'s validator guards them with
   `isinstance(..., dict)` used to *skip* validation on a mismatch rather than to *reject* the payload.
   **This is the earliest link**: closing it (guard-and-reject in the validator; guard-and-tolerate in
   the parser) prevents F-1 and F-3 outright.
3. The boolean leaves (`enabled`, `allow_self_open`) never received an analogous `_tolerant_bool` /
   `_check_wakeup_bool` pair at all — F-2's root cause is simply that this leaf type was never covered
   by the original dossier's design, not a regression from something that used to work.

**F-4/F-5, root cause: `2026-07-27-wakeup-config-key-preservation` redesigned `merge_json_config`'s
null-deletes-key semantics for `wakeup_config` specifically, and `AgentService.patch` reused the same
function for `workflow_capabilities` without a separate design pass, and without re-checking whether
`wakeup_config`'s own `{}`-is-a-no-op consequence was acceptable for the reset use case.**

1. The additive merge was designed and evidenced against `wakeup_config`'s own needs (preserving
   designer-set keys a partial PATCH doesn't mention).
2. `workflow_capabilities`'s PATCH branch (`agent_service.py:853-858`) started using the identical
   `merge_json_config` call as a side effect of both fields sharing the helper, not from a decision
   that `workflow_capabilities` should have the same null semantics — it happened to be correct (Q-1),
   but this dossier is the first place that was actually checked.
3. Separately, no one re-verified whether `wakeup_config: {}` — previously an implicit "reset" the old
   replace-whole-column write supported — still worked once merge became additive; it silently stopped,
   caught only by this review.

**F-6, root cause: the snapshot-base fallback (`current.wakeup_config` when no `wakeup_authored_snapshot`
exists) was written as an independent expression instead of as a reference to the wakeup_config branch's
own already-computed result.**

**F-7, root cause: `_clamp_n`/`_clamp_t` were introduced (for soft-bounds clamping) without checking
that `_clamp`'s one-line formula already existed one file over.**

## 6. Blast Radius and Sibling Suspects

**Blast radius**

- F-1: any client or migration that writes a non-dict `triggers` reaches an unhandled 500 on every
  subsequent PATCH touching `wakeup_config` for that agent (the crash is in the merge/normalize path,
  which every wakeup_config PATCH goes through), and fails the hourly `wakeup_refresh` sweep's
  per-agent `refresh_wakeup_config` call and the `wakeup_agent` task for that agent specifically —
  bounded by C1 of `2026-07-27-wakeup-sweep-failure-isolation`, which already ensures one such
  failure does not abort the whole sweep, but the affected agent's wake-up remains broken until the
  data is corrected.
- F-2: silent inversion of designer intent for any of four boolean toggles, indefinitely, with no
  error surfaced anywhere — an agent could be silently switched into `call_only` mode (suppressing
  `every_n_messages`/`silence_minutes` per their mutual-exclusivity) by a caller trying to do the
  opposite.
- F-3: one corrupted `soft_bounds` value, permanent until manual DB correction, for the affected agent
  only — no cross-agent or cross-tenant spread (each agent's config is independent).
- F-4: informational only per Q-1 — no fix, no blast radius to bound.
- F-5/F-6/F-7: no functional blast radius (dead code, redundant computation, duplicated formula).

**Sibling suspects**

Non-dict-tolerant `.get()` chains in `WakeupConfig.from_dict`:

- **CONFIRMED, in scope** `models.py:211-214` (`triggers_raw`, `enm`, `sm`, `co`) — F-1.
- **CLEARED** `models.py:215-229` (`soft_raw`) — already `isinstance`-guarded; this is the pattern F-1's
  fix extends to the other four.

Bare `bool(...)` coercions on a JSONB-sourced leaf:

- **CONFIRMED, in scope** `models.py:233,237,260,263` (the four `enabled`/`allow_self_open` sites) — F-2.
- Repo-wide grep for `bool(.*\.get(` over `backend/contexts/*/domain/` and `backend/app/api/v1/` found
  no other JSONB-config leaf coerced the same way; `workflow_capabilities`' three booleans
  (`can_instruct`, `can_approve`, `can_create_subagent`) are read via a different accessor
  (`AgentToolCapabilities`-style typed access, not `WakeupConfig.from_dict`'s pattern) and are out of
  this dossier's scope — no evidence of the same bug there, not chased further here.

`isinstance(..., dict)`-guards that skip rather than reject in `_validate_wakeup_config`:

- **CONFIRMED, in scope** `agents.py:101` (`triggers`) and `:132` (`soft_bounds`) — F-1/F-3.
- **CLEARED** every numeric leaf under a confirmed-dict container (`n`, `t_minutes`, the three
  autostop fields, `refresh_every_hours`) already raises via `_check_wakeup_int` when present and
  wrong-typed — the gap is specifically the *container* type, not the leaves already covered.

Duplicate merge/normalize computation:

- **CONFIRMED, in scope** `agent_service.py:838-850` (the snapshot-branch fallback) — F-6.
- **CLEARED** `wakeup_service.py:357` (`WakeupService.update_wakeup`'s system-actor write) — already
  fixed by commit `f5751fc`, which set `replace_wakeup_config=True` there; that fix's own reasoning
  explicitly does not extend to the snapshot branch (system-actor writes never touch it), so it is a
  cleared sibling, not a second instance of the same unfixed bug.

Duplicated clamp formula:

- **CONFIRMED, in scope** `wakeup_service.py:494-504` vs `models.py:119-120` — F-7.

## 7. Fix Design

**C1, F-1: dict-guard every trigger sub-object in `WakeupConfig.from_dict`, and fail closed in
`_validate_wakeup_config` when `triggers` is present but not a dict.**

- `models.py:211-214`: guard `triggers_raw`, `enm`, `sm`, `co` the same way `soft_raw` already is —
  `triggers_raw = raw.get("triggers"); triggers_raw = triggers_raw if isinstance(triggers_raw, dict)
  else {}`, and likewise for `enm`/`sm`/`co` derived from it. This is the domain-parser side: tolerate
  already-stored bad data by falling back to defaults, matching `_tolerant_int`'s existing contract.
- `agents.py:100-106`: when `triggers` is present (`"triggers" in value`) and not a dict, raise via a
  new small helper (mirroring `_check_wakeup_int`'s shape) naming the field, instead of silently
  skipping the block. This is the API-boundary side: reject new bad writes with a 422.

**C2, F-2: add a `_tolerant_bool` (domain) / a bool-type check (API boundary) pair, mirroring
`_tolerant_int`/`_check_wakeup_int`.**

- `models.py`: add `_tolerant_bool(value: Any) -> bool` next to `_tolerant_int`
  (`is_plain_int`'s sibling check is `isinstance(value, bool)` directly — no `TypeGuard` helper needed
  from `shared_kernel.type_guards` since `bool` has no int-vs-bool ambiguity to resolve). Replace the
  four `bool(x.get("enabled"/"allow_self_open", False))` call sites with
  `_tolerant_bool(x.get(..., False))`.
- `agents.py`: add a `_check_wakeup_bool` parallel to `_check_wakeup_int`, and call it for
  `triggers.*.enabled` and root-level `allow_self_open` wherever each container is confirmed to be a
  dict (reusing C1's guards).

**C3, F-3: extend C1's validator fix to `soft_bounds` itself (the container, not just its leaves).**

- `agents.py:131-132`: when `soft_bounds` is present and not a dict, raise (same helper as C1). The
  leaf-level `_check_wakeup_int` calls at `:133-145` are unaffected — they already fire correctly once
  the container is confirmed to be a dict.
- No domain-parser change needed beyond what already exists (`models.py:227-229` already resolves a
  non-dict `soft_bounds` to `None` safely) — C1's parser-side guard pattern was already applied here by
  the original dossier; only the validator-side reject was missing.

**C4, F-4: add a regression test; no code change.**

- `backend/tests/unit/test_agent_service_wakeup.py` (or the existing agent-service PATCH test file —
  confirm the right home during implementation): PATCH an agent with
  `workflow_capabilities: {"max_alive_subagents": null}` after it was previously set, assert the key is
  absent from the stored JSON afterward (not present-as-`null`). Pins Q-1's decision so a future change
  to `merge_json_config` cannot silently re-introduce ambiguity here.

**C5, F-5: remove the dead fallback; document the behavior change.**

- `agent_service.py:850`: `values["wakeup_authored_snapshot"] = merged_snapshot or None` →
  `values["wakeup_authored_snapshot"] = merged_snapshot` (the `or None` cannot trigger; see Claim B's
  verification).
- Add a one-line note to `_merge_and_normalize_wakeup`'s or `_normalize_wakeup_config`'s docstring
  recording that an empty-object patch is a no-op under additive merge, so a future reader does not
  reintroduce the crash-on-non-dict-recovery version of this same expectation.

**C6, F-6: reuse `values["wakeup_config"]` instead of recomputing when there is no prior snapshot.**

- `agent_service.py:838-850`: when `current.wakeup_authored_snapshot is None`, set
  `merged_snapshot = values["wakeup_config"]` directly (byte-identical result, per Claim C's
  verification — same base, same patch, same replace flag); only call `_merge_and_normalize_wakeup`
  again for the `current.wakeup_authored_snapshot is not None` branch, where the base genuinely
  differs.

**C7, F-7: export `_clamp` and call it from `_clamp_n`/`_clamp_t`, or accept the duplication —
implementer's call at build time, recorded here as low-stakes either way.**

- Preferred: rename `_clamp` to `clamp` (or add it to `models.py`'s `__all__` under its existing name)
  and import it into `wakeup_service.py` alongside the constants already imported from that module;
  replace both `_clamp_n`/`_clamp_t` bodies' final line with `return clamp(value, lo, hi)`.
- Alternative if the implementer judges the private-helper-promotion not worth it for one call site:
  leave as is and close FU-only — record the decision either way in the Deviation Log.

**Why these correct rather than mask.** F-1/F-2/F-3's shortcut would be a bare `except Exception` around
the whole `from_dict`/`patch` call, which would hide the crash but silently discard the caller's PATCH
(or worse, half-apply it) with no error surfaced — the actual defect is a missing type check at the one
correct layer (validate at the boundary, tolerate in the parser), and this design closes exactly that
gap using the pattern the file already established for numeric fields, rather than adding a new
suppression layer. F-6's shortcut would be to skip the snapshot update in this case entirely, which
would silently stop tracking the authored baseline for agents without one yet, rather than removing
only the redundant computation.

**Data repair position.** F-3's already-corrupted `soft_bounds` values (any agent already carrying a
non-dict `soft_bounds` before this fix ships) are not retroactively repaired by this dossier — the
fix stops new corruption and stops the crash class, but does not scan/backfill existing rows. If any
such rows exist in production, that is a separate, explicit data-repair decision the user should make
once this fix is confirmed to have not yet been bypassed by other means; recorded as FU-1.

## 8. Regression Test Plan

**T-1 — `backend/tests/unit/test_wakeup_config_domain.py::test_from_dict_tolerates_a_non_dict_triggers`**
(or the existing domain-model test file for `WakeupConfig` — confirm at build time)
`WakeupConfig.from_dict({"triggers": "x"})` must not raise, and must return the same defaults as
`WakeupConfig.from_dict({})`. *Fails today*: `models.py:212` raises `AttributeError` on `"x".get(...)`.

**T-2 — `test_agents_api.py::test_patch_wakeup_config_rejects_non_dict_triggers`** (confirm exact file —
likely alongside existing `_validate_wakeup_config` tests)
`PATCH` with `wakeup_config: {"triggers": "x"}` → 422 naming `wakeup_config.triggers`. *Fails today*:
`agents.py:101`'s `isinstance` guard skips validation, Pydantic accepts, the request 500s downstream
instead of 422ing at the boundary.

**T-3 — `test_wakeup_config_domain.py::test_from_dict_tolerates_a_non_bool_enabled`**
`WakeupConfig.from_dict({"triggers": {"call_only": {"enabled": "false"}}})`. Assert
`.triggers.call_only.enabled is False` (or whatever `_tolerant_bool`'s documented default is — decide
during implementation whether a wrong-typed truthy value defaults to `False` for safety, matching the
"never silently enable" intent). *Fails today*: resolves to `True`.

**T-4 — `test_agents_api.py::test_patch_wakeup_config_rejects_non_bool_enabled`**
`PATCH` with `wakeup_config: {"triggers": {"call_only": {"enabled": "false"}}}` → 422. *Fails today*:
200, and the stored value silently inverts.

**T-5 — `test_agents_api.py::test_patch_wakeup_config_rejects_non_dict_soft_bounds`**
`PATCH` with `wakeup_config: {"soft_bounds": "x"}` → 422. *Fails today*: 200, and the value persists
forever per F-3's reproduction.

**T-6 — `test_agent_service_wakeup.py::test_workflow_capabilities_null_deletes_the_key`** (Q-1/C4)
PATCH `workflow_capabilities: {"max_alive_subagents": null}` onto an agent that already has that key
set; assert the key is absent afterward. *Passes today* (this pins existing, confirmed-intended
behavior — not a failing test, a guard against a future accidental semantic change).

**T-7 — `test_agent_service_wakeup.py::test_wakeup_config_empty_patch_is_a_no_op`** (Q-2/C5)
PATCH `wakeup_config: {}` onto an agent with a non-default config; assert the stored config is
unchanged. *Passes today* (guard, documents Q-2's accepted behavior explicitly rather than leaving it
implicit).

**T-8 — `test_agent_service_wakeup.py::test_patch_reuses_the_wakeup_config_merge_for_a_first_snapshot`**
(C6) PATCH `wakeup_config` on an agent with `wakeup_authored_snapshot is None`; assert
`wakeup_authored_snapshot == wakeup_config` in the result (unchanged observable behavior) and,
via a call-count spy on `_merge_and_normalize_wakeup` (or `merge_json_config`), assert the merge chain
runs once for this PATCH, not twice. *Fails today* on the call-count assertion only — the observable
config values are already correct today; this test exists to pin the performance fix, not to catch a
correctness bug.

**T-9 — `test_wakeup_service.py::test_clamp_n_and_clamp_t_use_the_shared_clamp`** (C7, only if the
`clamp` export path is chosen) Assert `WakeupService._clamp_n`/`_clamp_t` and `models.clamp` agree on a
representative in/out-of-range value set. *Passes today* trivially if left as duplication (N/A if C7's
alternative — accept the duplication — is chosen instead).

## 9. Risks and Rollback

- **C1/C2/C3 tighten the API boundary.** Any existing client sending a non-dict `triggers`/`soft_bounds`
  or a non-bool `enabled` today (silently accepted) starts receiving 422s. This is the intended
  correction, but it is a breaking-for-malformed-callers change — worth confirming no internal caller
  (migration scripts, admin tooling) currently relies on the lenient behavior before shipping.
- **C6 changes which computation path produces `wakeup_authored_snapshot`** for the no-prior-snapshot
  case, from "recompute independently" to "reuse the sibling result". Verified byte-identical by
  construction (same base/patch/replace args), so no behavior change is expected; T-8 pins this.
- **C7, if the export path is chosen, expands `models.py`'s public surface** by one name. Low risk —
  `clamp` is a pure, three-argument function with no side effects.
- **Rollback.** Each C-n is independent and touches disjoint lines within shared files; any subset can
  be reverted without affecting the others. No migration, no schema change, no data repair performed
  (see §7's data repair position) — reverting restores prior behavior exactly, including F-3's
  already-open corruption path.

## 10. Acceptance Criteria

- [x] **AC-1**: T-1 fails before the fix, passes after — `WakeupConfig.from_dict` tolerates a non-dict
      `triggers` (and, by the same guard shape, non-dict `every_n_messages`/`silence_minutes`/`call_only`).
- [x] **AC-2**: T-2 passes — a non-dict `triggers` in a PATCH is rejected with 422 naming the field,
      not a 500.
- [x] **AC-3**: T-3 fails before the fix, passes after — a non-bool `enabled` value resolves to a safe
      default rather than `True` via `bool(...)`'s truthiness coercion.
- [x] **AC-4**: T-4 passes — a non-bool `enabled` in a PATCH is rejected with 422.
- [x] **AC-5**: T-5 passes — a non-dict `soft_bounds` in a PATCH is rejected with 422, and (per F-3's
      root cause) can therefore no longer persist indefinitely via the additive merge.
- [x] **AC-6**: T-6 passes — `workflow_capabilities`'s null-deletes-key semantic is pinned by a test
      (Q-1); no code change for this AC.
- [x] **AC-7**: T-7 passes — `wakeup_config: {}` remains a documented no-op (Q-2); no reset mechanism
      added.
- [x] **AC-8**: `agent_service.py:850`'s `merged_snapshot or None` is removed (`merged_snapshot` alone).
- [x] **AC-9**: T-8 passes — the snapshot-branch merge is not recomputed when it would be byte-identical
      to the already-computed `values["wakeup_config"]`.
- [x] **AC-10**: `_clamp_n`/`_clamp_t` call the shared `clamp` (T-9 equivalent coverage: the existing
      `test_soft_bounds_tolerate_wrong_typed_values` already exercises both through their full range and
      passed unchanged after the reuse, confirmed byte-identical behavior).
- [x] **AC-11**: No data-repair script, migration, or backfill is introduced (§7's data repair position).
- [x] **AC-12**: `pytest tests/unit -q` (6068 passed, 6 skipped for pre-existing unrelated environment
      reasons — same note as both prior dossiers in this area), `ruff check . && ruff format --check .`,
      `mypy .` all pass in `backend/`. No frontend change was needed (F-4 was test-only); `pnpm` gates
      N/A.

## 11. SRS Delta

None. R15.07/R15.08/R15.09 are correct as written; this dossier closes gaps between documented intent
and the code's actual type-tolerance, it does not change what the SRS requires. R15.18 is unaffected —
F-4's decision (Q-1) confirms existing `workflow_capabilities` behavior rather than changing it.

## 12. Deviation Log

**D-1.** During /build's own sibling sweep (Step 4's mandatory grep beyond §6's citations), a
repo-wide search for `bool(.*\.get(` over `backend/contexts/` (not just `backend/contexts/*/domain/`,
which is all §6's sweep covered) found a fourth F-2 sibling the spec did not name:
`contexts/agents/application/a2a_scope.py:69`'s `is_call_only_enabled`, which parses
`wakeup_config.triggers.call_only.enabled` via the identical `bool(call_only.get("enabled", False))`
pattern — reachable as the R9.17 agent-to-agent call-only bypass check, where a wrong-typed `enabled`
silently granted a call that should have been denied (a fail-open AuthZ-adjacent bug, not merely a
config-read bug). Fixed with the same `isinstance(enabled, bool) else False` guard and a regression
test (`test_a2a_scope.py::test_is_call_only_enabled_rejects_a_non_bool_enabled_value`); confirmed via
the security audit gate that the fix is strictly more restrictive than before and cannot introduce a
new bypass. This is an addition beyond the spec's named scope, not a contradiction of it — the spec's
own FU-2 anticipated the sweep might not be exhaustive.

**D-2.** AC-10's regression coverage differs from §8's T-9 as originally described: rather than adding
a new test asserting `_clamp_n`/`_clamp_t` and `clamp` "agree" on a value set, the build reused
`test_wakeup_service.py::test_soft_bounds_tolerate_wrong_typed_values` (already exercises both methods
across their full in/out-of-range/inverted-bounds behavior) as the regression guard, since it already
passed unchanged after C7's reuse landed — a new comparison test would have been redundant with what
that test already pins.

**D-3.** AC-12 ran `pytest tests/unit -q` rather than the bare `pytest -q` named in the spec, for the
same environment reason recorded in both prior dossiers in this area (`pyproject.toml`'s
`testpaths = ["tests"]` reaches `tests/integration`/`tests/wiring`, which need a live
Postgres/Redis/Vault stack not present in this build environment).

## 13. Follow-ups

- **FU-1** — F-3's already-corrupted `soft_bounds` values (if any exist in production, written before
  this fix ships) are not backfilled by this dossier (§7's data repair position). Decide whether a
  one-off audit query against production is warranted once this fix lands.
- **FU-2** — RESOLVED by D-1: the sibling sweep was extended during /build to cover
  `backend/contexts/agents/application/` as well, and found + fixed `a2a_scope.py`'s
  `is_call_only_enabled`. `workflow_capabilities`'s three booleans still use a different accessor
  entirely (confirmed: no `.get(...)`-style read site found for them anywhere in `backend/contexts/`)
  and remain unchased — worth a dedicated look only if that field grows a JSONB-parsing accessor of
  its own.
- **FU-3** — This dossier's C1-C3 close the *known* gaps in wake-up config type-tolerance; a systemic
  fix (e.g., replacing the hand-written per-field validation in `agents.py`/`models.py` with a single
  Pydantic model shared by both the API boundary and the domain parser) would prevent the next such gap
  by construction rather than by review. Out of scope here as a much larger refactor; noted for anyone
  revisiting this area a third time.
- **FU-4** — The security audit gate flagged `contexts/agents/application/runtime/tool_registry.py:290`
  (`vote=bool(args.get("vote"))`, an LLM tool-call argument for an approval-vote mechanism) as the same
  truthiness-coercion pattern on a different trust boundary (LLM-generated tool-call JSON, not a stored
  JSONB config column). Not confirmed exploitable within this dossier's scope — whether the tool's own
  JSON schema already constrains `vote` to a boolean before this parses it was not established. Worth a
  dedicated look from whoever next touches that file.
