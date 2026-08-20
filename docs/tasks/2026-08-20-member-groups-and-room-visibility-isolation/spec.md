---
type: feature
status: approved
created: 2026-08-20
requirements: [R5.03, R5.04, R5.05, R8.08, R11.17, R13.02, R13.04, R13.07, R28.02]
depends_on: []
---

# Member Groups and Room-Visibility Isolation

## 1. Summary

A self-hosted deployment today has exactly two containers for people — Org and Project —
and a chat room whose visibility is one of four project-or-org-wide tiers. There is no way
to split one project's members into sub-groups whose rooms are invisible to each other,
which is what a class of students in one course project needs. Worse, the isolation that
does exist is incomplete: any Org Member can enumerate the name, access flags and
observer status of **every** chat room in **every** project of that Org, because the room
listing never applies the room-flag check that every read path applies.

This dossier does two things, in two stages that share one design:

- **Stage 1 (restores intent)** — enumeration follows confidentiality. A chat-room listing
  returns only rooms the caller may read; a workspace listing only workspaces holding at
  least one such room; a project listing only projects the caller belongs to, owns through
  their Org, or that hold at least one such room.
- **Stage 2 (new capability)** — an optional per-project **Member Group** layer plus a
  fifth room access flag, so a Project Owner may bind a room to named groups and leave the
  rest of the project unable to see it. A project that defines no groups behaves exactly
  as it does today.

Stage 1 is a precondition for Stage 2, not merely adjacent to it: all members of a project
share the room listing, so without the listing fix, group-bound rooms would still be
enumerated by every project member.

## 2. Goals and Non-goals

**Goals**

- A Project Owner can create named Member Groups inside a project, put existing project
  members in them, and bind a chat room to one or more of them.
- A member of group A cannot read, send in, search, export, subscribe to, **or enumerate**
  a room bound only to group B.
- Grouping is optional and inert until used. An existing deployment sees no behavioural
  change until someone creates a group and binds a room to it.
- Project Owners and Org Owners of the parent Org keep reaching every room in the project
  (R8.08 / R5.03 inheritance is unchanged).
- The four existing access flags keep their current meaning, and the fifth tier is
  evaluated by the same single function every read path already funnels through.
- `GET /api/projects`, `GET /api/projects/{id}/workspaces` and
  `GET /api/workspaces/{id}/chatrooms` stop disclosing resources the caller cannot open.

**Non-goals**

- **Onboarding.** Adding members to a project still happens only through the existing
  email invite flow. The three onboarding paths the user has decided on (invite response
  carrying a copyable accept link, direct add of an existing user by exact email, admin
  account creation) are a separate dossier — see §16 FU-1.
- **Groups above the project.** Member Groups are per-project. Cross-project or org-level
  groups are out of scope.
- **Group-scoped resources.** Agents, keys, key groups, RAG/GraphRAG configs, skills and
  prompt-studio templates stay project-scoped and remain visible to every project member.
  Only chat-room access is grouped.
- **Fixing the project-scoped orchestration and workflow read surfaces.** §4.4 records
  exactly what they expose; the decision to change them is deferred (Q-4, FU-2).
- **Self-service groups.** Only capability #14 holders manage groups (Q-6). Members
  creating their own groups is out of scope.
- **Guest links.** `allow_guest_links` keeps its independent, unrevocable semantics
  (R13.07). A guest link on a group-bound room still admits guests; that is the operator's
  choice, not a hole this dossier closes.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What is the grouping layer, given `agent_groups` (LLM agent groups) and `key_groups` already own the word "group"? | **Member Groups**, tables `member_groups` (with a `project_id` FK) and `member_group_members`; UI label "Member group" / 「成員分組」. | The name states that the thing holds people, so it cannot be confused with the two existing `*_groups` tables. The literal `project_member_groups` / `project_member_group_members` pair was rejected as unreadable; the `project_id` column already carries the scope. |
| Q-2 | How is "bound to groups but `allow_project_members` still on" handled? | Server refuses it: 422 on create and patch, with an SRS Delta amending [R13.04]. | Unlike the useless combination R13.04 already contemplates, this one **silently widens a room the operator just restricted** — the group binding renders, the UI looks correct, and the whole project reads the room. A privacy control must not be defeatable by a checkbox the server accepted. |
| Q-3 | After fixing the `GET /api/projects` over-listing, how is a room with `allow_org_members` still reachable? | Project listing = projects the caller is a member of, plus projects of Orgs they own, plus projects holding at least one room the caller may read. | Navigation is project → workspace → room (`slices/tenancy/routes.ts`, `slices/conversation`); dropping non-member projects outright would strand every `allow_org_members` room with no entry point, narrowing R13.04's `allow_org_members` tier by accident. |
| Q-4 | Are the project-scoped orchestration and workflow read surfaces in scope? | No. §4.4 documents the exact exposure and the decision is deferred. | They have no room-ACL concept at all (`orchestration.py:52`, `workflows.py` module docstring line 4), so bringing them under one would be a third independent stage. §4.4 gives the user what they need to decide later; FU-2 carries it. |
| Q-5 | Should the workspace listing be filtered too? | Yes — list only workspaces holding at least one room the caller may read. | Same rule at all three levels means a user never meets a "visible but empty" container. Implementation cost is near zero once the room-level filter exists. |
| Q-6 | Who manages groups, and who can see group membership? | Management requires capability #14 (`PROJECT_MEMBER_MANAGE`: Org Owner + Project Owner). A non-owner Project Member sees only the groups they belong to, and those groups' member lists. | Matches the teaching case: the teacher forms the groups, a student sees their own team-mates and not the class's group structure. Reuses an existing capability, so the §5.2 matrix gains no row. |
| Q-7 | Does this depend on `2026-08-19-content-area-spacing-and-scroll-contract`, which rewrites `ChatroomSettingsView.vue:227`? | No. `depends_on: []`; whoever builds second rebases. | Verified 2026-08-20: that dossier is still `status: draft`, and `ChatroomSettingsView.vue:227` still reads `<main class="p-6 settings">`. It replaces the template **root element**; this dossier edits the access-flags block at `:320-395` and the composable's flag union. Different regions of one file. Making it a dependency would also park Stage 1's security fix behind a three-deep draft chain (`shared-overlay-and-shell-defects` → `content-area-spacing-and-scroll-contract`). |
| Q-8 | The other open dossiers — do any overlap? | No. Scan recorded in §4.5. | `2026-08-19-chatroom-scroll-and-composer` (Q-11 of its own spec) touches `ChatroomView.vue`, not the settings view. `shared-overlay-and-shell-defects` changes `STable`/`SDropdown`, which this work **consumes** but does not edit. New routes go in `slices/tenancy/routes.ts`, so `app/router.ts` — the file two of those dossiers edit — is untouched. |

## 4. Current State

### 4.1 The tenancy model, and where "group" would sit

Two containers exist: `orgs` / `org_members` and `projects` / `project_members`
(`contexts/tenancy/infrastructure/tables.py:10-76`), each with an `owner` / `member` role
(`contexts/tenancy/domain/models.py:11-18`). A project is owned by a user or an org
(`ProjectOwnerType`, `:34-36`). Chat rooms hang below a project through workspaces:
`workspaces.project_id` → `chatrooms.workspace_id`
(`contexts/conversation/infrastructure/tables.py:10-57`).

`TenancyRoleResolver.roles_for` (`contexts/tenancy/interfaces/role_resolver.py:31-68`)
computes the role set. Two behaviours matter here:

- For a project owned by an Org, it resolves the parent org and grants `ORG_MEMBER` (or
  `ORG_OWNER`) on **every** project of that Org (`:36-49`).
- An Org Owner additionally gets `PROJECT_OWNER` on every project of the Org (`:47-48`,
  implementing R5.03 / R8.08).

Nothing below the project exists. There is exactly one per-user, per-room table today —
`chatroom_guests` (`conversation/infrastructure/tables.py:133-148`) — and rows land in it
only through guest-token enrolment (`app/api/v1/guests.py:34-44`), so it is a link
redemption record, not a membership list a Project Owner can curate.

### 4.2 Room access is already funnelled through one function

`_satisfies_room_flags` (`contexts/conversation/application/access.py:119-145`) is the
single authority: moderator bypass, then `allow_project_owners_only` as an exclusive tier,
then `allow_project_members`, then `allow_org_members`, then `allow_guest_links` against
`chatroom_guests`. `RoomAccess` (`:59-72`) carries the room, project id, resolved roles and
the guest bit; `resolve_room_access` (`:75-116`) assembles it.

Every read path reaches it. Confirmed call sites of `resolve_room_access` /
`ensure_can_read` outside tests: `app/api/v1/chatrooms.py`, `messages.py`, `search.py`,
`exports.py`, `attachments.py`, `tus.py`, `observations.py`, `activities.py`,
`app/api/ws/chatroom.py`, `contexts/conversation/application/observation_service.py`,
`contexts/conversation/application/chat_export_service.py`,
`contexts/knowledge/interfaces/config_access.py`,
`contexts/conversation/interfaces/access.py`.

Two consequences the design leans on:

- A fifth tier added inside `_satisfies_room_flags` reaches messages, search, export,
  attachments, TUS uploads, observations, activities and the chatroom WebSocket without
  touching any of them.
- The WebSocket enforces on handshake **and** re-authorises mid-socket
  (`app/api/ws/chatroom.py:63-68` and `:154-159`), so removing a user from a group drops
  their live socket at the next re-auth window rather than at their next reconnect.

Note that `Role.GUEST` is never emitted by the resolver — guest status travels as
`RoomAccess.is_guest`, deliberately (`access.py:170-177` explains why `decide()` cannot
serve row 19). That is the precedent Stage 2 follows.

### 4.3 The enumeration chain (the defect Stage 1 fixes)

For a user who is an Org Member of Org O but **not** a member of project P ⊂ O:

1. `GET /api/projects` → `ProjectService.list_visible_for_user`
   (`contexts/tenancy/application/project_service.py:111-122`) returns own projects plus
   **all** projects of every org the user belongs to, with no project-membership check.
   Reached from `app/api/v1/projects.py:96`.
2. `GET /api/projects/{P}/workspaces` → `require_membership(project_param=...)`
   (`app/api/v1/workspaces.py:72`) passes, because `roles_for` handed out `ORG_MEMBER`
   for P (§4.1). All workspaces of P are returned.
3. `GET /api/workspaces/{W}/chatrooms` (`app/api/v1/chatrooms.py:242-269`) checks only that
   the resolved role set is non-empty (`:253-260`) and then returns **every** live room in
   the workspace. `_satisfies_room_flags` is never called on this path — it is the one read
   surface in §4.2's list that does not use the funnel.
4. Only on entering a room does `ensure_can_read` (`access.py:148-159`) refuse, because
   `allow_org_members` defaults to false (`tables.py:42`).

So message **content** is isolated and room **existence** is not: names, all four access
flags, `created_by_user_id`, `disclose_observers` and `observers_present` are disclosed
(`ChatroomOut`, `app/api/v1/chatrooms.py:83-108`). Steps 1 and 3 are independent defects;
step 2 is a consequence of R5.03's deliberate inheritance and is **not** changed by this
dossier (see §5, Decision 3).

`ChatroomRepository.list_for_workspace` paginates in SQL
(`conversation/infrastructure/repositories/chatroom_repo.py:202-223`), which matters for
the fix: filtering after a SQL `LIMIT/OFFSET` would return short pages and skip rows.
`list_visible_for_user` already returns everything and lets the route slice
(`projects.py:116`), so only the room listing changes shape.

### 4.4 Cross-group surface inventory (Q-4's evidence)

Everything a group-bound room touches, and whether the new tier covers it:

| Surface | Gate today | After Stage 2 | Residual cross-group exposure |
|---|---|---|---|
| Messages read/send/edit/delete (`messages.py`) | `resolve_room_access` + `ensure_can_read` | covered by the funnel | none |
| Room full-text search (`search.py:50-52`) | `ensure_can_read` | covered | none |
| Room WebSocket + mid-socket re-auth (`ws/chatroom.py:63-68`, `:154-159`) | `ensure_can_read` | covered, including live revocation | none |
| Chat export (`exports.py`, `chat_export_service.py`) | `ensure_can_read` + `export_sender_scope` | covered | none |
| Attachments (`attachments.py`), resumable upload (`tus.py`) | `ensure_can_read` | covered | none |
| Observations / observer surfaces (`observations.py`, `observation_service.py`) | `ensure_can_read` + `is_room_creator` | covered | none |
| Activities (`activities.py`) | `resolve_room_access` | covered | none |
| Chatroom-owned Concept Map, REST + WS + mid-socket watchdog (`knowledge/interfaces/config_access.py:57-65`) | inherits the room ACL | covered | none |
| **Chat-room listing** (`chatrooms.py:242-269`) | role set non-empty — **defect** | fixed in Stage 1 | none after fix |
| **Workspace listing** (`workspaces.py:68-78`) | `require_membership` | filtered in Stage 1 (Q-5) | none after fix |
| **Project listing** (`projects.py:86-117`) | org membership only — **defect** | filtered in Stage 1 (Q-3) | none after fix |
| Workspace- and agent_group-owned Concept Maps (`config_access.py:67-83`) | project membership + `concept_map_enabled` opt-in (R11.17) | unchanged | **By design: project-wide.** A workspace-owned map aggregates across every room in the workspace, groups included. Operators wanting per-group maps must use chatroom-owned maps. |
| Agents, agent tools (`agents.py:391`, `:653`, `:966`) | project membership | unchanged | by design — shared project resources |
| Keys, key groups, search keys, RAG/knowmap configs (`knowmap.py:279`, `:326`) | project membership | unchanged | by design |
| **Workflow definitions, runs, steps** (`workflows.py:332`, `:354`, `:546`, `:569`, `:602`; module docstring line 4 states "project membership for read") | project membership | unchanged | **A definition can name chat rooms** (the linter validates against `list_chatroom_ids_for_project`, `workflows.py:160`), and run/step history is readable by any project member. |
| **Orchestration reads** (`orchestration.py:52-65`, routes at `:209`, `:231`, `:257`, `:278`, `:304`, `:325`) | project membership, id-addressed | unchanged | `AgentInstanceOut` carries `chatroom_id` (`:118`, `:195`). Not enumerable from a room, but reachable: workspace workflow list → run id → `/workflow-runs/{id}/approvals` and `/workflow-runs/{id}/subagents`. Exposes another group's agent activity metadata, not message content. |
| Guest enrolment (`guests.py:34-44`) | token possession | unchanged | by design (R13.05–R13.07: no expiry, no revocation) |
| Notifications | per-user rows (`notification/domain/models.py:22-30`) | unchanged | none. `APPROVAL_HUMAN_REQUESTED` is declared (`:16`) but has no producer outside tests, so there is no room-driven fan-out today. |

The two bolded "unchanged" rows are Q-4's deferred decision. Both are enumerable by any
project member and neither has a room-ACL concept to extend; FU-2 carries them.

### 4.5 Dependency scan (contract Step 3)

Non-`implemented` dossiers on `docs/tasks/BOARD.md`, checked against this task's files:

| Dossier | Status | Overlap |
|---|---|---|
| `2026-08-19-shared-overlay-and-shell-defects` | draft | None. Edits `STable`, `SDropdown`, `ErrorBoundary`, `AppShell.vue`, `app/router.ts`. This task consumes `STable`/`SDropdown` and adds routes via `slices/tenancy/routes.ts`. |
| `2026-08-19-content-area-spacing-and-scroll-contract` | draft | Same file, different region — see Q-7. |
| `2026-08-19-mobile-viewport-and-breakpoints` | draft | None. `AppShell.vue`, `AgentDetailView.vue`. |
| `2026-08-19-chatroom-scroll-and-composer` | draft | None. Its own Q-11 states it touches `ChatroomView.vue` and none of the four padded conversation view roots. |
| `2026-07-19-large-artifacts-silently-dropped` | in-progress | None. Message artifact rendering. |
| `2026-07-07-graphrag-two-axis-redesign` | approved (blueprint) | None. Does not touch room ACL or tenancy membership. |

## 5. Design

### Options considered

**Option A — Member Groups as a room access tier (chosen).** A project-scoped
`member_groups` table, a `member_group_members` join, a conversation-owned
`chatroom_member_groups` binding table, and a fifth boolean `allow_member_groups` on
`chatrooms`. `RoomAccess` gains `in_bound_group: bool`, and `_satisfies_room_flags` gains
one branch. Group membership confers no capability anywhere.

**Option B — a seventh Role.** Add `Role.GROUP_MEMBER` to the permission enum and give it
cells in the matrix. Rejected: §5.1 of the SRS fixes the role set at six with "No custom
roles", it would force a decision for all 26 capabilities where only one question is being
asked, and `Guest` already establishes the "per-resource grant, not a role" pattern
(R5.04, `access.py:170-177`).

**Option C — per-room member lists** (promote `chatroom_guests` to a curated
`chatroom_members`). Rejected: maximum flexibility, but no reusable grouping object — a
teacher would re-pick the same six students for every room of a six-week course — and the
guest semantics that table carries (R13.07: no revocation) are wrong for a managed list.

**Option D for the listing filter — express the room-flag predicate in SQL** so filtering
happens before `LIMIT`. Rejected: it makes a second authority for the flag logic, which is
precisely the drift `_satisfies_room_flags`' own docstring warns about
(`access.py:48-56`). See Decision 2.

### Decision

**1. Member Groups are a room access tier, not a role.** Option A. What is given up: a
group cannot own or restrict anything other than chat rooms, so group-scoped agents or
knowledge bases are not reachable by extending this later without new design. That is
accepted — the request is about who can see which conversation.

**2. The flag logic stays in Python, in one place; the three listings pre-fetch and filter
through it.** `_satisfies_room_flags` remains the sole authority. Each listing gathers what
`RoomAccess` needs in batch (roles once per project, the caller's guest room-ids, the
caller's group ids, each room's bound group ids) and evaluates rooms in Python before
paginating. Given up: the listings read all candidate rooms rather than a filtered page. At
the realistic ceiling for a self-hosted institution (hundreds to a few thousand rooms per
org, each row five booleans and two UUIDs) that is cheap, and it buys a single authority
for a confidentiality rule. The read is explicitly bounded with a logged warning rather
than a silent truncation (AC-5, FU-3).

**3. `roles_for` and `require_membership` are not changed.** Tempting — an Org Member
holding a role on every project of the Org is what lets step 2 of §4.3 through — but that
inheritance is R5.03/R8.08 and it is exactly what makes `allow_org_members` work. The
defect is that **listings** ignore the flags, not that the roles exist. Fixing the listings
leaves an org member able to reach a room that opted into `allow_org_members` and nothing
else. Given up: `GET /api/projects/{P}/workspaces` still answers 200 with a filtered list
rather than 403 for a non-member of P, so "project P exists" remains inferable by a direct
request from an Org Member who guesses the id. Recorded as FU-4.

**4. SoC: tenancy answers "who is this user", conversation answers "which rooms".** The
visibility question spans both contexts and cannot be one SQL statement without a
cross-context join. Instead, tenancy resolves the caller's identity facts (role sets per
project, member-group ids) and passes them as plain values into a conversation facade
method that reads only conversation tables (`chatrooms`, `workspaces`,
`chatroom_member_groups`, `chatroom_guests`). Neither context reads the other's tables, and
the existing precedent is preserved: `access.py` already consumes `TenancyFacade` and
`TenancyRoleResolver` through their interfaces (`access.py:36-37`, `:85`, `:102`).

**5. `allow_member_groups` and `allow_project_members` are mutually exclusive, server-side
(Q-2).** The refusal lives at the API boundary as a 422 on both create and patch, with the
patch evaluated against the merged post-patch state so a two-step widening is refused too.

## 6. Detailed Changes

### Backend

**Migration `0079_member_groups.py`** (latest is `0078_agent_delegated_activity_control`).
Single transaction in both directions, no autocommit block, matching the rule
`tests/unit/test_migration_autocommit_ordering.py` pins and the reasoning `0076` records:

- `member_groups` — `id`, `project_id` FK → `projects.id` `ON DELETE CASCADE`, `name`,
  `created_by_user_id` FK → `users.id` `ON DELETE SET NULL`, `version`, `created_at`,
  `deleted_at`. Unique index on `(project_id, lower(name)) WHERE deleted_at IS NULL`.
  `version` via the existing `smap_bump_version` trigger (`0002_tenancy`, as `0016` does).
- `member_group_members` — PK `(member_group_id, user_id)`, both FKs `ON DELETE CASCADE`,
  `joined_at`.
- `chatroom_member_groups` — PK `(chatroom_id, member_group_id)`, both FKs
  `ON DELETE CASCADE`.
- `chatrooms.allow_member_groups BOOLEAN NOT NULL DEFAULT false`.

Forward-compatible: the new column is defaulted and the new tables are unread by pre-0079
code. **Note for the implementer:** `AdminService.hard_delete_user` issues a raw
`DELETE FROM users`; `created_by_user_id` is therefore `SET NULL`, never `RESTRICT` — see
`0078`'s docstring for what a `RESTRICT` or a partner CHECK does to GDPR erasure.

**`contexts/tenancy`** — owns groups as an extension of project membership:

- `domain/models.py` — `MemberGroup`, `MemberGroupMember` frozen dataclasses.
- `domain/errors.py` — `MemberGroupNotFound`, `MemberGroupNameTaken`,
  `NotAProjectMember` (raised when adding a non-member to a group).
- `infrastructure/tables.py` — `member_groups`, `member_group_members`.
- `infrastructure/repositories.py` — `MemberGroupRepository` following the existing
  repository shape (`_row_to_project` style row mappers, `VersionMismatch` on optimistic
  lock, `NameTaken` from `IntegrityError`).
- `application/member_group_service.py` — create / rename / soft-delete / add member /
  remove member / list-for-project / list-for-user, each emitting an audit event
  (`project.member_group_created`, `.renamed`, `.deleted`, `.member_added`,
  `.member_removed`) via `shared_kernel.audit`, exactly as `project_service` does.
- `application/project_service.py` — `list_visible_for_user` gains the room-visibility
  filter of Decision 2. Its current org loop is an N+1 (`:117-121`); collapse it to one
  `list_by_org` per org batched into a single query while touching it.
- `interfaces/facade.py` — `member_group_ids_for_user(user_id, project_ids)`,
  `member_groups_for_project`, `is_member_group_manager`. These are what conversation
  consumes; no other context touches the tables.
- `application/account_deletion_service.py` / `prepare_hard_delete` — verify the new
  cascades need no extra clearing (the FKs are `CASCADE`/`SET NULL`, so they should not,
  but this must be asserted, not assumed).

**`contexts/conversation`** — owns the binding and the ACL:

- `infrastructure/tables.py` — `chatroom_member_groups`; `chatrooms` gains
  `allow_member_groups`.
- `domain/models.py` — `Chatroom.allow_member_groups`.
- `infrastructure/repositories/chatroom_repo.py` — the new column in
  `_row_to_chatroom`/`create`/`patch`; `bound_group_ids(chatroom_ids)` returning
  `dict[chatroom_id, set[group_id]]`; `list_for_workspace` gains an unpaginated variant for
  the filtered listing (keep the paginated one for any caller that does not filter).
- `application/access.py` — `RoomAccess.in_bound_group: bool`;
  `_satisfies_room_flags` gains one branch between the project-members and org-members
  tiers; `resolve_room_access` resolves the caller's group membership through
  `TenancyFacade`.
- `application/chatroom_service.py` — `ChatroomFlagsPatch.allow_member_groups`; group
  binding read/replace; the mutual-exclusion invariant asserted here as well as at the
  route, so a future caller cannot bypass it.
- `interfaces/facade.py` — `filter_visible_rooms(...)` and
  `project_ids_with_visible_room(...)` taking the caller's identity facts as plain values
  (Decision 4), used by the tenancy project listing and the workspace listing.

**API contract** (`gen:api` rerun required):

- `ChatroomCreateIn`, `ChatroomPatchIn`, `ChatroomOut` gain `allow_member_groups`.
- 422 (RFC 7807, `type: /conversation/room-access-flags-conflict`) when a create or the
  merged post-patch state has both `allow_member_groups` and `allow_project_members`.
- `GET|POST /api/projects/{project_id}/member-groups`
- `GET|PATCH|DELETE /api/member-groups/{group_id}` (`PATCH`/`DELETE` require `If-Match`,
  matching `rename_project` at `projects.py:180-184`)
- `GET|POST /api/member-groups/{group_id}/members`,
  `DELETE /api/member-groups/{group_id}/members/{user_id}`
- `GET|PUT /api/chatrooms/{chatroom_id}/member-groups` (PUT replaces the binding set)
- Router split mirrors `workspaces.py`: a `project_router` for the collection and a
  `group_router` for id-addressed routes.

AuthZ on every new route: management via
`require(Capability.PROJECT_MEMBER_MANAGE, scope_from_path(project_param=...))`; the two
read routes via `require_membership` **plus** the Q-6 narrowing (a non-owner sees only
their own groups) applied in the service, not the route.

### Frontend

- **tenancy slice** — `views/ProjectMemberGroupsView.vue` (group list, create, rename,
  delete, member add/remove), route in `slices/tenancy/routes.ts`, a link from
  `ProjectDetailView.vue`/`ProjectMembersView.vue`, `api/memberGroups.ts`, query keys in
  `queries/index.ts` (`tenancyKeys.memberGroups(projectId)`), strings in
  `locales/en.json` + `locales/zh-TW.json`, and a view test (gate #8).
- **conversation slice** — `ChatroomSettingsView.vue` access section (`:320-395`) gains the
  member-group tier and a bound-group multi-select; `useChatroomSettings.ts` `AccessFlag`
  union (`:20-24`) gains `allow_member_groups`, and `flags` (`:45-53`) the new field;
  `types/index.ts` + `types/schemas.ts` updated. The tier control must make the Q-2
  exclusivity visible **before** the request: choosing "member groups" clears
  `allow_project_members` in the same patch rather than letting the server 422.
  Cross-slice import of the group list is via `@slices/tenancy`'s `index.ts` only
  (`conversation → tenancy` is declared in `SLICE_DEPS`, `eslint.config.js:35`).
- `ChatroomListView.vue` needs no change — the server-side filter is transparent to it.

### Deploy/config

None. No env vars, no Vault paths, no compose changes.

## 7. NFR Checklist

- **i18n** — every new string via `$t()` in both `en.json` and `zh-TW.json`. Watch the
  literal `@` rule: vue-i18n reads `@` as a linked message and only fails in a production
  build, so any email shown in the group member list must go through a binding, never a
  literal.
- **Audit log** — `project.member_group_created` / `.renamed` / `.deleted` /
  `.member_added` / `.member_removed`, and `chatroom.member_groups_bound` carrying the
  before/after group-id sets. Never log names of groups the actor could not otherwise see.
- **Tenant isolation** — every new route resolves the parent project and checks membership
  or capability #14 before returning anything; `member-groups/{id}` routes resolve the
  project from the group row, never trust a client-supplied project id.
- **Error handling UX** — loading / empty / error states on the new view; the 422 renders
  as an inline explanation of the exclusivity rule, not a toast.
- **Performance** — Decision 2's pre-fetch is the only new cost on a hot path
  (`GET /api/projects` is called on nearly every navigation). One query for candidate
  rooms, one for the caller's guest rows, one for bound-group ids, one for the caller's
  group ids. No N+1; the existing N+1 in `list_visible_for_user:117-121` is removed while
  the function is open.

## 8. Security Considerations

This change is entirely about tenant boundaries, so the whole dossier is the security
section. Specific traps:

- **Fail closed on an unresolved group.** A room bound to a group that was soft-deleted
  must grant nothing, not everything. `_satisfies_room_flags` must treat "no live bound
  group containing the caller" as false, and the branch must be ordered so
  `allow_project_owners_only` still short-circuits above it.
- **Never widen by accident.** The new branch must sit **inside** the
  `allow_project_owners_only` early return (`access.py:137-138`), or an owners-only room
  with a stale binding becomes group-readable.
- **Enumeration is confidentiality.** Stage 1's three listings and the Q-6 narrowing of the
  group listing are security controls, not cosmetics. A member of group A learning that
  group B exists, and who is in it, is the leak in a class setting.
- **Mid-socket revocation.** Removing a user from a group must actually drop their live
  socket. The re-auth callback re-runs `resolve_room_access`
  (`ws/chatroom.py:154-159`), so this works **only if** group membership is resolved inside
  `resolve_room_access` and not cached in `RoomAccess` construction elsewhere. Test it.
- **The 422 must not become an oracle.** Refusing the flag combination happens after the
  capability check, so a non-owner probing a room id gets 403, never 422.
- **Audit metadata leakage.** Group names in audit rows are readable by admins only
  (capability #21), which is fine; group names must not appear in any error body returned
  to a caller who cannot list that group.
- **No new plaintext surface.** No provider keys, no tokens, no user input reaching a
  template or a shell. Group names are user input and are stored/returned as text — they
  render through Vue's default escaping, never `v-html`.

## 9. Quality Notes

**Existing debt in the touched files** (record, do not silently fix):

- `useChatroomSettings.ts:41-44` claims "R13.04's server-side auto-correction of the
  sibling flags renders for free". **There is no server-side auto-correction.** No write
  path normalises the flags (`chatroom_service.py:86-107`, `chatroom_repo.py:83-105`);
  what exists is a *read-time* override — `_satisfies_room_flags:137-138` treats
  `allow_project_owners_only` as exclusive — plus UI toggles disabled at
  `ChatroomSettingsView.vue:335` and `:354`. The comment is wrong and this dossier's Q-2
  decision makes the distinction load-bearing, so fixing this comment **is** in scope
  (AC-13).
- `ProjectService.list_visible_for_user:117-121` is an N+1 across orgs. In scope, since
  the function is being rewritten anyway.
- `TenancyRoleResolver.is_chatroom_participant:74-86` raises `NotImplementedError` by
  design. Do **not** be tempted to implement it for group membership; the room ACL lives in
  `conversation/application/access.py` and that docstring says so explicitly.

**Patterns to follow:**

- Room-ACL evaluation: `contexts/conversation/application/access.py` — one authority, no
  hand-inlined copies of a predicate (`:48-56` says why).
- Per-resource grant that is not a role: `chatroom_guests` + `RoomAccess.is_guest`.
- Repository + service + facade layering: `contexts/tenancy/application/project_service.py`
  and `interfaces/facade.py`.
- Migration documentation depth: `0078_agent_delegated_activity_control.py` — state what
  was deliberately **not** constrained and why.
- Optimistic locking + `If-Match`: `app/api/v1/projects.py:180-213`.
- Frontend member management: `ProjectMembersView.vue` + `composables/useMemberActions.ts`
  are directly reusable for the group member table.

**Reuse inventory:**

- `shared_kernel.auth.dependencies`: `require`, `require_membership`, `scope_from_path`,
  `get_role_resolver`, `_raise_forbidden`.
- `shared_kernel.audit.emit` + `AuditEvent`.
- `app.api.v1.deps`: `PaginationParams`, `require_if_match`.
- `contexts.tenancy.interfaces.facade.TenancyFacade` — extend, do not bypass.
- `contexts.conversation.application.access.is_moderator_roles` — the moderator predicate,
  already shared between serialization and enforcement.
- Frontend: `@shared/ui` `STable`, `SBadge`, `SDropdown`, `SFormField`, `SSelect`,
  `SConfirmDialog`, `SEmptyState`; `useMemberActions`, `useEntityLifecycle`,
  `useProjectRole` in the tenancy slice; `styles/member-form.css`.

## 10. Risks and Rollback

| Risk | Mitigation |
|---|---|
| Stage 1 hides a room somebody currently reaches, and it reads as data loss. | The filter is exactly `_satisfies_room_flags`, which already governs opening the room — anything newly hidden was already un-openable. Call it out in the release note. |
| The pre-fetch turns `GET /api/projects` into a slow path on a large org. | Bounded read with a logged warning (AC-5); FU-3 carries the SQL-predicate option if a real deployment hits it. |
| A room ends up bound to groups with `allow_member_groups` false, granting nothing while the UI shows groups. | The settings UI shows the binding list as inert when the tier is off, mirroring how the guest-link card is conditioned on `allow_guest_links` (`ChatroomSettingsView.vue:423`). |
| Migration rollback. | `0079` is reversible: drop the three tables and the column, both directions in one transaction. No data migration and no backfill, so downgrade loses only group definitions — state that in the docstring. |
| The mutual-exclusion 422 breaks an existing client. | No existing room can have `allow_member_groups` set (the column is new and defaults false), so no existing request shape becomes invalid. |

## 11. Acceptance Criteria

**Stage 1 — enumeration follows confidentiality**

- [ ] AC-1: `GET /api/workspaces/{id}/chatrooms` returns only rooms for which
      `_satisfies_room_flags` is true for the caller (admin and moderators unchanged). A
      test proves an Org Member who is not a project member sees an `allow_org_members`
      room and does not see a default room in the same workspace.
- [ ] AC-2: That listing's pagination is correct after filtering — a filtered page of the
      requested size is returned where enough visible rooms exist, and `offset` skips
      visible rooms rather than raw rows.
- [ ] AC-3: `GET /api/projects/{id}/workspaces` returns only workspaces holding at least
      one room visible to the caller.
- [ ] AC-4: `GET /api/projects` returns projects the caller is a member of, projects of
      Orgs they own, and projects holding at least one room visible to them — and no
      others. A test proves an Org Member no longer sees a sibling project with no
      org-visible room.
- [ ] AC-5: The candidate-room read in each of the three listings is bounded, and hitting
      the bound emits a warning log naming what was dropped. No silent truncation.
- [ ] AC-6: `roles_for`, `require_membership` and the four existing flags are behaviourally
      unchanged — the existing access-control test suite passes untouched.

**Stage 2 — Member Groups**

- [ ] AC-7: Migration `0079` applies and downgrades cleanly against a real PostgreSQL, in a
      single transaction each way, and the unique index rejects a duplicate live name in
      one project while permitting the same name in another.
- [ ] AC-8: A Project Owner can create a group, add an existing project member, bind it to
      a room, and set `allow_member_groups`; a member of that group can read and send in
      the room.
- [ ] AC-9: A project member who is in **no** bound group cannot read, send in, search,
      export, download an attachment from, subscribe to the WebSocket of, or **see in the
      listing** a room bound only to another group.
- [ ] AC-10: A Project Owner and an Org Owner of the parent Org both still reach a
      group-bound room, and `ChatroomOut.is_moderator` is true for them.
- [ ] AC-11: `allow_member_groups` + `allow_project_members` in one create, or reachable by
      one patch of an existing room, is refused with 422 and an RFC 7807 body; the room's
      stored state is unchanged.
- [ ] AC-12: Removing a user from a bound group drops their live chatroom WebSocket at the
      next mid-socket re-auth, without a reconnect.
- [ ] AC-13: A non-owner project member listing groups sees only groups they belong to and
      those groups' members; a group they are not in is absent, not empty.
- [ ] AC-14: A room bound only to a soft-deleted group grants access to nobody but
      moderators and admin, and an `allow_project_owners_only` room with a live binding
      still admits only owners.
- [ ] AC-15: A project with zero member groups behaves byte-identically to today —
      `ChatroomOut` gains one field defaulting to false and nothing else changes.
- [ ] AC-16: `useChatroomSettings.ts:41-44`'s stale claim about server-side auto-correction
      is corrected to describe the actual mechanism (read-time exclusivity + disabled
      toggles), and the new tier's exclusivity is stated accurately.
- [ ] AC-17: Deleting a project cascades away its groups, bindings and group memberships;
      hard-deleting a user removes their group memberships without aborting the erasure.
- [ ] AC-18: Gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`,
      `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, and
      `pnpm run check:openapi-drift` after `gen:api`.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-2, AC-3, AC-4 | unit (route + service with a fake resolver) | `backend/tests/unit/` alongside the existing chatroom/tenancy route tests |
| AC-1, AC-4, AC-9 | integration | `backend/tests/integration/` — real Postgres, real membership rows; the enumeration chain of §4.3 replayed end to end as a regression test |
| AC-5 | unit | assert the warning log fires at the bound |
| AC-6 | existing suite, unmodified | `backend/tests/unit/` access-control tests |
| AC-7, AC-17 | db (`pytest.mark.db`) | migration up/down against `SMAP_SCRATCH_DATABASE_URL`; the partial unique index needs real PostgreSQL — the unit tier compiles with `literal_binds` and cannot see it (see `backend/CLAUDE.md`) |
| AC-8, AC-10, AC-14 | unit — full matrix over `_satisfies_room_flags` | one parametrised test enumerating all 5 flags × {moderator, project member, org member, group member, guest, nobody} |
| AC-11 | unit (route) + component | route returns 422 on both create and merged-patch; the settings view clears the sibling flag before sending |
| AC-12 | integration | drive the WS re-auth callback after a group removal |
| AC-13 | unit (service) | owner vs non-owner listing |
| AC-15 | unit | a project with no groups produces identical listings and identical `ChatroomOut` apart from the new field |
| AC-16 | review | the corrected comment |
| AC-18 | CI | per `docs/tasks/README.md`'s Definition of Done in `/build` |
| Frontend | component (Vitest) | `ProjectMemberGroupsView` (gate #8 requires a view test) and the amended `ChatroomSettingsView` / `useChatroomSettings` tests |
| End-to-end | Playwright | `frontend/e2e/` — teacher creates two groups, binds two rooms, and a student in group A cannot see group B's room in the rail |

**Verification note.** Per `docs/tasks/BOARD.md`, the last seven dossiers in this area
shipped with no `db`/`integration` execution and no browser pass because Docker was
unavailable on the implementing host. This dossier's core claim is a **confidentiality**
claim, and reasoning is not evidence for one. AC-1, AC-4, AC-9 and AC-12 must be executed
against a real stack — locally or on CI — before this is called implemented; leave them
unticked rather than claimed.

## 13. SRS Delta

To be applied to `REQUIREMENTS.md` on approval.

**(a) Amend §5.1, inserting after [R5.04] (`REQUIREMENTS.md:171`):**

> - **[R5.06]** Member Group membership (§13.2a) is orthogonal to the role set. Like
>   `Guest`, it is a per-resource grant evaluated by the chat-room access check, never a
>   seventh role, and it confers no capability in the §5.2 matrix. The fixed role set of
>   §5.1 is unchanged.

**(b) Replace §13.2 (`REQUIREMENTS.md:674-683`) with:**

> ### 13.2 Access modes (Q43)
>
> Five composable flags per chat room:
>
> - `allow_org_members` (project-owned + org-owned only)
> - `allow_project_members`
> - `allow_member_groups` (§13.2a — admits the members of every Member Group bound to this
>   room)
> - `allow_project_owners_only` (overrides all of the above; if true, only Project Owners
>   enter)
> - `allow_guest_links` (if true, Guest Link URL is active and shareable)
>
> **[R13.04]** The flags are independently togglable with one exception:
> `allow_member_groups` and `allow_project_members` are mutually exclusive, and the server
> refuses with 422 any create or patch whose resulting state sets both. The exception
> exists because that combination does not merely fail to add meaning — it silently widens
> a room the operator has just restricted to named groups, which no other pair does. Every
> other subset is valid. Semantically useless combinations are handled at read time and in
> the UI rather than by rewriting stored state: `allow_project_owners_only` overrides the
> other tiers wherever access is evaluated, and the UI disables the tiers it overrides.

**(c) Insert a new §13.2a after §13.2:**

> ### 13.2a Member groups (optional, per project)
>
> - **[R13.28]** A Project may define **Member Groups**: named subsets of its own members,
>   used to scope chat-room visibility below the project level. They are optional, and a
>   project defining none behaves exactly as one defined before this section existed. A
>   Member Group belongs to exactly one Project; a user may belong to any number of groups
>   within a project; only current Project Members may be group members, and losing project
>   membership removes the user from that project's groups.
> - **[R13.29]** A chat room may be bound to any number of Member Groups of its parent
>   project. When `allow_member_groups` is set, membership of any bound group satisfies the
>   room's access check for read and for send, exactly as `allow_project_members` does for
>   project membership. Bindings on a room whose `allow_member_groups` is unset grant
>   nothing, and a binding to a deleted group grants nothing.
> - **[R13.30]** Project Owners, and Org Owners of the parent Org, reach every room in the
>   project regardless of group binding (R8.08, R5.03). Group membership never narrows an
>   existing grant; it only widens a room that has opted into the tier.
> - **[R13.31]** Creating, renaming and deleting a Member Group, changing its membership,
>   and binding it to a room all require capability #14 (Invite/remove Project Member). A
>   Project Member who is not an Owner may read the groups they belong to and those groups'
>   member lists, and must not be able to learn that other groups in the project exist.
> - **[R13.32]** Enumeration follows confidentiality. A chat-room listing returns only
>   rooms the caller may read under §13.2; a workspace listing returns only workspaces
>   containing at least one such room; a project listing returns only projects the caller
>   is a member of, projects of Orgs they own, and projects containing at least one such
>   room. A caller must not be able to learn the name or the existence of a room,
>   workspace, or project they cannot open.

## 14. Open Questions

- **OQ-1** — Should a Member Group be able to own a Concept Map, the way an
  `agent_group` and a `workspace` can (R11.17, `config_access.py:75-80`)? Not needed for
  the stated goal, and a workspace-owned map already aggregates across groups (§4.4). Worth
  revisiting once groups are in use.
- **OQ-2** — Should the group member list expose display names rather than email addresses?
  `ProjectMembersView.vue` shows email today; in a classroom, a student seeing team-mates'
  emails may be more disclosure than intended. Deferred: it is a change to the existing
  member surfaces too, not just the new one.

## 15. Deviation Log

Appended by `/build`. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- **FU-1** — Onboarding, decided with the user on 2026-08-20 and deliberately not in this
  dossier: (a) the invite-create response carries a copyable accept link so a closed
  deployment does not need working SMTP (today the plaintext token is emailed and never
  returned — `orgs.py:375-383`); (b) an org/project owner can add an **existing** user by
  exact email without the invite round trip (no such endpoint exists — membership rows are
  written only by `InviteService._finalize_acceptance`, `invite_service.py:383-398`);
  (c) an admin can create an account with a forced first-login password change (no such
  endpoint — `admin_users.py` has ban/delete/promote only), optionally with an
  invite-only registration mode. Needs its own dossier.
- **FU-2** — Q-4's deferred decision: `workflows.py` (definitions, runs, steps) and
  `orchestration.py` (approvals, instructions, sub-agent instances carrying `chatroom_id`)
  are readable by any project member and have no room-ACL concept. §4.4 records the exact
  fields and the reachability path. Decide whether group isolation must extend to them.
- **FU-3** — If a deployment makes Decision 2's bounded pre-fetch measurably slow, the
  fallback is a SQL predicate for the listings plus a parity test asserting it agrees with
  `_satisfies_room_flags` over the full flag × role matrix. Do not add the SQL copy
  without that test.
- **FU-4** — Decision 3's residue: `GET /api/projects/{id}/workspaces` still answers 200
  (filtered) rather than 403 to an Org Member who is not a project member, so a guessed
  project id confirms the project exists. Closing it means revisiting the R5.03 role
  inheritance, which is a larger question than this dossier.
