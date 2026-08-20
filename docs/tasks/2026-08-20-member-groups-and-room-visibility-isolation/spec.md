---
type: feature
status: implemented
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

- [x] AC-1: `GET /api/workspaces/{id}/chatrooms` returns only rooms for which
      `_satisfies_room_flags` is true for the caller (admin and moderators unchanged). A
      test proves an Org Member who is not a project member sees an `allow_org_members`
      room and does not see a default room in the same workspace.
      *Verified by `tests/unit/test_visible_room_ids.py` (the flag/role matrix, asserted
      against `ensure_can_read` itself) and `tests/unit/test_listing_visibility_routes.py`
      (the route serves the filter's output and nothing else). See D-3 for why the
      org-member scenario is asserted at the predicate rather than through the route.
      **Executed against PostgreSQL** by
      `tests/integration/test_room_listing_visibility_db.py`, where the named scenario —
      an org member seeing the `allow_org_members` room and not the default one in the
      same workspace — runs against real rows, and one test asserts the listing and the
      open path agree room-for-room for all four principals (D-8).*
- [x] AC-2: That listing's pagination is correct after filtering — a filtered page of the
      requested size is returned where enough visible rooms exist, and `offset` skips
      visible rooms rather than raw rows.
- [x] AC-3: `GET /api/projects/{id}/workspaces` returns only workspaces holding at least
      one room visible to the caller.
- [x] AC-4: `GET /api/projects` returns projects the caller is a member of, projects of
      Orgs they own, and projects holding at least one room visible to them — and no
      others. A test proves an Org Member no longer sees a sibling project with no
      org-visible room.
      *Unit: `test_listing_visibility_routes.py` and `test_tenancy_services.py`.
      **Executed against PostgreSQL** by `test_room_listing_visibility_db.py`, which also
      pins D-6's fix — a user holding only a `project_members` row, with no `org_members`
      row anywhere, is a candidate and is directly visible.*
- [x] AC-5: The candidate-room read in each of the three listings is bounded, and hitting
      the bound emits a warning log naming what was dropped. No silent truncation.
      *`tests/unit/test_room_listing_bound.py`, both halves: the repository fetches
      `limit + 1` so a full page is not mistaken for the end, and the facade warns only
      when it actually truncated.*
- [x] AC-6: `roles_for`, `require_membership` and the four existing flags are behaviourally
      unchanged — the existing access-control test suite passes untouched.
      *`test_room_access.py`, `test_role_resolver_chatroom_failclosed.py`,
      `test_deps_assert_project_membership.py` and `test_config_access.py` are unmodified
      and green. One unrelated existing test was removed with the code it covered — D-2.*

**Stage 2 — Member Groups**

- [x] AC-7: Migration `0079` applies and downgrades cleanly against a real PostgreSQL, in a
      single transaction each way, and the unique index rejects a duplicate live name in
      one project while permitting the same name in another.
      *`alembic upgrade head` → `downgrade -1` → `upgrade head` executed against
      PostgreSQL 16. The index is covered three ways in `test_member_groups_db.py`:
      case-insensitive rejection, free in another project, free again after a soft delete.*
- [x] AC-8: A Project Owner can create a group, add an existing project member, bind it to
      a room, and set `allow_member_groups`; a member of that group can read and send in
      the room.
- [x] AC-9: A project member who is in **no** bound group cannot read, send in, search,
      export, download an attachment from, subscribe to the WebSocket of, or **see in the
      listing** a room bound only to another group.
      *Asserted at the funnel, both halves: the db tests prove `ensure_can_read` refuses
      and the listing omits the room. The other surfaces named here are the fifteen call
      sites of that same predicate inventoried in §4.2 — they are covered by construction,
      not by fifteen separate tests, which is the property the design was chosen for.*
- [x] AC-10: A Project Owner and an Org Owner of the parent Org both still reach a
      group-bound room, and `ChatroomOut.is_moderator` is true for them.
- [x] AC-11: `allow_member_groups` + `allow_project_members` in one create, or reachable by
      one patch of an existing room, is refused with 422 and an RFC 7807 body; the room's
      stored state is unchanged.
      *Including both two-step orderings, and asserting `rooms.update` was never called.*
- [ ] AC-12: Removing a user from a bound group drops their live chatroom WebSocket at the
      next mid-socket re-auth, without a reconnect.
      *Left unticked deliberately. The **mechanism** is proven against the database —
      after removal, `resolve_room_access` denies, which is exactly what
      `ws/chatroom.py:154-159` re-runs — but no test drives a live socket across a
      revocation. See D-12; the honest claim is "the re-auth will refuse", not "the socket
      was observed to drop".*
- [x] AC-13: A non-owner project member listing groups sees only groups they belong to and
      those groups' members; a group they are not in is absent, not empty.
- [x] AC-14: A room bound only to a soft-deleted group grants access to nobody but
      moderators and admin, and an `allow_project_owners_only` room with a live binding
      still admits only owners.
- [x] AC-15: A project with zero member groups behaves byte-identically to today —
      `ChatroomOut` gains one field defaulting to false and nothing else changes.
      *The 7022-test unit tier passes with eight fixture updates for the new field and no
      behavioural change; a room with the tier off ignores its bindings, and a listing of
      such rooms never queries the group tables at all.*
- [x] AC-16: `useChatroomSettings.ts:41-44`'s stale claim about server-side auto-correction
      is corrected to describe the actual mechanism (read-time exclusivity + disabled
      toggles), and the new tier's exclusivity is stated accurately.
- [x] AC-17: Deleting a project cascades away its groups, bindings and group memberships;
      hard-deleting a user removes their group memberships without aborting the erasure.
- [x] AC-18: Gates green — `pytest -q` (7022 unit, 30 db), `ruff check . && ruff format
      --check .`, `mypy .` (955 files), `pnpm lint`, `pnpm typecheck`, `pnpm test` (1205),
      `pnpm build`, `check:boundaries-enforced`, `check:type-coverage`.
      *`check:openapi-drift` could not run on this host — D-14.*

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

**Post-implementation `/code-review` (2026-08-20).** Six findings, all verified real, five
fixed here and one already recorded as FU-8. The review is the reason to read this section
before trusting the two gates above it.

- **D-16 — R13.32 was enforced on one branch of `GET /api/projects` and bypassable by a
  query parameter.** `?scope=org&id=<org>` ran `list_by_org` behind a bare org-membership
  check, so any org member received the name of every project in the org — the exact
  disclosure `_list_visible` claims to close. Not theoretical: `ProjectListView.vue:52` and
  `OrgProjectSwitcher.vue:37` both build that request, so the project list's per-org tab
  was served by the unfiltered branch. `_list_visible` now takes an optional `org_id` that
  narrows the result without widening the candidate set, and the org branch is a narrowing
  of the same answer rather than a second way to ask. Admin keeps the unfiltered view.
  **This is a miss in my own Stage 1 security trace**, which followed this endpoint through
  one branch and read past the other two on the same screen. The lesson is structural: a
  filter applied at a branch, rather than at the function every branch shares, is a filter
  waiting to be walked around.
- **D-17 — `list_candidates` did not filter `projects.deleted_at`.** `resolve_room_access`
  refuses a room whose project is soft-deleted, and the role resolver reads projects with
  `include_deleted=True`, so a member of a deleted project was handed its rooms by the
  listing and then 404'd on open — the listable-but-unopenable split the shared predicate
  exists to prevent. Third join added; the compile assertion now pins all three filters.
- **D-18 — the candidate ceiling could delete whole containers, not just rooms.** The limit
  applies to the union across containers ordered globally, so a workspace whose rooms
  sorted after the cut was judged on none of them and vanished. Truncation now splits the
  container set and re-asks, down to a single container; only a single container over the
  ceiling on its own answers from a partial read. The warning text said "shortened list"
  for a failure that was larger.
- **D-19 — `toggleGroup` cleared its guard before the read-back landed.** The binding
  endpoint replaces rather than patches, so a second click inside the window rebuilt its
  payload from the stale set and dropped the group the first click added. The applied set
  now goes straight into the query cache; the failure path refetches.
- **D-20 — two CSS classes the group picker was written against did not exist.**
  `access-row--stacked` and `group-picker` had no rules, so the checkbox list rendered
  squeezed alongside its own label with default bullets.
- The review's N+1 finding on `visible_room_ids` is FU-8, already recorded before the
  review ran; it is unchanged and still open.

**Stage 2 (2026-08-20).**

- **D-9 — the security gate found a live hole in the tier this task added, and it is the
  most important thing in this log.** `OrgService.remove_member` (`org_service.py:282`)
  deletes `project_members` rows straight at the repository layer and knows nothing about
  member groups, so it bypassed the cleanup wired into `ProjectService.remove_member`. A
  user removed from an org kept their `member_group_members` row; the tier asks only
  whether the user is in a bound group; they went on reading and sending in the room after
  losing every role in the project. **Fixed at the ACL, not by adding a second cleanup
  call**: `group_ids_for_user` now joins to `project_members`, so lapsed standing grants
  nothing regardless of which path removed it — including paths written later. That is the
  same shape that already makes a deleted group inert. The org-side cleanup was added as
  well so the rows do not rot. Verified by removing the join and watching the regression
  test fail.
- **D-10 — the quality gate found the room-binding route importing another context's
  application layer.** `chatrooms.py` reached `MemberGroupService` directly. It now uses
  `TenancyFacade.get_member_group`, which also let the route refuse a missing or
  soft-deleted group instead of binding a row that grants nothing while looking as though
  it does.
- **D-11 — `_is_project_member` accepts more than a `project_members` row.** §6 did not
  say what "current project member" means for someone whose project standing comes from
  R5.03 rather than a membership row. The service admits an Org Owner of the parent org and
  the owner of a user-owned project, because refusing to put a teacher in a group they
  already moderate would be a rule invented here rather than one the SRS asks for. Note the
  asymmetry with D-9's fix: such a user can be *added* to a group but is filtered out of
  `group_ids_for_user` — with no effect, since a moderator clears every tier first.
- **D-12 — AC-12 is unticked on purpose.** The revocation mechanism is proven against a
  real database; no test drives a live WebSocket across a group removal. FU-13.
- **D-13 — no CHECK constraint pairs the two exclusive flags.** §6 did not ask for one, but
  it is the obvious partner to the 422 and its absence is deliberate: a CHECK would reject
  pre-0079 rows during a mixed-version deploy window and turn a future data fix into a
  migration. Recorded in `0079`'s docstring so the next reader does not "complete" it.
- **D-14 — `check:openapi-drift` did not run.** Its script needs `python` on PATH inside
  git-bash, which this host does not provide. The spec was exported from the backend and
  the client generated from that spec, so they agree by construction; CI is the check.
  `openapi.json` was written from Python rather than by shell redirection, because
  PowerShell adds a UTF-8 BOM that `core.autocrlf` does not normalise and that has already
  failed this gate once (BOARD.md).
- **D-15 — a settings-view test clicked its toggle by index.** Adding a tier between two
  existing rows broke it. Rewritten to find the row by its label, so the next tier does not
  silently repoint the click at a different switch.

**Stage 1 (2026-08-20).**

- **D-1 — the SoC split landed in the route, not inside a facade method.** §5 Decision 4
  said tenancy would resolve the caller's identity facts and pass them as plain values into
  a conversation facade method. In the code, `visible_room_ids`
  (`conversation/application/access.py`) resolves roles itself through
  `TenancyRoleResolver`, and the *route* (`projects.py::_list_visible`) composes the two
  facades. Reason: `resolve_room_access` already calls the tenancy resolver from that exact
  module (`access.py:102`), so the "identity facts as parameters" design would have been a
  second, different convention for the same question in the same file — and passing an
  authorization input as a parameter is the shape that lets a future caller supply the
  wrong one. The property Decision 4 actually wanted is preserved: no context reads
  another's tables, and `ProjectService.list_candidates_for_user` returns the membership
  split rather than the answer, so tenancy still knows nothing about chat rooms.
- **D-2 — `ChatroomService.list_for_workspace` was deleted, and its unit test with it.**
  §6 said the repository would gain an unpaginated variant and keep the paginated one. The
  repository method is kept (the bootstrap seeder still uses it, `seed.py:179`), but the
  *service* wrapper was a pure passthrough whose only caller was the route being changed.
  Leaving it would have been dead code of exactly the kind §9 says not to add.
  `TestChatroomList` in `tests/unit/test_conversation_services.py` covered only that
  wrapper and was removed with it — the only existing test this task touched.
- **D-3 — the org-member scenario named in AC-1 is asserted at the predicate, not through
  the route.** The route tests inject the filter's result, so they cannot also prove what
  the filter decides; `test_visible_room_ids.py` proves that, for every flag/role
  combination, against `ensure_can_read` itself. Asserting the same thing twice through a
  mock would have tested the mock. The genuinely end-to-end version of AC-1 is the
  integration test §12 asks for, which has **not** been run — see D-5.
- **D-4 — `list_by_orgs` and `owned_org_ids` are new repository methods.** §6 mentioned
  only collapsing the N+1; these are how. Both are single-query batch forms of methods that
  already existed.
- **D-6 — the AC-4 gap the self-audit found, fixed in scope.** The candidate set was built
  from "owns it" and "belongs to its org" only, so a user holding just a `project_members`
  row in an org-owned project — exactly what accepting a project invite produces
  (`invite_service.py:396`) — never appeared. They could open the project by id but could
  not find it. The defect predates this task; AC-4 says the listing returns projects the
  caller is a member of, so it was fixed rather than deferred. Membership is now the third
  candidate source.
- **D-7 — two findings from the quality gate, both self-inflicted, both fixed.** The route
  imported `Project` from `contexts.tenancy.domain.models`, which is below the facade line
  a route may cross; it now comes through `project_service`, the door this file already
  uses for `ProjectMemberRole` and `ProjectOwnerType`. And `ConversationFacade._visible_ids`
  used a lazy import justified by a circular dependency that **does not exist** — verified
  by import, and `tenancy/interfaces/facade.py` imports nothing from conversation. A
  comment asserting a constraint that isn't real is worse than the indirection it excused;
  both are gone.
- **D-5 — the datastore tier was initially not executed; closed the same day.** §12 states
  that AC-1 and AC-4 must be run against a real stack. They were first ticked on the unit
  tier alone, with the gap recorded rather than hidden; D-8 records closing it.
- **D-8 — the datastore tests landed in the `db` tier, not the `integration` tier, and
  they were run.** §12 said "integration". That is the wrong tier: `ci.yml` states in
  its own comment that `backend-integration` tests "exercise the HTTP/middleware boundary
  with fakes, so they need no DB/Redis", and anything needing a real datastore carries
  `pytest.mark.db` and runs in `backend-db` inside the compose network. The new module is
  marked `db` accordingly and will be collected by `pytest -q -m db` there.

  Three things the run produced that reasoning had not.

  **The mutation probes.** A `db` test that has only ever been seen green is the exact
  hazard `BOARD.md` records for `SMAP_SCRATCH_DATABASE_URL` — tests that had never executed
  anywhere while the job reported passed. So both halves were deliberately broken and the
  suite re-run: replacing `_satisfies_room_flags` with an unconditional allow killed 6
  tests, and dropping the `workspaces.deleted_at` predicate from `list_candidates` killed
  the soft-deleted-workspace test *and* the unit-tier compile assertion. Both were then
  reverted.

  **Migrations 0077 and 0078 have now been applied somewhere.** `BOARD.md` records both as
  never applied in any environment. `alembic upgrade head` ran all 78 cleanly against
  PostgreSQL 16. That is not a substitute for their own atomicity tests, but the "has never
  been applied anywhere" note attached to those two dossiers is now out of date.

  **Reproducing the database** takes one container, no compose stack, and does not touch
  the ports the full stack uses:

  ```
  docker run -d --name smap_pg_local -e POSTGRES_USER=smap -e POSTGRES_PASSWORD=smap \
    -e POSTGRES_DB=smap_test -p 5433:5432 \
    -v "$PWD/deploy/compose/postgres/init:/docker-entrypoint-initdb.d:ro" \
    pgvector/pgvector:0.8.0-pg16
  cd backend && SMAP_APP_ENV=test \
    SMAP_DB_DSN=postgresql+asyncpg://smap:smap@localhost:5433/smap_test alembic upgrade head
  ```

  Then run pytest with the same two env vars. The rest of the `db` tier still fails on this
  host — `test_knowmap_neo4j_replacement` and `test_workflow_join_epoch` resolve the
  `neo4j` and `redis` hostnames, which only exist inside the compose network — so those
  remain CI's to run. That is a pre-existing environment limitation, not a regression:
  nothing in this task touches either subsystem.

- **D-16 (post-close) — a `/code-review` run on 2026-08-20, after this dossier reached
  `implemented`, found three live defects in the group-binding UI. All three are fixed,
  each with a mutation-probed test; the dossier stays `implemented` and no new dossier was
  opened (the user's call).**

  1. **A deleted group wedged the room's picker permanently.** `GET
     /api/chatrooms/{id}/member-groups` returned raw binding rows "live and stale alike"
     while the PUT refused a deleted id, and the settings view sends the GET's list
     straight back on the next edit — so deleting a bound group made every later toggle
     422, with no UI path to clear the stale binding because the picker only renders live
     groups. The read now reports only live bindings. The stored row is still left alone
     (the ACL ignores it, and the repository still does not read tenancy's `deleted_at`);
     what changed is that a dead binding is no longer handed to a client that will send it
     back. Both routes now go through one facade method, so the read and the write cannot
     disagree again.
  2. **Switching the group tier off closed the room to everyone.** `setFlag` paired
     R13.04's exclusive flags only when switching one *on*. Turning `allow_member_groups`
     off sent it alone, and since enabling it had already cleared
     `allow_project_members`, the room landed with no member tier at all — every
     non-moderator silently lost read and send. The comment above `setFlag` claimed the
     room is never momentarily open to nobody, which was true only of the on-path. The
     project tier is now restored in the same patch, but only when nothing else would
     still admit members: an org-wide or owners-only room is narrower on purpose, and a
     guest link does not count as a member tier.
  3. **A group checkbox kept the user's click after a failed bind.** The input is
     uncontrolled, so the browser flips `el.checked` and `:checked` re-applies only when
     the rendered value changes — after a failure the confirmed set is unchanged, Vue
     patches nothing, and the box shows a binding the server rejected under a toast saying
     it failed. A nonce in the key forces the re-render an unchanged value cannot.

  Defects 1 and 3 compound: the wedge from 1 makes every toggle fail, and 3 then displays
  each of those failures as if it had worked.

## 16. Follow-ups

- **FU-1** — Onboarding, decided with the user on 2026-08-20 and deliberately not in this
  dossier: (a) the invite-create response carries a copyable accept link so a closed
  deployment does not need working SMTP (today the plaintext token is emailed and never
  returned — `orgs.py:375-383`); (b) an org/project owner can add an **existing** user by
  exact email without the invite round trip (no such endpoint exists — membership rows are
  written only by `InviteService._finalize_acceptance`, `invite_service.py:383-398`);
  (c) an admin can create an account with a forced first-login password change (no such
  endpoint — `admin_users.py` has ban/delete/promote only), optionally with an
  invite-only registration mode. **Opened 2026-08-20 as
  `2026-08-20-onboarding-without-smtp`**, with one correction that narrowed it: the in-app
  invite inbox already works without mail for an invitee who has an account, so only the
  unregistered invitee and the missing admin-provisioning route were real gaps. Consent is
  preserved at both levels — no endpoint writes a membership row on someone else's behalf.
- **FU-2** — Q-4's deferred decision: `workflows.py` (definitions, runs, steps) and
  `orchestration.py` (approvals, instructions, sub-agent instances carrying `chatroom_id`)
  are readable by any project member and have no room-ACL concept. §4.4 records the exact
  fields and the reachability path. **Opened 2026-08-20 as
  `2026-08-20-orchestration-room-scoped-reads`**, which found that the workflow half is a
  straight [R14.10] violation rather than a new rule, and that the approval half is
  exploitable today against an `allow_project_owners_only` room, independently of grouping.
- **FU-3** — If a deployment makes Decision 2's bounded pre-fetch measurably slow, the
  fallback is a SQL predicate for the listings plus a parity test asserting it agrees with
  `_satisfies_room_flags` over the full flag × role matrix. Do not add the SQL copy
  without that test.
- **FU-4** — Decision 3's residue: `GET /api/projects/{id}/workspaces` still answers 200
  (filtered) rather than 403 to an Org Member who is not a project member, so a guessed
  project id confirms the project exists. Closing it means revisiting the R5.03 role
  inheritance, which is a larger question than this dossier.
- **FU-5** — ~~The datastore tests for AC-1, AC-3 and AC-4 do not exist.~~ **Closed
  2026-08-20**: `tests/integration/test_room_listing_visibility_db.py`, 14 tests, executed
  against a real PostgreSQL 16 and green. See D-8 for what running them proved and for the
  one-line recipe that reproduces the database. AC-12 remains open because it belongs to
  Stage 2 (there is no group membership to revoke yet).
- **FU-7** — `GET /api/projects` paginates an unordered result. Neither `list_by_user`,
  `list_by_org`, `list_by_orgs` nor `list_by_ids` carries an `ORDER BY`, so PostgreSQL may
  return rows in a different order between two requests and a page boundary can drop or
  repeat a project. Pre-existing; the new filtering does not change it, but a stable sort
  key is a prerequisite for anyone who later trusts this endpoint's paging.
- **FU-8** — Raised by the security gate as a MEDIUM under resource exhaustion, and it
  compounds FU-6. `project_ids_with_visible_room` resolves roles once per distinct project
  in the candidate set, so `GET /api/projects` for a caller who is a plain org member of an
  org with N projects costs roughly N role resolutions (about 3 queries each) on the
  endpoint the SPA hits most. The information is knowable without asking — every project in
  the undecided set is undecided *because* the caller is only an org member of it — but
  encoding that in the caller means passing an authorization fact as a parameter, which is
  what D-1 deliberately refused. The right fix is a request-scoped memo inside
  `TenancyRoleResolver`, which also retires FU-6 and is a shared-kernel change beyond this
  task's scope. Two unbounded `IN` lists on the same path (`list_by_orgs` over every org
  the caller belongs to, and the undecided project ids) are the same finding's smaller half.
- **FU-9** — The group read path hides a group's existence from a caller who may not see
  it (404 via `_resolve_readable`), but `PATCH` and `DELETE` on the same id answer 403,
  which discloses that the id exists. No practical exploit — group ids are UUIDv4 — but the
  two halves of one resource should not disagree about what they admit to. Route the write
  paths through `_resolve_readable` too.
- **FU-10** — `MemberGroupRepository.list_for_project` returns every group in the project
  and the route paginates in Python. Same shape as FU-8 and bounded by how many groups a
  project actually has, but it is an unbounded read on a route any project member can call.
- **FU-11** — The group member list returns bare `user_id`s. The settings view can name
  them because it also holds the project roster, which is fetched for managers only, so a
  non-manager viewing their own group sees raw UUIDs where a manager sees emails. Either
  serialize a display name on the endpoint or make the roster readable to group members.
- **FU-12** — `GET /api/chatrooms/{id}/member-groups` requires capability #14, so a
  non-manager opening a group-scoped room's settings page fires a request that 403s. The
  page still renders; the failed request is noise, not a defect, but the picker section
  should not mount for a caller who cannot manage bindings.
- **FU-13** — D-12: no test drives a live chatroom WebSocket across a group revocation.
  The re-auth callback and the predicate it calls are both covered; the wiring between
  them is covered by inspection only, and AC-12 stays unticked until something drives it.
- **FU-6** — Two callers now resolve the caller's project roles twice per request: the
  chatroom listing route computes `is_moderator` from its own `roles_for` call, and
  `visible_room_ids` resolves the same roles again inside the facade. Correct but wasteful.
  Threading the resolved roles through was rejected for the reason in D-1; a request-scoped
  memo on the resolver would fix it without moving an authorization input into a parameter.
