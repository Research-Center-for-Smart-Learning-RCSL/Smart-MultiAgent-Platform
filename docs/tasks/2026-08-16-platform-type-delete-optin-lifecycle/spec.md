---
type: bugfix
status: implemented
created: 2026-08-16
requirements: [R30.32, R30.33]
depends_on: []
---

# Deleting a platform activity type orphans every project's opt-in row, and two docstrings claim a cascade that never fires

## 1. Summary

Deleting a platform-scoped activity type is a **soft** delete, so the `ON DELETE CASCADE` on
`project_activity_type_optins.activity_type_id` never fires and every opted-in project's
authorization row is left pointing at a tombstone forever. Two docstrings state the opposite,
migration 0076 even builds an index whose comment says it "drives the admin delete cascade" -
an index for code that does not exist, and the strongest evidence the cascade was intended.

The rows are permanently inert, so there is no authorization or data-exposure impact. The real
costs are two false docstrings, unreclaimable accumulation, and a documented upgrade procedure
that silently revokes every project's opt-in without saying so - which matters because that
procedure is the only way to get the corrected worksheets into an already-installed deployment.

F-9 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`, whose original
claim of an authorization impact was refuted; what survived is recorded here.

## 2. Observed vs Expected

**Observed.**

- The delete is an UPDATE:
  `backend/contexts/activities/infrastructure/repositories/type_repo.py:300-313` is
  `UPDATE activity_types SET deleted_at = now() WHERE id = :id AND deleted_at IS NULL`, never a
  row `DELETE`.
- Reached from `backend/contexts/activities/interfaces/facade.py:470-496`
  (`delete_platform_type`) via `_cascade_delete` (`:498-527`, `soft_delete` at `:524-526`) and
  `backend/contexts/activities/application/type_service.py:286-313`.
- The FK is `ON DELETE CASCADE`
  (`backend/alembic/versions/0076_platform_activity_types.py:84`), which only a real `DELETE`
  triggers.
- Nothing in the tree ever hard-deletes `activity_types`. The retention purge table list
  (`backend/app/workers/tasks/retention.py:60-66`) is `(orgs, projects, agents, workflows,
  chatrooms)`; `activity_types` is absent. Platform rows have `project_id IS NULL`, so the
  project cascade cannot reach them either.
- **The two false docstrings**: `backend/contexts/activities/interfaces/facade.py:487-488`
  ("The opt-in rows disappear with the type through the FK cascade, so no project is left
  holding an authorization for a row that no longer exists") and
  `backend/app/api/v1/admin_activities.py:434-435` ("the opt-in rows go with it through the FK
  cascade").
- `backend/alembic/versions/0076_platform_activity_types.py:103-107` creates
  `ix_project_activity_type_optins_type` on `(activity_type_id)`, with a comment saying it
  drives the admin delete cascade. It is currently unused by any query.
- Re-installing mints a **new id**: the partial unique is
  `ON activity_types (key) WHERE project_id IS NULL AND deleted_at IS NULL` (`0076:68-70`), so
  a tombstone does not block a fresh insert, and `install_course` has no resurrection branch
  (`backend/contexts/activities/application/example_service.py:204-218`).
- The documented upgrade path says nothing about it:
  `docs/examples/creative-thinking-course.md:252-254` warns only that activations end.

**Expected.** Deleting a platform type removes the authorization rows it granted, and the code
and documentation say what actually happens - including that a re-install does not restore any
project's access.

**Intent source.** The two docstrings, and [R30.33], which makes the opt-in "the authorization
record". An authorization record for a type that no longer exists is not a state the design
contemplates; the migration's index comment shows the original author expected it to be
impossible.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Delete the orphan rows, or preserve them so a future undelete could restore access? | **Delete them.** | Not a user question - there is no undelete path to preserve them for. A grep across `contexts/activities/` for `undelete`, `restore`, or clearing `deleted_at` returns nothing; `type_repo.py` ends at `soft_delete` with no inverse; `admin_activities.py` exposes GET/PATCH/DELETE only; and the retention purge never hard-deletes `activity_types`, so the cascade is unreachable by every path. The rows can never become effective and can never be reclaimed - they are pure accumulation. What is worth preserving is the *knowledge* that the projects had opted in, and that belongs in the audit trail (Q-2), not in a dead authorization row. |
| Q-2 | Should the cleanup emit a per-project `activity_type.opted_out` event? | **No.** Record the blast radius as metadata on the existing `activity_type.deleted` event instead. | Not a user question, and the audit's refuter was right. [R30.33]'s last sentence binds two named *operations* ("Opt-in and opt-out emit audit events"), both performed by a Project Owner through the opt-in routes. An admin delete is a third operation with a different actor and authority; emitting N synthetic `opted_out` rows with the admin as `actor_user_id` would put a false statement in the audit log - no Project Owner opted out. The codebase already reasons this way: `AgentGroupService.add_member` declines to emit `member_added` for a membership that already existed (`backend/contexts/agent_groups/application/group_service.py:130-134`). The honest record is a count on the operation that actually happened, mirroring how `opt_out` already records `activations_ended` (`example_service.py:343`). |
| Q-3 | Where does the cleanup call go? | **`facade.delete_platform_type` only**, not the shared `_cascade_delete`. | Not a user question. A project-scoped type can hold no opt-in rows by construction - `opt_in` refuses anything that is not `scope is PLATFORM` (`example_service.py:255-257`) - so putting it in `_cascade_delete` (`facade.py:498-527`) would issue a pointless DELETE on every project-type delete. The facade already holds `self._optin_repo` (`facade.py:87`), so no wiring changes. |
| Q-4 | Does any compliance stance require per-project auditability of access lost to an admin delete? | **Assumed no**, recorded as an open question rather than built. | This is the one point in this dossier a reviewer might overturn. If the answer is yes, the honest artifact is a **new** action string (`activity_type.access_revoked_by_delete`), not a reused `opted_out`, and it would be its own small dossier. Not invented speculatively; see §14. |
| Q-5 | Does any unfinished dossier conflict? | **No `depends_on`, one file to coordinate.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches this area. `2026-08-16-example-docs-corrections` also edits `docs/examples/creative-thinking-course.md`, but different sections (the `filled_count` boolean rule and the Limitations list, versus the upgrade note here). Different regions; rebase rather than sequence. |

## 4. Reproduction

**Preconditions.** A platform admin has installed `creative-thinking`, producing platform type
T1. Projects P1 and P2 have each opted into T1.

**Steps.**

1. `DELETE /api/admin/activity-types/{T1}`.
2. Inspect the join table: `SELECT project_id FROM project_activity_type_optins WHERE
   activity_type_id = '{T1}'`.
3. Re-install the course: `POST /api/admin/activity-examples/creative-thinking/install`.

**Actual.** Step 2 returns both P1 and P2 - the rows survive the delete. Step 3 creates T2 with
a **new id**, so neither project holds an opt-in for it. Both projects' facilitators get a bare
404 from activation, session and submission; `GET /projects/P1/activity-examples` shows the
example as not enabled, with no explanation of why it changed. The orphan rows for T1 remain
forever and cannot even be removed through `opt_out`, which 404s because the type read filters
tombstones (`backend/contexts/activities/application/reachability.py:44-46`).

**Expected.** Step 2 returns nothing. Step 3's outcome (both projects must re-enable) is
unchanged - that part is correct behaviour - but it is stated in the upgrade note rather than
discovered.

## 5. Root Cause Analysis

1. **Root cause.** `facade.delete_platform_type` relies on a database cascade that its own
   delete strategy makes unreachable. The type delete is soft (`type_repo.py:300-313`), and
   `ON DELETE CASCADE` (`0076:84`) fires only on a row `DELETE`. Adding an explicit removal in
   `delete_platform_type` prevents the symptom.
2. Nothing else can compensate. The retention purge would fire the cascade if it ever
   hard-deleted the row, but `activity_types` is not in `_SOFT_DELETE_TABLES`
   (`backend/app/workers/tasks/retention.py:60-66`), so the tombstone is permanent.
3. **Why the impact is bounded, and why the audit's original claim was refuted.** Every read of
   `activity_types` filters `deleted_at IS NULL` (`type_repo.py:127`, `:144`, `:167`, `:207`,
   `:232`, `:253`), and `resolve_reachable_type` fetches the type **first** and 404s before the
   opt-in is consulted (`reachability.py:44-46`). With no undelete path (Q-1), an orphan row can
   never become effective. The 404 a facilitator sees after a delete is the intended, documented
   semantics of delete-for-everyone, not a consequence of this defect.
4. **Why it shipped.** The migration author expected the cascade to fire - `0076:103-107` builds
   an index specifically to make it fast. The soft-delete strategy predates 0076
   (`0049_activities.py`), so the two decisions were made in different changes and never
   reconciled. No test covers the interaction: the word "optin" does not appear in
   `backend/tests/unit/test_activity_type_delete.py`, and its facade double stubs `_types`,
   `_activation_repo`, `_sessions` and `_activation` but **not** `_optin_repo`, so the real
   repository is never exercised.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Bounded by (projects x deleted platform types). No authorization impact, no
data exposure, no availability impact (§5.3). The concrete costs are: rows that accumulate and
cannot be reclaimed; two docstrings that will mislead the next person to read them; and the
documented upgrade procedure at `docs/examples/creative-thinking-course.md:252-254`, which is
the only route to getting the corrected worksheets into an installed deployment and which
currently omits that every Project Owner must re-enable afterwards.

**Sibling suspects** - other places a soft delete is expected to trigger a cascade:

| Site | Verdict |
|---|---|
| `agent_group_members` on agent-group delete | **confirmed present, and knowingly tolerated.** `backend/tests/wiring/test_agent_group_repository.py:144` explicitly asserts a membership row "is still physically present (no cascade cleanup)". Out of scope here - it is a different context with an existing recorded decision, not a defect this dossier introduces or should silently change. Recorded as FU-2. |
| `project_activity_type_optins` on **project** delete | **cleared** - projects *are* in `_SOFT_DELETE_TABLES` (`retention.py:60-66`), so the retention purge eventually hard-deletes the project row and the FK's `ON DELETE CASCADE` on `project_id` (`0076:81-83`) does fire. |
| `activity_activations` / `activity_sessions` on type delete | **cleared** - handled explicitly by `_cascade_delete` (`facade.py:498-527`), which ends activations and calls `close_open_for_type`. They never relied on the FK. |
| `opt_out`'s own cleanup path | **cleared** - `example_service.py:305-307` calls `optin_repo.remove` directly and does not rely on any cascade. |

**Systemic reading.** The tree contains exactly one other instance of the pattern
(`agent_group_members`), and that one is deliberate and tested. This is therefore a single-site
defect, not a sweep - but the fact that both instances exist suggests a rule worth writing down;
see FU-2.

## 7. Fix Design

**7.1 A delete-by-type on the opt-in repository.**
`backend/contexts/activities/infrastructure/repositories/optin_repo.py` today has exactly four
methods - `exists` (`:47-64`), `list_for_project` (`:66-73`), `add` (`:75-93`), `remove`
(`:95-105`) - and no delete-by-type and no list-by-type. Add
`remove_all_for_type(activity_type_id) -> Sequence[uuid.UUID]`, implemented as
`DELETE ... WHERE activity_type_id = :id RETURNING project_id`. Returning the project ids costs
nothing over a bare `DELETE` and is what makes Q-2's metadata possible.

It uses the index that already exists for it (`0076:103-107`), finally giving that index a
caller.

The module docstring (`:5-8`) states the repository is "deliberately narrow ... so there is no
query shape that could accidentally answer 'reachable' more permissively than `exists` does".
A delete does not widen a reachability answer, so the fifth method is consistent with that
intent - but the docstring must be extended to say so, or the next reader will think the rule
was forgotten.

**7.2 Call it from `delete_platform_type`.** In
`backend/contexts/activities/interfaces/facade.py:470-496`, before delegating to
`_cascade_delete`, remove the opt-ins and keep the returned project ids. Per Q-3, this does
**not** go in `_cascade_delete`, which both delete paths share.

**7.3 Record the blast radius on the delete event (Q-2).**
`type_service.soft_delete` (`type_service.py:286-313`) builds its audit metadata entirely
inside the method (`:306-310`). Give it an optional `extra_metadata: dict[str, str] | None =
None` parameter and merge it, so the facade can pass `{"optins_removed": str(len(removed))}`.
This mirrors `opt_out`, which already records `activations_ended` in its own metadata
(`example_service.py:343`). No new action string, no new mapping.

**7.4 Correct the two docstrings.** They must be replaced in the same change as the code, or
they become a *different* false statement.

- `facade.py:487-488` becomes: every project's opt-in row is removed explicitly; the FK's
  `ON DELETE CASCADE` is not what does it, because this is a soft delete and the cascade never
  fires; re-installing the course mints a new type id, so a project that had enabled the example
  must enable it again.
- `admin_activities.py:434-435` becomes: every active activation ends, every open session
  closes, and every project's opt-in is revoked; projects must enable the example again after a
  re-install, which mints a new type id.

**7.5 Correct the upgrade note.** `docs/examples/creative-thinking-course.md:252-254` currently
reads that deleting "ends its active activations across every tenant, so do it between
classes". It must gain the opt-in loss and the new-id consequence, and say to tell the affected
Project Owners before deleting. The document already establishes at `:205-207` that enabling is
a per-project Project Owner act, so the reader has the concept; the upgrade note simply never
connects it to delete.

**Why this does not mask the symptom.** The symptom is a row that outlives its type; the cause
is a cleanup that was delegated to a database mechanism the delete strategy makes unreachable.
The fix performs the cleanup in code. It does **not** change the delete to a hard delete, which
would be a far larger behavioural change and would lose the audit trail the tombstone carries.

**Data repair.** Existing orphan rows are not repaired by the code change - they belong to types
already deleted. Because they are permanently inert (§5.3) and bounded by deliberate admin
deletes, no repair migration is justified. Recorded as FU-1 with the one-line query an operator
can run if they want the table clean.

## 8. Regression Test Plan

The failing test comes first. Note the harness gap: `test_activity_type_delete.py`'s facade
double does not stub `_optin_repo` at all, so that must be added before anything can be
asserted.

**8.1 The failing test - facade orchestration.** In
`backend/tests/unit/test_activity_type_delete.py`, extend `_facade_with` (`:76-92`) to stub
`_optin_repo`, then assert that `delete_platform_type` awaits `remove_all_for_type` once with
the type id. Fails today because no such call exists.

**8.2 The negative twin.** In `TestDeleteTypeCascade`, assert a **project-scoped** delete does
*not* call `remove_all_for_type` (Q-3). This pins the placement decision, not just the presence
of the call.

**8.3 The `db`-tier test, which is not optional here.** Per `backend/CLAUDE.md` the unit tier
compiles with `literal_binds` and never executes, so it cannot distinguish a `DELETE` that runs
from one that matches nothing - and "the cascade silently does not fire" is exactly that class
of defect. `backend/tests/integration/test_platform_activity_type_schema.py` already carries
`pytestmark = pytest.mark.db` and an `_insert_platform_type` helper (`:47-59`). Add: insert a
platform type, add an opt-in row, delete the type through the facade, and assert the join table
holds no row for that type. **Written against the current tree this fails with `1 != 0`, and it
is the direct empirical proof of the finding.**

**8.4 Repository level.** A compiled-SQL assertion in `backend/tests/unit/test_activity_repos.py`
alongside `:266-276`, that `remove_all_for_type` filters on `activity_type_id` and nothing else -
specifically that it is not accidentally project-scoped and does not delete unconditionally.

**8.5 Audit metadata.** Assert the `activity_type.deleted` event carries `optins_removed` with
the right count, and that a project-type delete's event is unchanged.

**8.6 Must stay green unmodified.**
`test_activity_type_delete.py::TestPlatformTypeDeleteAuthority::test_an_admin_delete_ends_activations_in_every_tenant`
(`:191-206`) and the project-bounded opt-out cascade tests - AC-10 of the platform-example
dossier must continue to hold, since this fix must not blur the two delete paths it separates.

## 9. Risks and Rollback

- **Low.** One additive repository method, one call, one optional service parameter, two
  docstrings, one documentation paragraph. No migration, no API contract change, no frontend.
- **The DELETE is unbounded by project.** `remove_all_for_type` deletes every project's row for
  that type, which is correct for an admin delete (the type is going away for everyone) and is
  precisely what must **not** happen on the opt-out path. AC-4 and §8.2 exist to keep the two
  apart; AC-10 of `docs/tasks/2026-08-09-platform-example-activity-types/spec.md:545-549`
  established that separation and this change must not erode it.
- **The audit record changes shape.** Adding `optins_removed` to `activity_type.deleted`'s
  metadata is additive, but any consumer parsing that metadata strictly would see a new key.
  Metadata is a free-form dict throughout the audit context, so this is low risk; noted for
  completeness.
- **Pre-existing orphan rows stay.** The fix is forward-looking. Stated in §7 rather than
  discovered.
- **Rollback**: `git revert`. The new repository method would have no caller; nothing else
  depends on it.

## 10. Acceptance Criteria

- [ ] AC-1: The `db`-tier test from §8.3 fails before the fix and passes after: after deleting a
  platform type, `project_activity_type_optins` holds no row for it.
- [x] AC-2: `delete_platform_type` removes every project's opt-in for the type and passes the
  count into the `activity_type.deleted` audit metadata as `optins_removed`.
- [x] AC-3: No `activity_type.opted_out` event is emitted by the delete path (Q-2).
- [x] AC-4: A **project-scoped** delete calls no opt-in removal at all, and the project-bounded
  opt-out cascade is unchanged - AC-10 of the platform-example dossier still holds.
- [x] AC-5: `remove_all_for_type` filters on `activity_type_id` only, pinned by the compiled-SQL
  test in §8.4.
- [x] AC-6: The two docstrings (`facade.py:487-488`, `admin_activities.py:434-435`) describe
  what the code does, including that the cascade does not fire and that a re-install mints a new
  id.
- [x] AC-7: `docs/examples/creative-thinking-course.md:252-254` states that deleting revokes
  every project's opt-in and that re-installing does not restore it, so Project Owners must
  re-enable.
- [x] AC-8: `optin_repo.py`'s module docstring explains why a delete-by-type does not widen the
  reachability answer its narrowness protects.
- [x] AC-9: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`;
  `db` and `integration` tiers on CI, which is authoritative per the project's remote-CI rule.
  (`ruff`: clean, 943 files formatted. `mypy`: no issues in 938 files. `pytest`: 6852 passed,
  6 skipped - see D-7 for the one pre-existing file excluded, and D-4 for the tiers this host
  cannot run at all.)

## 11. SRS Delta

**None.** [R30.33] already makes the opt-in the authorization record; this restores the
invariant that an authorization record does not outlive the thing it authorizes. [R30.32] is
unchanged.

Deliberately **not** drafted: a requirement that an admin delete emit per-project audit events.
Q-2 concluded [R30.33]'s existing sentence does not require it and that a synthetic
`opted_out` would be a false record. If a reviewer disagrees, that is a new requirement and a
new dossier, not an amendment smuggled in here - see §14.

## 12. Deviation Log

- **D-1**: §7.3 gave `soft_delete` the `extra_metadata` parameter but did not say how it would
  reach it - the facade calls `soft_delete` through `_cascade_delete`, which both delete paths
  share. `_cascade_delete` therefore gained a pass-through `extra_metadata` parameter of its own,
  defaulting to `None`. The Q-3 placement decision is unchanged and is now visible in the
  signatures: only `delete_platform_type` ever passes a value, and the project path's audit
  record is byte-identical to before (pinned by
  `TestPlatformDeleteAuditRecord::test_a_project_type_delete_writes_no_optin_count`).
- **D-2**: §8.5 asked for an assertion that the event carries the count; the test built for it
  drives a **real** `ActivityTypeService` over a mocked repository rather than asserting on a
  mocked `soft_delete` call, so the metadata dict asserted is the one the service actually
  builds - including that `scope` and `key` survive the merge. The mock-level assertion exists
  too (`test_the_delete_event_records_how_many_optins_were_revoked`); they fail for different
  reasons, which is the point of keeping both.
- **D-3**: The `db`-tier test does more than §8.3 asked. `check-quality` found that nothing in
  the change exercised the one part that depends on real cursor semantics: the unit tier mocks
  `result.scalars().all()`, so a wrong way of reading `RETURNING project_id` would still delete
  the rows correctly, still pass every test, and silently write `optins_removed: "0"` into the
  audit trail forever. The db test now reads the `activity_type.deleted` row back out of
  `audit_logs` and asserts the count, not only that the join table is empty.
- **D-4**: **AC-1 has not been executed anywhere.** Docker is unavailable on this host and no
  local PostgreSQL is listening on 5432, so the `db`/`integration` tiers cannot run here at all;
  the test is written and is the empirical proof the dossier calls for, but it rests on reasoning
  until the `backend-db` CI job runs it. This is the sixth consecutive dossier in this series to
  record the same gap. AC-1's checkbox stays unticked deliberately - see §14 OQ-2.
- **D-5**: No behavioural verification (same cause as D-4). The user-visible change is small
  (an admin delete now revokes opt-ins, and two surfaces describe it accurately) but the
  end-to-end flow - delete a platform type, observe the projects lose the example - has not been
  seen in a browser. Confirm on the first deployed build.
- **D-7**: `pytest -q` over `tests/unit` was run as `--ignore=tests/unit/test_graphrag_builder.py`.
  That file **hangs indefinitely on this host**, in isolation as well as in the tier (confirmed
  twice, at the same point both times, with the process idle rather than working). It is
  pre-existing and outside this diff - it last changed in `1c3bad6`, an unrelated ruff bump, and
  nothing in `contexts/activities/` reaches it. Excluding it, the tier is **6852 passed, 6
  skipped**. Recorded rather than chased because it is not this task's defect, but somebody
  should chase it: 48 tests are silently unrunnable locally, and only CI is covering them.
- **D-6**: §7.5 said the upgrade note "must gain the opt-in loss and the new-id consequence, and
  say to tell the affected Project Owners before deleting". It also gained what the affected
  facilitators will actually see (a bare 404 from activation, and the example showing as not
  enabled with no explanation), because that is the symptom an operator will be asked about and
  the reason the warning has to be given *before* rather than after.

## 13. Follow-ups

- **FU-1**: Pre-existing orphan rows are not repaired. An operator who wants the table clean can
  run `DELETE FROM project_activity_type_optins o USING activity_types t WHERE
  o.activity_type_id = t.id AND t.deleted_at IS NOT NULL`. Not shipped as a migration because
  the rows are inert and bounded by deliberate admin deletes; recorded so nobody has to derive
  the query.
- **FU-2**: The same pattern exists in `agent_group_members`, where
  `backend/tests/wiring/test_agent_group_repository.py:144` explicitly asserts the row survives
  ("no cascade cleanup"). Two instances of "an `ON DELETE CASCADE` that a soft delete makes
  unreachable" is enough to warrant a written rule: either soft-deleting parents clean their own
  join rows, or the FK is documented as decorative. A sweep for `ON DELETE CASCADE` against
  tables whose parent is soft-deleted would say how many more there are.
- **FU-3**: `ix_project_activity_type_optins_type` (`0076:103-107`) was created for a cascade
  that never ran and has had no caller until this fix. Worth checking whether other indexes in
  the tree were added speculatively for paths that were never built.
- **FU-4**: The delete-and-reinstall dance that makes this defect matter exists only because
  re-sync was deferred - OQ-1 of the platform-example dossier and OQ-2 of the agent-packs
  dossier, the same question left open twice. Resolving it once for courses and packs together
  would remove the upgrade procedure this dossier is documenting the hazards of.

- **FU-5**: `remove_all_for_type` carries no guard of its own - it will wipe every project's
  opt-in for whatever id it is handed. Safe today (one caller, admin-gated, scope-checked two
  lines above it, and pinned by a negative test), and pushing the scope check down would put a
  type read in the infrastructure layer, which is why it was not done. Recorded by
  `check-security` as hardening so a future second caller knows the method is unguarded.

## 14. Open Questions

- **OQ-2**: AC-1 is unverified (D-4): no host in this environment can run the `db` tier. The
  dossier is complete in every other respect. Either the `backend-db` CI job runs it green - at
  which point AC-1 is ticked and nothing else changes - or it fails, and §8.3's test is the thing
  that tells us so. Nothing downstream depends on the answer.

- **OQ-1**: Q-2 assumes no compliance stance requires per-project auditability of access lost to
  an admin delete. If one does, the honest artifact is a new action string
  (`activity_type.access_revoked_by_delete`) with the admin as actor and the project in
  metadata - **not** a reused `activity_type.opted_out`, which would assert that a Project Owner
  performed an act they did not. Does not block this fix: the `optins_removed` count added in
  §7.3 is a strict improvement either way, and the new action could be layered on later without
  reworking anything here.
