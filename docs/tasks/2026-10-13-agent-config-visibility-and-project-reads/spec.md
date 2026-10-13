---
type: feature
status: draft
created: 2026-10-13
requirements: [R5.05, R5.06, R8.09, R8.10, R9.01, R13.28, R13.31, R13.32, R30.25]
depends_on: []
---

# Agent configuration for editors only, and project reads for project roles only

## 1. Summary

Any holder of any role in a project can read every agent's full configuration: its system
prompt, model settings, tools, skills, workspace files and A2A dead letters. In a classroom this
means students read every teaching prompt written about them; every CRAT student is a project
member, because group submissions run on project Member Groups. Separately, and more widely, a
member of the project's parent organization who was never added to the project passes the same
"any role" gate on about 63 project-scoped reads, against [R8.09]. This feature does two things,
in two independently shippable stages. Stage 1 restricts agent configuration to the people who can
edit it; everyone else sees agent names only. Stage 2 adds a project-role gate and puts the
project-scoped reads that an organization member has no business with behind it, keeping only the
reads that reach a room admitting organization members.

Born from `docs/audits/2026-10-12-crat-ta-multi-speaker-facilitation/findings.md` F-13 and FU-9;
stage 2 also carries out the dashboard decision of
`docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md` F-24.

## 2. Goals and Non-goals

**Goals**
- Only callers who may edit an agent (Admin, Org Owner, Project Owner: matrix row 15) can read its
  configuration, tools, skills, workspace files and A2A dead letters, and project skills.
- Everyone else in a project, and anyone reaching an organization-members room, still gets agent
  names, so room labels keep working.
- The frontend shows a non-editor agent names only: no agents pages, no wake-up editor in room
  settings, and no error where a full listing used to load.
- Project-scoped reads require a project role (Project Owner, Project Member, or the inherited
  Org Owner). A bare Org Member role admits only the reads on the path to a room that admits
  organization members.
- The frontend does not offer a bare organization member the project surfaces they can no longer
  read.

**Non-goals**
- Hiding knowledge configs (RAG, knowledge maps, GraphRAG) or agent groups from ordinary project
  members. Stage 2 removes only the organization-member leak on them; whether students should
  read their contents is a separate decision (FU-1).
- Changing `TenancyRoleResolver.roles_for`. It must keep returning `ORG_MEMBER` at project scope:
  the room access check relies on it for `allow_org_members` rooms
  (`backend/contexts/conversation/application/access.py:257`).
- Write paths. Every create, edit and delete already requires a capability the matrix denies to
  Org Members and Project Members.
- Per-agent visibility flags or redacted projections (Q-1).
- Organization-scoped reads (`require_membership(org_param=...)`, eight routes in
  `backend/app/api/v1/orgs.py`). An Org Member is meant to read their organization.
- Key usage views: matrix row 5 lets Org Members view key usage at project scope; unchanged (Q-5).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Who may read an agent's full configuration? | Exactly those who may edit it: the `RESOURCE_CREATE_EDIT` capability at project scope (matrix row 15). | User's choice. Reading and editing then share one gate. Precedent: [R30.25] limits an activity type's `validator_config` to Project Owners. A per-agent "visible to members" flag and a redacted projection were offered and not chosen. |
| Q-2 | What does a non-editor see in the frontend? | Agent names only. The agents pages are not offered; room labels keep using `GET /agents/names`; room settings show bound agents by name without the wake-up editor. | User's choice. A non-editor could never save the wake-up editor anyway (`PATCH /api/agents/{id}` requires `RESOURCE_CREATE_EDIT`, `backend/app/api/v1/agents.py:792-800`). |
| Q-3 | Which reads besides the agent record move behind the edit gate? | Agent tools, agent and project skills, agent workspace files, and the agent's A2A dead-letter queue. | User's choice. Skills are instruction content as sensitive as the prompt; tool configs name sources and allowed tools; dead letters carry message content. |
| Q-4 | Fix the organization-member leak in this dossier or separately? | Here, as stage 2 of one dossier; each stage ships on its own. | User's choice. Stage 1 already closes the leak for every read it moves behind the edit gate, since Org Members do not hold that capability; stage 2 handles the rest. Precedent for a two-stage dossier: `2026-08-20-member-groups-and-room-visibility-isolation`. |
| Q-5 | Key, key-group and search-key lists? | Project roles only; key usage (matrix row 5) unchanged. | User's choice. Row 5 grants usage, not the list. |
| Q-6 | Prompt assistant and "apply canvas template", which are used inside rooms? | Project roles only, like the rest. An organization member in an organization-members room loses both. | User's choice. |
| Q-7 | Which project-scoped reads keep admitting a bare Org Member? | The room-navigation path: `GET /api/projects/{pid}` (`projects.py:282`), `GET /api/projects/{pid}/workspaces` (`workspaces.py:73`), `GET /api/workspaces/{wid}` (`workspaces.py:151`), `GET /api/workspaces/{wid}/chatrooms` (`chatrooms.py:400`) and `GET /api/projects/{pid}/agents/names` (`agents.py:500`). | These are what an organization member walks through to reach an `allow_org_members` room; the two listings already filter by room visibility (`workspaces.py:87-91`, `chatrooms.py:414`), and `ChatroomView` reads the workspace and the names (`frontend/src/slices/conversation/views/ChatroomView.vue:527,673`). Tests pin the path: `backend/tests/integration/test_room_listing_visibility_db.py:233,315-355`. |
| Q-8 | Dependencies on active dossiers? | None. | Only two dossiers are active. `2026-07-19-large-artifacts-silently-dropped` edits `turn_engine.py`; `2026-10-12-graphrag-phase5a2-evidence-refs-contract` edits evidence storage and retrieval, not these routes. |

## 4. Current State

**Gates.** `require_membership` admits any non-empty role set
(`backend/shared_kernel/auth/dependencies.py:145-172`, the check at `:169`);
`assert_project_membership` does the same (`backend/app/api/v1/deps.py:101-123`); ten routes do it
inline. `TenancyRoleResolver.roles_for` returns `{ORG_MEMBER}` for an organization member with no
`project_members` row (`backend/contexts/tenancy/interfaces/role_resolver.py:46`), and
`{ORG_OWNER, PROJECT_OWNER}` for an organization owner (`:42-49`). The codebase already describes
the distinction this feature needs: `project_role_holders` says "ORG_MEMBER alone is not a project
role" (`role_resolver.py:107-136`, cited from `access.py:120-122`).

**Agent configuration reads**, all "any role":

| Route | File:line | Returns |
|---|---|---|
| `GET /api/projects/{pid}/agents` | `agents.py:466`, gate `:470` | `list[AgentOut]`: system prompt, model, keys, knowledge bindings, wake-up, capabilities (`agents.py:403-456`) |
| `GET /api/agents/{id}` | `agents.py:755`, inline `:766-771` | `AgentOut` |
| `GET /api/agents/{id}/tools` | `agents.py:1070`, inline `:1082-1086` | tool configs with auth removed (`agents.py:981-994`) |
| `GET /api/agents/{id}/workspace-files` | `agent_workspace.py:107`, `:119-123` | file metadata |
| agent skills: list, get, export, files | `skills.py:872,898,975,989` | skill bodies and files |
| `GET /api/agents/{id}/skill-bindings` | `skills.py:1620` | bindings |
| project skills: list, get, export, files | `skills.py:1067,1092,1169,1181` | skill bodies and files |
| `GET /api/orchestration/agents/{id}/dlq` | `orchestration.py:467`, gate `:82` via `:480` | A2A envelopes |

`GET /api/projects/{pid}/agents/names` (`agents.py:476-495`) returns `{id, name}` only; it was
added for response size and deliberately shares the listing's gate
(`docs/tasks/2026-09-03-observer-ui-defect-sweep/spec.md:941-957`), which
`backend/tests/unit/test_agent_name_listing.py:67-85` pins.

**Other project-scoped reads that admit a bare Org Member** (stage 2):

| Area | Routes (file:line of the gate) |
|---|---|
| Project members | `projects.py:373` |
| RAG | `rag.py:221,306,440` |
| Knowledge maps | `knowmap.py:279,326,474,506` |
| GraphRAG | `graphrag.py:238,250,611`; non-room configs via `contexts/knowledge/interfaces/config_access.py:69-71` |
| Agent groups | `agent_groups.py:137,181,288` |
| Member groups | `member_groups.py:121` (also contradicts [R13.31]) |
| Keys | `project_keys.py:64`, `key_groups.py:165`, `search_keys.py:68` |
| MCP egress allowlist | `mcp.py:132` |
| Prompt studio | `prompt_studio.py:852,866,880` |
| Activity types | `activities.py:705` |
| Canvas templates | `canvas_templates.py:140` (read predicate used at `:190,:239,:280`), `:175` |
| Dashboard | `dashboard.py:120` via `_resolve_workspace_rooms` (`:134,:166,:187`) |

**Frontend.** Full `AgentOut` is read by `AgentListView.vue:64`, `AgentDetailView.vue:79`,
`AgentToolsView.vue:69,79`, `useRagDocuments.ts:20`, `useKnowmapDocuments.ts:31`; the agents routes
check authentication only (`frontend/src/slices/agents/routes.ts`). Room settings load the full
listing (`frontend/src/slices/conversation/composables/useChatroomBindings.ts:125`) and join it to
the bound ids (`:79-103`); a failed or partial listing drops every bound agent to `orphanAgentIds`
(`:111-115`) or errors the panel (`:152-154`). `SWakeupEditor` renders for every bound agent
without a gate (`ChatroomSettingsView.vue:846-849`). The workflow editor, agent-group detail and
activity-type form also read the listing but are already owner-gated
(`WorkflowEditorView.vue:499` behind `useBackstageGuard`, `AgentGroupDetailView.vue:46`,
`ActivityTypesView.vue:43-60`). `useProjectRole` reads `ProjectOut.is_moderator`
(`frontend/src/slices/tenancy/composables/useProjectRole.ts:26-51`;
`backend/app/api/v1/projects.py:65,103-115`).

## 5. Design

### Options considered

**Option A, gate by capability and by project role.** Stage 1 moves the agent-configuration reads
to the existing `require(Capability.RESOURCE_CREATE_EDIT, scope_from_path(...))` (or its inline
equivalent where the project is derived from the agent). Stage 2 adds one dependency,
`require_project_role(project_param=...)`, and one helper, `assert_project_role(...)`, that pass
when the role set holds `PROJECT_OWNER` or `PROJECT_MEMBER` (Org Owners carry `PROJECT_OWNER` by
inheritance, `role_resolver.py:48-49`; Admin passes as everywhere), and moves the stage-2 routes
onto them. The room-navigation reads keep `require_membership`.

**Option B, narrow `roles_for`.** Stop returning `ORG_MEMBER` at project scope. Fixes every gate at
once, but breaks `allow_org_members` rooms (`access.py:257`), whose check reads the same set.

**Option C, a redacted `AgentOut` for non-editors.** Keeps one listing route and strips sensitive
fields per role. More fields to classify, and a new field defaults to visible.

### Decision

Option A. It leaves the resolver and the room access check untouched, reuses the capability the
matrix already defines for agents, and makes "project role" a named predicate instead of an
implicit property of a non-empty set. Given up: one more gate to choose from when writing a route;
the dependency's docstring states which to use when.

## 6. Detailed Changes

### Stage 1: agent configuration for editors

- **Backend, agents API** (`backend/app/api/v1/agents.py`): `list_project_agents` (`:466`) takes
  `require(Capability.RESOURCE_CREATE_EDIT, scope_from_path(project_param="project_id"))`, like
  create (`:524-529`). `read_agent` (`:755`) and `list_agent_tools` (`:1070`) replace their inline
  membership checks with the capability decision on the agent's project, as patch does
  (`:792-800`). `GET /agents/names` (`:496`) keeps `require_membership`, so its gate and the
  listing's now differ by design.
- **Backend, agent workspace** (`agent_workspace.py:107`): same capability decision.
- **Backend, skills** (`skills.py`): the agent-skill reads (`:872,898,975,989,1620`) and the
  project-skill reads (`:1067,1092,1169,1181`) use the capability decision. Write paths already do
  through `_assert_agent_write` (`:844`).
- **Backend, orchestration** (`orchestration.py:467`): the DLQ read uses the capability decision;
  its own docstring already says not to reuse the membership gate (`:73-78`).
- **Backend, tests**: `test_agent_name_listing.py:67-85` is rewritten to pin the new split (names:
  membership; listing: edit capability).
- **Frontend, agents slice**: the agents routes and their navigation entry are offered only when
  `useProjectRole().isModerator` (admin or project moderator); a non-editor opening an agents URL
  gets the existing forbidden state rather than a failed fetch. `useRagDocuments` and
  `useKnowmapDocuments` are only reached from editor pages; no change beyond the route gate.
- **Frontend, room settings** (`useChatroomBindings.ts`, `ChatroomSettingsView.vue`): for a caller
  who is not a moderator, bound agents are resolved through `GET /agents/names` instead of the full
  listing, are not treated as orphans, and the wake-up editor is not rendered. Moderators keep
  today's behaviour.
- **Frontend, i18n**: no new strings expected; any added go to both locales.

### Stage 2: project reads for project roles

- **Backend, shared kernel** (`shared_kernel/auth/dependencies.py`): `require_project_role(*,
  project_param)` beside `require_membership`, passing for Admin or a role set intersecting
  `{PROJECT_OWNER, PROJECT_MEMBER}`, refusing with the same 403 shape. Its docstring names the
  routes that legitimately keep `require_membership` (Q-7).
- **Backend, app deps** (`app/api/v1/deps.py`): `assert_project_role(...)` beside
  `assert_project_membership`, same predicate, for routes that derive the project from a resource.
- **Backend, routes**: every stage-2 route in §4's second table moves to the project-role gate:
  `projects.py:373`; `rag.py:221,306,440`; `knowmap.py:279,326,474,506`;
  `graphrag.py:238,250,611` and the non-room branch of `config_access.py:69-71`;
  `agent_groups.py:137,181,288`; `member_groups.py:121`; `project_keys.py:64`, `key_groups.py:165`,
  `search_keys.py:68`; `mcp.py:132`; `prompt_studio.py:852,866,880`; `activities.py:705`; the
  canvas-template read predicate (`canvas_templates.py:140`) and list (`:175`); the dashboard's
  `_resolve_workspace_rooms` (`dashboard.py:120`). Room-owned GraphRAG configs keep the room ACL
  (`config_access.py:62`).
- **Backend, API contract**: `ProjectOut` gains `has_project_role: bool`, the server's verdict of
  the stage-2 predicate, beside `is_moderator` (`projects.py:65,103-115`). `gen:api` rerun required.
- **Frontend**: the project view, room composer (prompt assistant) and canvas template picker read
  `has_project_role`; a caller without it is offered the workspaces and rooms they can open and
  nothing that would 403. `useProjectRole` exposes the flag.
- **Docs**: `docs/implement/` entries that describe these gates as membership-based are updated.
- **Migration**: none.
- **Deploy/config**: none.

## 7. NFR Checklist

- [ ] i18n: any new string in both locales.
- [ ] Audit log: N/A; reads are not audited today and this adds no write.
- [ ] Tenant isolation: the whole point. Each changed route is covered by a role-parametrized test
  (§12), including the bare-Org-Member case and the cross-org case.
- [ ] Error handling UX: non-editors and bare organization members are not offered surfaces that
  would 403; a direct URL shows the existing forbidden state.
- [ ] Performance: no new queries; the predicate reuses the role set each gate already resolves.

## 8. Security Considerations

This is an authorization change on tenant boundaries (check-security's AuthZ dimensions).

- **Confidentiality gained.** Teaching prompts, skills and tool configs leave the reach of
  students; a bare Org Member loses about 63 project reads, including member lists, key lists and
  dashboards.
- **No new reach.** Every changed gate is strictly narrower; the room-navigation reads keep exactly
  today's gate.
- **Enforcement stays server-side** ([R5.05]); the frontend changes are advisory and covered by
  the same tests from the server side.
- **Residual**: knowledge configs and agent groups stay readable by ordinary project members
  (FU-1); `GET /agents/names` keeps disclosing agent names to anyone reaching a room, which
  [R28.10] already treats as public within the room.
- **Regression risk is availability, not disclosure**: a route moved to the wrong gate locks out a
  legitimate user. AC-7 and the existing room-listing tests guard the organization-member path.

## 9. Quality Notes

- **Existing debt**: three gate styles for the same predicate (dependency, deps helper, inline
  checks); this feature adds a project-role variant of the first two and replaces inline checks on
  the touched routes rather than adding new inline ones. `ProjectDetailView.vue:47` checks
  `myMembership.role === 'owner'` without inheritance; not fixed here unless it gates a stage-2
  surface (FU-2).
- **Patterns to follow**: `require(...)` usage on agent create (`agents.py:524-529`); the
  capability decision on patch (`agents.py:792-800`); `is_moderator` as a server verdict on
  `ProjectOut` (`projects.py:103-115`).
- **Reuse inventory**: `require`, `scope_from_path`, `decide` (`shared_kernel/auth/permissions.py:299-369`),
  `TenancyRoleResolver.roles_for`, `project_role_holders`, `useProjectRole`, `useBackstageGuard`,
  `agentsApi`'s names call used by `ChatroomView.vue:673`.

## 10. Risks and Rollback

- **Project members lose the agents pages.** Intended (Q-1). A teacher who is a project member but
  not an owner, and who used to read agent prompts, needs to be made an owner.
- **Bare organization members lose prompt assistant and canvas templates in organization-members
  rooms** (Q-6). Intended; stated in release notes.
- **A missed caller.** A frontend surface that still fetches the full listing for a non-editor now
  errors. Mitigated by AC-4 and a grep-based test over `agentsApi.list` call sites (§12).
- **Rollback**: revert per stage; no data changes.

## 11. Acceptance Criteria

Stage 1
- [ ] AC-1: the agent listing, agent read, tools, workspace files, agent and project skill reads
  and the agent DLQ return 403 for a Project Member and a bare Org Member, and 200 for a Project
  Owner, an Org Owner and an Admin.
- [ ] AC-2: `GET /agents/names` still returns `{id, name}` for a Project Member and for a bare Org
  Member.
- [ ] AC-3: no response to a Project Member or a bare Org Member contains a `system_prompt` field.
- [ ] AC-4: a non-editor is not offered the agents pages, and a direct agents URL shows the
  forbidden state without a failed request.
- [ ] AC-5: room settings for a non-editor list bound agents by name, render no wake-up editor, and
  mark none as orphaned; a moderator's room settings are unchanged.

Stage 2
- [ ] AC-6: every stage-2 route returns 403 for a bare Org Member and 200 for a Project Member,
  Project Owner, Org Owner and Admin.
- [ ] AC-7: a bare Org Member still reaches an `allow_org_members` room through the
  room-navigation reads (Q-7); the existing room-listing visibility tests pass unchanged.
- [ ] AC-8: `ProjectOut.has_project_role` is true for project roles and false for a bare Org
  Member; `gen:api` is rerun.
- [ ] AC-9: a bare Org Member's project view offers only workspaces and rooms, and their room view
  offers neither the prompt assistant nor canvas templates, without any 403 request.
- [ ] AC-10: the dashboard routes refuse a bare Org Member (the F-24 decision).

Both
- [ ] AC-11: `backend/tests/integration/test_permission_matrix.py` covers matrix row 28.
- [ ] AC-12: backend `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`; frontend
  `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` pass.

## 12. Test Plan

- Unit, `backend/tests/unit/test_agent_name_listing.py` rewritten: names keeps
  `require_membership`, the listing requires the edit capability.
- Unit, new `backend/tests/unit/test_require_project_role.py`: the dependency and the deps helper
  pass for Admin, Project Owner, Project Member and Org Owner, refuse a bare Org Member and an empty
  set.
- Integration (`pytest.mark.integration`), new `backend/tests/integration/test_project_read_gates.py`:
  one parametrized table of (route, expected status per role) for every stage-1 and stage-2 route
  and the five room-navigation reads, run against seeded users in each role, including a member of
  another organization. This is AC-1, AC-2, AC-6, AC-7, AC-10.
- Unit, a response scan for AC-3 over the stage-1 routes for non-editor roles.
- Integration, `test_permission_matrix.py`: row 28.
- Frontend: `useChatroomBindings` and `ChatroomSettingsView` tests for a non-moderator (AC-5); an
  agents route guard test (AC-4); a `ProjectDetailView` / composer test for
  `has_project_role = false` (AC-9); a test that every `agentsApi.list` call site sits behind an
  editor gate.
- Manual via the `run` skill: a student account (Project Member) and a bare Org Member walk a CRAT
  room and the project page.

## 13. SRS Delta

Add to the §5.2 matrix (`REQUIREMENTS.md:178-206`) after row 27:

> | 28 | Read agent configuration: prompt, model settings, tools, skills (agent and project scope), workspace files, A2A dead letters | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ |

Add after [R5.05] (`REQUIREMENTS.md:210`):

> **[R5.07]** Project-scoped reads require a project role: Project Owner, Project Member, or the
> Project Owner role an Org Owner inherits ([R5.03]). An Org Member role alone, held through the
> project's parent organization ([R8.09]), admits only the reads that lead to a chat room admitting
> Org Members: the project, its workspace list, a workspace, a workspace's room list, and agent
> names. The two lists stay filtered to what the caller can open ([R13.32]). Agent names are
> readable by every project role and on that path; the rest of an agent's configuration follows
> row 28.

Amend the §22.6 Agents table (`REQUIREMENTS.md:1634,1636`): `GET /api/projects/{pid}/agents`
"List (matrix row 28)."; `GET /api/agents/{id}` "Read (matrix row 28)."; and add
`GET /api/projects/{pid}/agents/names` "Names only, `{id, name}`; any project role and the
[R5.07] room path."

`docs/traceability.csv` is regenerated with `python scripts/traceability.py`.

## 14. Open Questions

None blocking.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

- **FU-1**: whether ordinary project members (students) should read knowledge configs (RAG,
  knowledge maps, GraphRAG) and agent groups. Stage 2 only removes the organization-member leak.
- **FU-2**: `ProjectDetailView.vue:47` checks the member row's role without the Org Owner
  inheritance that `is_moderator` applies.
