---
type: feature
status: draft
created: 2026-07-23
requirements: [R30.05, R30.17, R30.19]
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
| Q-1 | Which first-party validators ship in v1? | **To decide.** At minimum one concrete example (e.g. an exact-match / expected-answer scorer) to prove the path; possibly a small starter set. | The framework is useless without ≥1 registered validator; the set defines the feature's user value. |
| Q-2 | Where does the registration site live and how is it invoked? | **To decide.** A module under `app/plugins/` imported from the app bootstrap (so registration is a startup side effect), vs. an explicit registry call in `create_app`. | Determines discoverability and test isolation; `registry.py:3-5` suggests `app/plugins/`. |
| Q-3 | Is the validator list global or project-scoped? | **To decide.** In-process validators are process-global (first-party), so a global `GET /api/activity-validators` is simplest; a project-scoped path is only needed if availability ever varies per project. | Global matches the registry's process-global nature. |

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

## 6. Detailed Changes

**Backend**
- `registry.py`: add `list_registered()` (+ optional per-validator title metadata).
- `app/plugins/activity_validators.py` (new): register the v1 validator(s); import it from the
  app bootstrap so registration happens on startup.
- New read route `GET /api/activity-validators` (membership or authenticated) returning the
  list.
- No migration.

**API contract** — new GET; `gen:api` rerun: yes.

**Frontend**
- `types/schemas.ts`: add `in_process` to `VALIDATOR_KINDS` and an `in_process` sub-form
  (`validator_id`), folded into `validator_config` by `assembleValidatorConfig`.
- `ActivityTypeForm.vue`: `in_process` branch — a validator `SSelect` from a new
  `listActivityValidators()` query.
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
input. The list endpoint exposes only ids/titles, no code.

## 9. Quality Notes
- Reuse the `registry` as the single source; the endpoint must not maintain a parallel list.
- Follow the `webhook`/`mcp` sub-form pattern already in `ActivityTypeForm`.

## 10. Risks and Rollback
- Startup import ordering: the registration module must be imported before any type
  registration is served. Additive; removing the plugin module + route rolls back.

## 11. Acceptance Criteria
- [ ] AC-1: At least one first-party validator is registered at startup and
  `GET /api/activity-validators` lists it.
- [ ] AC-2: An owner creates an `in_process` type selecting a registered validator; it
  registers (no 422) and scores a submission end-to-end.
- [ ] AC-3: The form offers `in_process` only when ≥1 validator is registered; the picker
  lists the registered ids.
- [ ] AC-4: new strings resolve en + zh-TW; lint passes.

## 12. Test Plan
- Backend unit: `list_registered` returns registered validators; an `in_process` type with a
  registered id passes config validation; end-to-end scoring via `run_in_process_scorer`.
- Backend wiring: the startup site registers the v1 validator(s).
- Frontend component: the `in_process` sub-form renders the picker and assembles
  `validator_config`.

## 13. SRS Delta

Amend `[R30.24]` (which currently states the surface does not offer `in_process` while none
are registered) to reflect that first-party `in_process` validators are now registered and
offered. Draft exact wording at approval once Q-1 fixes the shipped set.

## 14. Open Questions
- OQ-1: Do any first-party validators need project-owned config (beyond `validator_id`)? If so
  the `in_process` sub-form grows per-validator fields — out of scope for v1 unless required.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

To be discovered during build.
