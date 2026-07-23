---
type: feature
status: approved
created: 2026-07-23
requirements: [R30.05, R30.17, R30.19, R30.24]
depends_on: []
---

# First-party in-process activity validators

## 1. Summary

The platform ships zero in-process validators: `registry.py` has `register_in_process_validator`
/ `is_registered` / `run_in_process_scorer` but no listing accessor, and there is no startup
site that registers any first-party validator (`registry.py:1-15,27`). Consequently an
`ActivityType` authored with `validator_kind=in_process` cannot be created — `type_service`
rejects it because `is_registered(validator_id)` is always false (`type_service.py:107-110`)
— which is exactly why the authoring UI omits `in_process` (authoring dossier §2, FU-1). This
feature adds (a) a startup registration site outside the activities context, (b) at least one
first-party validator, (c) a listing accessor + endpoint so the form can offer `in_process`,
and (d) the `in_process` branch in the create form. Follows up FU-1 of
`docs/tasks/2026-07-23-activities-type-authoring-ui/`.

## 2. Goals and Non-goals

**Goals**
- A registration site (a module under `app/plugins/` or equivalent, imported at app startup)
  that registers first-party in-process validators, keeping the activities context
  domain-free (`registry.py:3-5` describes this intended shape).
- A listing accessor on the registry (`list_registered() -> list[ValidatorInfo]`) and a
  read endpoint exposing the available validator ids + display metadata.
- The `in_process` branch in `ActivityTypeForm.vue`: a validator picker populated from the
  listing; `in_process` becomes a selectable `validator_kind`.

**Non-goals**
- A plugin system for third-party/untrusted validators (those use the MCP sandbox;
  `registry.py:12-14`). First-party, in-process, backend-trust only.
- Changing the scoring dispatch (`SubmissionService` already runs in_process synchronously).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which first-party validators ship in v1? | **`exact_match` only.** A scorer that compares a chosen payload field to an expected answer held in the type's `validator_config`. | Delivers real scoring (aligns with the no-MVP/production stance), and is the spec's own named example. Consequence: it needs per-type config (`field` + `expected`), so OQ-1's per-validator config is **in-scope for v1** — the `in_process` sub-form grows beyond a bare `validator_id` picker, and the registry gains a per-validator config-validation hook so registration/edit rejects a malformed `exact_match` config rather than deferring the failure to submit time. |
| Q-2 | Where does the registration site live and how is it invoked? | **`app/plugins/activity_validators.py`, imported by a new `startup.py` `INITIALIZERS` step.** Registration happens as an import side effect; the step makes it ordered and testable. | Matches `registry.py:3-5`; keeps `contexts/activities` domain-free; `clear_registry()` + re-running the step isolates tests. `app/plugins/` is the discoverable home for future first-party validators. |
| Q-3 | Is the validator list global or project-scoped? | **Global `GET /api/activity-validators`, authenticated (any logged-in user).** | In-process validators are process-global first-party code; availability never varies per project, so a project-scoped path and membership check would be dead ceremony. |

## 4. Current State

- `registry.py`: `register_in_process_validator(id, fn)`, `is_registered(id)`,
  `run_in_process_scorer(...)`, `clear_registry()` — **no list accessor** (`registry.py:30-61`).
- `type_service._validate_validator_config` rejects an `in_process` config whose
  `validator_id` is not registered (`type_service.py:107-110`).
- `SubmissionService` scores `in_process` types synchronously at submit (per the authoring
  dossier §4 and `test_activities_services.py::TestSubmitInProcess`); the async worker path is
  only for webhook/mcp (`app/workers/tasks/activities.py:81-83`).
- The authoring form (`types/schemas.ts` `VALIDATOR_KINDS`) offers only `webhook`/`mcp`.

## 5. Design

### Options considered
- **Option A (chosen shape)** — `app/plugins/activity_validators.py` registers first-party
  scorers at import; the app bootstrap imports it. Add `registry.list_registered()` returning
  `(validator_id, title)` pairs, and `GET /api/activity-validators` returning them. Form adds
  an `in_process` branch with a validator `SSelect`.
- **Option B** — configuration-file-driven validator registration. Rejected: validators are
  code (they run with a DB session), so a code registration site is the honest model.

### Decision
Option A, finalized with Q-1..Q-3 at approval. The registry stays the single source of truth;
the endpoint is a thin read over it.

**v1 validator — `exact_match`.** `validator_config` shape:
`{validator_id: "exact_match", field: <payload key>, expected: <scalar>, case_sensitive: <bool>}`.
The scorer reads `activity_type.validator_config`, compares `payload[field]` to `expected`
(string comparison honours `case_sensitive`, default false; the payload is already
schema-valid at dispatch), and returns `ValidationResult(is_valid=..., error_class="mismatch"
when wrong)`. It needs no DB session but keeps the standard `(payload, activity_type, *, db)`
signature (`registry.py:25`).

**Per-validator config validation.** Because `exact_match` requires `field`/`expected`,
`register_in_process_validator` gains optional `title` and `config_validator` metadata, and
`type_service._validate_validator_config`'s `IN_PROCESS` branch — after confirming the id is
registered — calls the registered `config_validator` to reject a malformed `exact_match` config
at registration/edit time (`type_service.py:174-177`) instead of letting it surface as a
per-submission `error` verdict. The registry stays the single source: `list_registered()`
returns `(validator_id, title)` and the config rules live beside the scorer, not in a parallel list.

## 6. Detailed Changes

**Backend**
- `registry.py`: extend `register_in_process_validator(id, fn, *, title, config_validator=None)`;
  add `list_registered() -> list[(validator_id, title)]`; keep `run_in_process_scorer`/
  `is_registered`/`clear_registry` unchanged (`registry.py:30-61`).
- `app/plugins/activity_validators.py` (new): the `exact_match` scorer + its `config_validator`,
  registered at import. Lives outside `contexts/activities` to keep the context domain-free.
- `app/bootstrap/startup.py`: new `register_activity_validators_step` appended to `INITIALIZERS`
  (`startup.py:63-69`) that imports the plugin module so registration is an ordered startup step.
- `type_service._validate_validator_config`: in the `IN_PROCESS` branch, after `is_registered`,
  invoke the validator's `config_validator` to reject malformed config (`type_service.py:174-177`).
  This tightens both `register` and `update` (both call this method).
- New read route `GET /api/activity-validators` (authenticated) returning `[{id, title}]`, a thin
  read over `list_registered()`.
- No migration.

**API contract** — new GET; `gen:api` rerun: yes.

**Frontend**
- `types/schemas.ts`: add `in_process` to `VALIDATOR_KINDS`; add the `in_process` sub-form fields
  (`in_process_validator_id`, `exact_match_field`, `exact_match_expected`,
  `exact_match_case_sensitive`) with conditional `superRefine` (mirrors the webhook/mcp pattern,
  `schemas.ts:53-65`); `assembleValidatorConfig` folds them into `validator_config`, coercing
  `expected` to the selected field's schema type.
- `ActivityTypeForm.vue`: `in_process` branch — a validator `SSelect` from `listActivityValidators()`;
  for `exact_match`, a `field` picker sourced from the payload schema's property names and an
  `expected` input typed by the chosen field, plus a case-sensitivity toggle.
- `api/index.ts`: `listActivityValidators()`.
- i18n en + zh-TW.

## 7. NFR Checklist
- [ ] i18n — new strings both locales.
- [ ] Audit log — none new (registration is startup; creation already audits).
- [ ] Tenant isolation — the list is first-party/global; the read requires auth.
- [ ] Error handling UX — form validator picker loading/empty states.
- [ ] Performance — the list is a tiny in-memory read.

## 8. Security Considerations

In-process validators are first-party backend code with a live DB session (`registry.py:12-14`).
The registration site must only register shipped validators — never anything derived from user
input. The list endpoint exposes only ids/titles, no code. `exact_match`'s `expected`/`field` are
project-owned config, not code: the scorer only reads a named payload key and compares values, so
a malicious `expected` cannot escalate beyond producing a verdict. The `config_validator` hook
rejects malformed config at the API boundary (registration/edit), consistent with the existing
`mcp` UUID guard (`type_service.py:184-195`).

## 9. Quality Notes
- Reuse the `registry` as the single source; the endpoint must not maintain a parallel list.
- Follow the `webhook`/`mcp` sub-form pattern already in `ActivityTypeForm`.

## 10. Risks and Rollback
- Startup import ordering: the registration module must be imported before any type
  registration is served. Additive; removing the plugin module + route rolls back.

## 11. Acceptance Criteria
- [ ] AC-1: `exact_match` is registered at startup (via the `INITIALIZERS` step) and
  `GET /api/activity-validators` returns it as `{id: "exact_match", title: ...}` for an
  authenticated caller.
- [ ] AC-2: An owner creates an `in_process`/`exact_match` type with a valid `field`/`expected`;
  it registers (no 422). A submission whose `field` equals `expected` scores `is_valid=true`; a
  mismatching submission scores `is_valid=false, error_class="mismatch"` — end-to-end via the
  synchronous submit path.
- [ ] AC-3: Registering (or editing) an `exact_match` type with a missing/empty `field` or absent
  `expected` is rejected at the API with `ValidatorConfigInvalid` (422), not deferred to submit.
- [ ] AC-4: The form offers `in_process` only when ≥1 validator is registered; the picker lists
  the registered ids, and selecting `exact_match` reveals the `field`/`expected`/case-sensitivity
  sub-form.
- [ ] AC-5: new strings resolve en + zh-TW; `pnpm lint` and `ruff check` pass.

## 12. Test Plan
- Backend unit: `list_registered` returns `(id, title)` for `exact_match`; `exact_match` scorer
  returns valid/`mismatch` verdicts (incl. `case_sensitive` on/off); `_validate_validator_config`
  accepts a well-formed `exact_match` config and raises `ValidatorConfigInvalid` on missing
  `field`/`expected`.
- Backend wiring: the `INITIALIZERS` step registers `exact_match` (assert `is_registered`
  after running the step); the route lists it; end-to-end scoring via the submit path.
- Frontend component: the `in_process` branch renders the validator picker; selecting
  `exact_match` reveals `field`/`expected`; `assembleValidatorConfig` folds the fields and coerces
  `expected` to the field's schema type; `superRefine` blocks submit on empty `field`/`expected`.

## 13. SRS Delta

Amend `[R30.05]` — drop the stale "the platform ships no domain validators" clause:

> **[R30.05]** A validator has one of three kinds: `in_process` (synchronous, a registered pure
> scoring function; first-party in-process validators are registered at app startup from a code
> site outside the `activities` context, keeping the context domain-free), `mcp`, or `webhook`
> (both asynchronous via a worker job that writes the result back). The `activities` context never
> imports the `agents` context; MCP/webhook composition is performed in the worker layer through
> the agents facade only.

Amend `[R30.24]` — the surface now offers `in_process`, backed by a registered first-party set:

> **[R30.24]** The authoring surface supports the `webhook`, `mcp`, and `in_process` validator
> kinds. The platform registers first-party in-process validators at startup from a code
> registration site outside the activities context; the surface offers `in_process` only while at
> least one such validator is registered, and an authenticated read endpoint
> (`GET /api/activity-validators`) lists the registered validator ids and their display titles as
> the single source the picker draws from. A `webhook` validator's URL is stored for proxy-only
> egress ([R30.07]); an `mcp` validator's `agent_id`/`binding_id` must reference agents/bindings
> within the same project; an `in_process` validator's config must name a registered
> `validator_id` plus that validator's required parameters (e.g. `exact_match` requires the
> payload `field` to compare and the `expected` value), validated at registration and edit time
> ([R30.02], [R30.23]).

## 14. Open Questions
- OQ-1: **Resolved (in-scope).** `exact_match` requires per-type config (`field`/`expected`), so
  the `in_process` sub-form carries per-validator fields and the registry validates them at
  registration/edit time (see §5 Decision and Q-1). Any future validator needing config follows
  the same `config_validator`-hook pattern.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

To be discovered during build.
