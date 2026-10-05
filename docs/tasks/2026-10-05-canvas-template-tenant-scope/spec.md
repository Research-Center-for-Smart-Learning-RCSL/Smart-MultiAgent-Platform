---
type: bugfix
status: draft
created: 2026-10-05
requirements: [R13.44, R13.59, R13.60, R13.61]
depends_on: []
---

# Project canvas templates leak across tenants and can be applied into any room

## 1. Summary

Any authenticated principal, an anonymous guest from any public link included, can list every
project's canvas templates across all tenants with `GET /api/canvas-templates?scope=project`, and can
apply any template by id into a room they can write, which copies the template's content onto a
canvas they can read. Templates saved with "save as template" are copies of real project canvases,
so this is a cross-tenant read. This dossier fixes finding F-12 of
`docs/audits/2026-10-05-guest-anonymous-session/findings.md` by applying one "readable template" rule
([R13.59]) to list, get, apply and delete.

## 2. Observed vs Expected

- **Observed** (`main` at `c5114a13`)
  - **List** (`backend/app/api/v1/canvas_templates.py:130-157`): membership is checked only when
    `project_id` is supplied (`:146-153`). The repository, given `scope=project` and no project id,
    filters by scope alone (`backend/contexts/canvas/infrastructure/repositories/canvas_template_repo.py:42-43`),
    returning every project's templates. Commit `a685b07e` (2026-09-11) introduced this by changing
    the repository's `else:` platform fallback to `elif scope is None:` (`:51`); before it, that case
    produced an impossible filter and returned nothing.
  - **Apply** (`canvas_templates.py:250-288`): checks only `resolve_room_access` and `ensure_can_send`
    on the target room (`:258-259`); the service loads the template by id and never compares its
    project with the room's (`backend/contexts/canvas/application/template_service.py:157-170`).
  - **Delete** (`canvas_templates.py:214-244`): a foreign project template answers 403 and an unknown
    id 404, an existence oracle.
  - Every route takes `Depends(current_principal)`, which admits guest tokens
    (`backend/app/api/middleware/auth.py:103-109`).
  - `backend/tests/unit/test_canvas_templates.py` has no route or authorization test and no db-tier
    test; the templates dossier planned one ("project template not visible to non-members",
    `docs/tasks/2026-09-10-canvas-templates/spec.md:229-233`) that was never written.
- **Expected**
  - [R13.59] (`REQUIREMENTS.md:807`): "Platform templates are readable by all authenticated users;
    project templates are readable by project members."
  - Templates dossier AC-5 (`spec.md:94-95`): project templates appear only for members of that
    project; D-4 (`:259-263`) records the guest bypass as fixed.
  - [R13.44]: a guest with guest links on may write its own room's canvas, so applying a platform
    template there is legitimate.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What does `GET /api/canvas-templates?scope=project` without `project_id` return? | 422 (validation problem): `scope=project` requires `project_id`, which is then gated by `assert_project_membership`. The repository's platform-only fallback for an unspecified filter is restored as defence in depth. | The only client shape passes `project_id` (`frontend/src/slices/canvas/components/CanvasTemplatePicker.vue:27-29`). A "templates of all my projects" listing would need a batch of `roles_for` semantics that `member_project_ids` does not match (it omits org owners and user-owned projects, `backend/contexts/tenancy/infrastructure/repositories.py:555-571`); no caller needs it. |
| Q-2 | Who may apply which template into a room? | A platform template: anyone who may send in the room (guests included). A project template: only when it belongs to the room's project and the caller holds a role in that project (or is a platform admin). Anything else answers the same 404 as an unknown id. | Applying copies the content onto a canvas the caller reads, so apply must not grant more than [R13.59]'s read rule; a guest in the right room is not a project member. Same 404 text (`template_service.py:170`) avoids an oracle. |
| Q-3 | Delete of a template the caller cannot read? | 404, as for an unknown id; a readable project template the caller cannot moderate stays 403. | Removes the existence oracle without hiding a real permission refusal from members. |
| Q-4 | The audit's note that applying `elements` overwrites a non-empty canvas (audit FU-8)? | Not a defect; unchanged. | [R13.60] (`REQUIREMENTS.md:809`) specifies `replace=true`, as rewritten by `docs/tasks/2026-09-14-canvas-crdt-bridge-remaining/spec.md:38,50`; the original dossier's empty-canvas rule is superseded. |
| Q-5 | Does this depend on another dossier? | `depends_on: []`. | No active dossier touches the canvas template files (BOARD.md); the templates and CRDT-bridge dossiers are implemented. |

## 4. Reproduction

1. As a member of project P1 only, call `GET /api/canvas-templates?scope=project`: templates of
   every project are returned.
2. Take a template id of project P2 and `POST /api/chatrooms/{room in P1}/canvas/apply-template`:
   the P2 template's content lands on the P1 room's canvas.
3. Repeat step 1 with an anonymous guest token from any public room link: same result.

## 5. Root Cause Analysis

1. The read rule of [R13.59] is enforced at one call site per route, by hand, and only for the
   request shape the picker happened to use (`project_id` supplied). The repository treats "no
   project filter" as "no filter". **Root cause of the list leak**, made reachable by `a685b07e`.
2. Apply treats "may write the target canvas" as sufficient and never asks whether the caller may
   read the source template. **Root cause of the cross-project apply.**

## 6. Blast Radius and Sibling Suspects

- **Blast radius.** Every tenant's project templates, readable by any principal; not guest-specific.
  No live UI calls these routes (the picker was unmounted in `ac74db18`, save-as-template removed from
  the toolbar in `b3d9714c`), so the fix breaks no screen.
- **Sibling suspects.**
  - `GET /api/canvas-templates/{id}` (`canvas_templates.py:160-177`): **cleared**, already 404 for
    non-members of a project template; folded into the shared rule.
  - `POST /api/canvas-templates` and save-as-template (`:180-211`, `:294-336`): **cleared**, moderator
    of the room's or the given project.
  - Org-scope templates: **cleared**, the scope does not exist (`backend/contexts/canvas/domain/models.py:12-14`).
  - Legacy `objects`-shape templates (all four seeded platform templates,
    `backend/alembic/versions/0094_canvas_templates.py:68,103,138,185`) raise `CanvasNotEmpty` on a
    non-empty canvas (`template_service.py:210-212`) where [R13.60] says they are converted on apply:
    **confirmed** drift, unrelated to tenancy; FU-1.
  - Audit FU-2 asks for a check-security pass over the whole canvas surface; FU-2 here.

## 7. Fix Design

- **One rule.** A `readable_template(template, principal, roles_for)` decision in the canvas
  application layer: platform template, or the caller is a platform admin, or the caller holds any
  role in `template.project_id` (via the tenancy role resolver, the same semantics as
  `assert_project_membership`, `backend/app/api/v1/deps.py:101-123`). List, get, apply and delete use
  it.
- **List** (`canvas_templates.py`): the query model rejects `scope=project` without `project_id`
  (422); with `project_id`, `assert_project_membership` replaces the hand-rolled check. The repository
  restores `else:` (platform only) for any filter combination it does not recognise.
- **Apply**: after `resolve_room_access` and `ensure_can_send`, the service receives the room's
  `project_id` and the caller's readability decision; a project template whose `project_id` differs
  from the room's, or that the caller cannot read, raises the same not-found as an unknown id.
- **Delete**: unreadable gives 404 before the moderator check.
- **Existing debt (record, do not imitate).** The membership checks are inline per route; the
  repository's filter branches encode policy. `test_canvas_templates.py` tests services only. The
  frontend picker and API helpers are unused (`frontend/src/slices/canvas/api/index.ts:192-235`); left
  in place.
- **Patterns to follow.** Routes call facades only (`backend/CLAUDE.md`); room gates as in
  `canvas_templates.py:258-259`; project membership through `assert_project_membership`.
- **Reuse inventory.** `assert_project_membership`, `resolve_room_access` (its `project_id`),
  `ensure_can_send`, `TenancyRoleResolver.roles_for`, `is_moderator_roles`, the existing
  "Template not found" mapping (`canvas_templates.py:277-281`).
- **Security considerations.** This closes a tenant-boundary read (check-security Part B, CRITICAL
  class). Every refusal of an unreadable template is byte-identical to an unknown id on get, apply and
  delete. Guests keep platform templates only.

## 8. Regression Test Plan

Written first, failing against current code:

1. Route unit tests (new `backend/tests/unit/test_canvas_template_authz.py`, facade patched as in
   `backend/tests/unit/test_guest_roster_events.py`): `scope=project` without `project_id` gives 422;
   with a foreign `project_id` gives 403; a guest listing project scope is refused; apply of a foreign
   project's template into the caller's room gives 404 with the unknown-id body; same-project apply by
   a member succeeds; platform apply by a guest succeeds; project-template apply by a guest of a room
   in that project gives 404; delete of a foreign template gives 404.
2. db tier (`pytest -m db`): the repository's list never returns another project's templates for any
   filter combination, including the unrecognised one.

## 9. Risks and Rollback

- A client that relied on listing every project template without `project_id` breaks; none exists.
- No migration, API shape unchanged apart from the new 422. Rollback: revert the commits.

## 10. Acceptance Criteria

- [ ] AC-1: no request shape returns a project template to a caller without a role in that project
  (platform admins excepted); `scope=project` without `project_id` is a 422.
- [ ] AC-2: applying a template into a room succeeds only for platform templates, or for a template of
  the room's own project applied by a caller with a role in it; otherwise the response is identical
  to an unknown template id.
- [ ] AC-3: deleting a template the caller cannot read answers 404.
- [ ] AC-4: guests can list, read and apply platform templates into their own room, and nothing else.
- [ ] AC-5: backend lint, typecheck and tests, including the db tier, pass in CI.

## 11. SRS Delta

None. [R13.59] already states the rule.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** Legacy `objects`-shape templates, including all seeded platform templates, refuse a
  non-empty canvas instead of being converted and applied with replace as [R13.60] specifies
  (`template_service.py:210-240`).
- **FU-2.** Run `check-security` over the whole canvas surface (audit FU-2), including the unmounted
  picker's API helpers.
