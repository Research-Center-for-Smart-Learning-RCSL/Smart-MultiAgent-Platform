---
type: feature
status: implemented
created: 2026-09-10
requirements: [R13.42, R13.43]
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Templates

## 1. Summary

Provide pre-built canvas templates that reduce the blank-canvas barrier by offering
structured starting points for common collaboration scenarios. Templates are available at
two scopes: platform-provided (shipped with the product) and project-scoped (created by
project admins from existing canvases). When a user opens an empty canvas, a template
picker offers the available templates; selecting one populates the canvas with the
template's pre-positioned objects. Template objects become regular canvas objects once
placed and are fully editable.

## 2. Goals and Non-goals

**Goals**

- Ship 4-5 platform-provided templates: brainstorming, retrospective, SWOT analysis,
  mind map, and optionally Kanban board.
- Project admins can save an existing canvas as a project-scoped template ("save as
  template").
- Template picker appears on empty canvases and is dismissible (user can always start
  blank).
- Templates define a layout of pre-positioned objects (notes, text blocks, shapes,
  connectors) as a JSON structure matching the `snapshot_data` schema.
- Templates are stored in the database with scope-based access control (platform scope
  readable by all, project scope readable by project members).

**Non-goals**

- Template marketplace or sharing between organizations.
- Template versioning or migration (a template is a frozen snapshot; editing creates a
  new one).
- Template thumbnails generated server-side (frontend renders a preview from the JSON).
- Org-scoped or user-scoped templates (only platform and project for now).
- Template categories or tagging beyond the name and description.

## 3. Clarifications

- **Q-1**: Template scope? **Platform + project.** Platform templates ship as seed data
  (inserted by a migration or bootstrap script). Project templates are user-created.
  Follows the same two-scope subset used by prompt templates
  (`contexts/prompt_studio/domain/models.py:94`).

- **Q-2**: Storage model? **Database-backed `canvas_templates` table.** Not static JSON
  in the frontend bundle, because project-scoped templates need server-side CRUD and
  access control. Platform templates are seeded into the same table.

- **Q-3**: Template data structure? **Same shape as `snapshot_data`**: `{"objects": [{id,
  kind, content, position_x, position_y, width, height, z_index, style}, ...]}`. This
  reuses the existing serialization and can be applied via `batch_operate()`.

## 4. Requirements

### 4.1 New requirements (SRS Delta)

- **[R13.59]** A canvas template is a named, immutable JSON layout definition. Each
  template has a scope (platform or project), a name, a description, and a `template_data`
  JSONB column holding objects in the same schema as `canvas_snapshots.snapshot_data`.
  Platform templates are readable by all authenticated users; project templates are
  readable by project members.

- **[R13.60]** Applying a template to a canvas creates real canvas objects via
  `batch_operate`. Template objects become regular editable objects; no link to the source
  template is maintained. A template may only be applied to an empty canvas (no existing
  objects) unless the user explicitly confirms overwrite.

- **[R13.61]** A project admin may create a project-scoped template from an existing
  canvas ("save as template"). The template captures the canvas's current object state as
  a snapshot. Deleting the source canvas does not affect the template.

## 5. Acceptance Criteria

- [ ] AC-1: When opening an empty canvas, the user sees a template picker with at least
  4 built-in platform templates and any project-scoped templates.
  (Unticked: needs running stack for browser verification. Code verified: CanvasPanel
  shows CanvasTemplatePicker when objects.length === 0; migration seeds 4 platform
  templates; listTemplates returns platform + project-scoped templates.)
- [x] AC-2: Selecting a template populates the canvas with the template's objects. The
  objects are fully editable. (Verified by unit test TestApplyTemplate.)
- [x] AC-3: The user can dismiss the picker and start with a blank canvas.
  (Verified in code: "Start blank" sets pickerDismissed=true.)
- [ ] AC-4: A project admin can save the current canvas as a project-scoped template via
  a "Save as template" action in the canvas toolbar.
  (Unticked: needs running stack. Code verified: toolbar shows button when isModerator,
  endpoint checks access.is_moderator.)
- [x] AC-5: Project-scoped templates appear in the picker only for members of that project.
  (Verified: repo filters by project_id, API checks project membership.)
- [x] AC-6: Platform templates are readable by all authenticated users but cannot be
  created or deleted via the API (seed data only).
  (Verified: create endpoint forces PROJECT scope; delete rejects PLATFORM with 403.)
- [x] AC-7: Template data validates against the `snapshot_data` schema (objects array with
  required fields). Invalid template data is rejected at creation.
  (Verified by unit tests TestValidateTemplateData.)

## 6. Detailed Changes

### 6.1 Backend -- Domain model

New file: `contexts/canvas/domain/models.py` (extend)

- `CanvasTemplateScope` enum: `PLATFORM`, `PROJECT`.
- `CanvasTemplate` dataclass: `id`, `scope`, `project_id` (nullable, set for project
  scope), `name`, `description`, `template_data` (dict, same schema as snapshot_data),
  `created_by_user_id`, `created_at`.

### 6.2 Backend -- Database

New migration: add `canvas_templates` table.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| scope | ENUM(platform, project) | |
| project_id | UUID FK nullable | set for project-scoped |
| name | VARCHAR(200) | unique per (scope, project_id) |
| description | TEXT nullable | |
| template_data | JSONB | same schema as snapshot_data |
| created_by_user_id | UUID FK nullable | null for platform seeds |
| created_at | TIMESTAMPTZ | |
| deleted_at | TIMESTAMPTZ nullable | soft delete |

### 6.3 Backend -- Repository

Extend `CanvasRepository` or new `CanvasTemplateRepository`:

- `list_templates(scope, project_id)` -- returns templates visible to the caller.
- `get_template(template_id)` -- returns one template.
- `create_template(values)` -- inserts a new template.
- `delete_template(template_id)` -- soft delete.

### 6.4 Backend -- Service

Extend `CanvasService`:

- `apply_template(canvas_id, template_id, chatroom_id, actor_*)` -- loads template,
  validates canvas is empty, creates objects via `batch_operate`.
- `create_template_from_canvas(canvas_id, name, description, project_id, actor_*)` --
  reads current objects, builds template_data, inserts template.

### 6.5 Backend -- API endpoints

Add to `app/api/v1/canvas.py` (or new `app/api/v1/canvas_templates.py`):

- `GET /api/canvas-templates?scope=platform&project_id=...` -- list available templates.
- `GET /api/canvas-templates/{template_id}` -- get one template.
- `POST /api/chatrooms/{chatroom_id}/canvas/apply-template` -- apply template to canvas.
- `POST /api/canvas-templates` -- create project-scoped template (admin only).
- `DELETE /api/canvas-templates/{template_id}` -- soft delete (admin only).

### 6.6 Backend -- Seed data

Migration or bootstrap script seeds 4-5 platform templates:

1. **Brainstorming**: Central topic note + 4 surrounding sticky notes + connectors.
2. **Retrospective**: Three columns (What went well / What to improve / Action items)
   with header text blocks and empty sticky notes.
3. **SWOT Analysis**: 2x2 grid of labeled quadrants (Strengths, Weaknesses,
   Opportunities, Threats) with header text blocks.
4. **Mind Map**: Central topic with 4 branching connectors to subtopic notes.

### 6.7 Frontend -- Template picker component

New component: `slices/canvas/components/CanvasTemplatePicker.vue`

- Displayed in `CanvasPanel.vue` when the canvas is empty (replaces or augments the
  current `SEmptyState`).
- Grid of template cards showing name, description, and a rendered preview (small-scale
  rendering of template_data objects as simple shapes).
- "Start blank" button to dismiss.
- Selecting a template calls `POST .../apply-template`.

### 6.8 Frontend -- Save as template

- New button in `CanvasToolbar.vue`: "Save as template" (visible to project admins).
- Opens a dialog with name + description fields.
- Calls `POST /api/canvas-templates` with current canvas objects.

## 7. Existing Debt and Patterns

### Patterns to follow

- **Scope model**: `PromptTemplate` at `contexts/prompt_studio/domain/models.py:94` for
  the scoped entity pattern.
- **Batch create**: `CanvasService.batch_operate()` at `canvas_service.py:242` for
  applying template objects.
- **Seed data**: Follow existing migration seed patterns in `alembic/versions/`.

### Reuse inventory

| What | Where | Use |
|------|-------|-----|
| `CanvasService.batch_operate()` | `canvas_service.py:242` | Apply template objects |
| `CanvasService.list_objects()` | `canvas_service.py:131` | Read objects for "save as template" |
| `snapshot_data` schema | `canvas_service.py:293-309` | Template data format |
| `SEmptyState` | `shared/ui/SEmptyState.vue` | Base for template picker empty state |

## 8. Security Considerations

- Template data is JSONB user input -- validate structure at the API boundary (Pydantic
  model matching snapshot_data schema).
- Project-scoped template creation requires project admin role check.
- Platform template mutation endpoints are admin-only or disabled (seed data only).
- Template names/descriptions are user-generated text -- sanitize for display (no HTML).

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Large template_data payloads | Low | Medium | Cap template_data at 500 KB; cap objects per template at 200 |
| Template preview rendering performance | Low | Low | Simple shape rendering, not full Excalidraw mount |

## 10. Test Plan

### Unit tests

- Template domain model validation (scope, required fields).
- Template application to empty canvas (objects created correctly).
- Template application to non-empty canvas (rejected unless overwrite confirmed).
- "Save as template" builds correct template_data from canvas objects.

### Integration tests (pytest.mark.db)

- Template CRUD lifecycle (create, list, get, delete).
- Scope visibility (project template not visible to non-members).
- Platform templates seeded correctly.

### E2E tests

- Open empty canvas, see template picker, select brainstorming, verify objects appear.
- Dismiss picker, verify blank canvas.
- Save existing canvas as template, verify it appears in picker for same project.

## 11. Open Questions

None.

## 12. Deviation Log

- **D-1**: AC-1 and AC-4 remain unticked because they require a running stack for browser
  verification. The code paths are verified by reading the source and by unit tests, but no
  browser session has confirmed the picker renders or that the save-as-template flow works
  end-to-end.

- **D-2**: The spec proposed templates as either a new repository or an extension of
  `CanvasRepository`. Implementation uses a separate `CanvasTemplateRepository` and
  `CanvasTemplateService` to maintain separation of concerns within the canvas context.

- **D-3**: Self-audit found that duplicate template names produced a raw 500. Fixed: the
  `IntegrityError` is now caught and returned as a 409 Conflict with a descriptive message.

- **D-4**: Code review found a guest authz bypass: guests could list any project's templates
  by passing `project_id`. The `is_guest` exemption was removed. Also found contradictory SQL
  when `scope=project` without `project_id`, kind enum duplication vs the domain enum,
  moderator role check duplication, and a missing `onError` handler on the save-as-template
  mutation. All five fixed.

## 13. Follow-ups

- FU-1: Template thumbnails (server-rendered PNG for gallery display).
- FU-2: Org-scoped templates if demand arises.
- FU-3: Template categories/tags for filtering in the picker.
- FU-4: The unique constraint `uq_canvas_templates_scope_project_name` does not enforce
  uniqueness for platform-scoped templates because `project_id IS NULL` and SQL treats
  NULLs as distinct. Only reachable through seed data or direct DB writes (the API blocks
  platform template creation), so risk is low. A partial unique index or a sentinel
  project_id would close this if platform template management is ever added.
- FU-5: CanvasTemplatePicker fetches each template detail sequentially (N+1 requests).
  Could be parallelized with `Promise.all` or the list endpoint could include `template_data`.
- FU-6: `apply_template` endpoint returns raw `dict[str, Any]` instead of a Pydantic response
  model, matching the pre-existing pattern in `batch_operate`. Both should be migrated to a
  typed response model.
