---
type: bugfix
status: in-progress
created: 2026-07-22
requirements: [R5.04, R5.05, R13.14, R13.17]
depends_on: []
---

# Chat export authorization (matrix row 19) and stale export polling

`depends_on: []` is justified: this dossier pairs audit findings F-2 and F-16, which
`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:671` routes here together.
Neither finding participates in the F-1 coupling cluster called out at `findings.md:83-85`
(F-1, F-8, F-11, F-13). F-2 is confined to the export request path
(`backend/app/api/v1/exports.py`, `backend/contexts/conversation/application/chat_export_service.py`,
`backend/contexts/conversation/infrastructure/repositories/message_repo.py`) and F-16 to the
export polling path (`frontend/src/shared/composables/usePolling.ts`,
`frontend/src/slices/conversation/composables/useChatroomExport.ts`). The two share only the
export feature surface, not a code dependency, so either may be built first. There is,
however, a hard sequencing constraint *inside* this dossier: Q-1 through Q-4 in §3 must be
answered before any of the F-2 work begins, because the fix cannot be written without them.

## 1. Summary

Permission-matrix row 19 (`Capability.CHAT_EXPORT`) restricts Org Members, Project Members
and Guests to exporting their own messages, but no route or service ever consults it. Any
caller who can *read* a chatroom, including a Guest who enrolled through a permanent guest
link, can POST `/api/chatrooms/{id}/export` and receive a complete archive of every message
by every participant, including raw markdown, sanitized HTML, the full edit history of each
message, and attachment object paths. The capability is defined, mapped and unit-tested, yet
is dead code at runtime: the authorization rule that exists to prevent cross-participant
disclosure has never been wired to the feature it governs. Separately and much less
seriously, the export modal's status poller has no per-job cancellation, so a poll belonging
to a previous export job can overwrite the modal after the user has opened it to configure a
new one, presenting the earlier export's download as though it were the one just requested.

## 2. Observed vs Expected

### F-2 (major, authorization / data disclosure)

- **Observed** — `Capability.CHAT_EXPORT` is declared at
  `backend/shared_kernel/auth/permissions.py:66` and mapped at `:234-240` as
  `ORG_OWNER -> ALLOW`, `ORG_MEMBER -> OWN_ONLY`, `PROJECT_OWNER -> ALLOW`,
  `PROJECT_MEMBER -> OWN_ONLY`, `GUEST -> OWN_ONLY`. The only other reference anywhere in
  `backend/` is `backend/tests/integration/test_permission_matrix.py:102`. `Outcome.OWN_ONLY`
  is interpreted only inside `decide()` at `permissions.py:336-339`, which requires an
  explicit call, and `create_export` (`backend/app/api/v1/exports.py:89-118`) never calls it:
  its entire authorization is `resolve_room_access` + `ensure_can_read` at `:97-102`.
  The worker-side re-check is the same pair (`chat_export_service.py:77-82`), and the message
  window it then reads is room plus optional date bounds only
  (`chat_export_service.py:90-95` calling `message_repo.py:272-301`), with no sender predicate
  in the `WHERE` clause at `message_repo.py:289-297`. The manifest built at
  `chat_export_service.py:100-131` therefore carries `content_md`, `content_html`, the full
  `edits[]` array and each attachment's `minio_path` for every sender.
- **Expected** — `REQUIREMENTS.md:197` (matrix row 19) reads
  `| 19 | Export chat history | ✓ | ✓ | ∘ (own messages) | ✓ | ∘ (own messages) | ∘ (own messages) |`
  against the legend at `REQUIREMENTS.md:175`, "`∘` allowed only on resources the user owns".
  `REQUIREMENTS.md:207` (R5.05) requires that "All authorization MUST be enforced server-side
  in a single `permissions` service." An Org Member, Project Member or Guest export must
  therefore be narrowed, and the narrowing must derive from the matrix rather than from a
  second hand-rolled rule. The *precise* meaning of "own messages" is unresolved and is
  Q-1 below; the fix must not be written until it is answered.

### F-16 (minor, UI state)

- **Observed** — `usePolling` (`frontend/src/shared/composables/usePolling.ts:60-72`) exposes
  `start(key)` but only a global, terminal `stop()` that sets `disposed = true` at `:65`;
  there is no per-key cancel. `useChatroomExport` (`.../composables/useChatroomExport.ts:22-25`)
  assigns `exportJob.value` from `onResult` unconditionally, for whatever key ticked, and
  `runExport` at `:27-36` calls `exportPoll.start(job_id)` without stopping any in-flight key.
  The controller is created in the view's setup (`ChatroomView.vue:595`) and so outlives the
  modal, while `openExport()` (`ChatroomView.vue:774-777`) clears `exportJob` and opens the
  modal without cancelling anything.
- **Expected** — `docs/UI/07-conversation.md:805-843` specifies one modal lifecycle per
  export: the user configures a job, the modal transitions to progress, and the download
  offered at `:836-842` is for *that* job. A tick belonging to a superseded job must not
  render into the modal. `docs/UI/12-shared-patterns.md` §5.3 and §7.2 are the intent source
  the audit cites at `findings.md:469`.

## 3. Clarifications

These are blocking for F-2. An export that silently narrows generates support load; one that
silently widens is a disclosure. Neither outcome may be chosen by the implementer.

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | For a member or guest, does "own only" mean (a) only messages the caller sent, or (b) all messages the caller is entitled to read (that is, the whole room, with row 19 degrading to a read check)? | **(a) — only messages the caller sent** (user, 2026-07-24) | The SRS is textually explicit for (a): `REQUIREMENTS.md:197` annotates the circle as "(own messages)", and the same table deliberately uses a *different* marker where a room-wide read grant is meant, "per room ACL" in row 17 (`REQUIREMENTS.md:195`). The code mapping agrees: `CHAT_SEND` maps to `Outcome.ROOM_ACL` (`permissions.py:223-229`) while `CHAT_EXPORT` maps to `Outcome.OWN_ONLY` (`permissions.py:234-240`). Against that, R13.17 (`REQUIREMENTS.md:678`) describes the feature as exporting "a chat room's history", and under (a) a member's export of an agent chatroom contains their own prompts and not one agent reply, since agent messages carry `sender_type=AGENT` (`domain/models.py:92-93`). Reading (a) is what the requirement says; reading (b) is what the feature is for. Only the user can settle which the SRS intended. |
| Q-2 | May a Guest export at all? | **No — guests may not export at all** (user, 2026-07-24). The UI doc wins; Delta-2 resolves in favour of `docs/UI/07-conversation.md:1273`. | Direct conflict between two intent sources. `REQUIREMENTS.md:197` grants `GUEST` the `∘ (own messages)` cell and `permissions.py:239` encodes it. `docs/UI/07-conversation.md:1273` states "Guest sessions have limited permissions: no chatroom settings, no export, no agent binding, no admin actions". The frontend today implements neither: the header export control at `ChatroomHeader.vue:72-74` and `:193` has no guest predicate. Note the resolver interaction under Q-4 makes "deny guests entirely" the accidental default of a naive fix. |
| Q-3 | If Q-1 resolves to (a), are agent and system messages included in a narrowed export? | **Yes — own user messages plus all `AGENT` and `SYSTEM` messages; only other *users* are excluded** (user, 2026-07-24) | There is no stored link from an agent message back to the user whose turn produced it. `Message` (`backend/contexts/conversation/domain/models.py:89-99`) carries only `sender_type` and `sender_id`; for an agent message `sender_id` is the agent, and nothing records the prompting user. Any "my messages plus the replies to them" shape would require deriving that relationship, which is new behavior, not a bugfix. The three candidate shapes are: own user messages only; own user messages plus all `SYSTEM` messages; own user messages plus all `AGENT` and `SYSTEM` messages (that is, exclude only *other users*). |
| Q-4 | Should already-generated exports be purged as part of this fix? | **Yes — purge as the final deployment step, after AC-1..AC-13 are confirmed in production** (user, 2026-07-24) | See §7 "Data-repair position". The residual window is bounded but real, and we cannot enumerate who exported what because the required audit action is not emitted (§6). |

## 4. Reproduction

### F-2

Preconditions: an org O with project P, workspace W and chatroom R; R has
`allow_guest_links = true`; participants Alice (Project Member) and Bob (Project Member) have
each sent messages in R, with at least one message edited so `message_edits` is non-empty and
at least one message carrying an attachment; a guest link for R exists.

1. Guest user G enrols through the guest link, becoming a row in `chatroom_guests`. G holds
   no `org_members` or `project_members` row, so `TenancyRoleResolver.roles_for`
   (`backend/contexts/tenancy/interfaces/role_resolver.py:31-68`) returns an empty frozenset
   for G, and `RoomAccess.is_guest` is true (`application/access.py:84-92`).
2. G calls `POST /api/chatrooms/{R}/export` with `{"format": "json", "date_range": "all"}`.
3. `resolve_room_access` + `ensure_can_read` (`exports.py:97-102`) pass:
   `_satisfies_room_flags` (`access.py:96-122`) returns true on the
   `room.allow_guest_links and access.is_guest` branch at `:122`. The route returns 202 with a
   `job_id`.
4. The Arq task `chat_export` (`backend/app/workers/tasks/conversation.py:216-261`) validates
   only that the enqueued args match the stored Redis state (`:233-245`), then builds the
   manifest.
5. G polls `GET /api/exports/{job_id}`. Once `status == "ready"`, `exports.py:131-137` returns
   a 15-minute presigned MinIO URL.
6. Observed: the downloaded `manifest.json` contains every message in R by Alice and Bob,
   each with `content_md`, `content_html`, the complete `edits[]` history including
   `edited_by_user_id`, and each attachment's `minio_path`
   (`chat_export_service.py:100-131`).

The same steps with G replaced by an Org Member who satisfies `allow_org_members` reproduce
identically; the guest variant is used here only because it is the widest gap between granted
role and granted data.

### F-16

Preconditions: chatroom R with enough history that a PDF export takes longer than 3 seconds.

1. Open R, click the header export control, choose PDF, submit. `runExport`
   (`useChatroomExport.ts:27-36`) creates job A and calls `exportPoll.start(A)`.
2. Close the modal (`ChatroomView.vue:239` emits `close`, setting `exportOpen = false`). The
   poller is untouched; it lives in the view's setup scope (`ChatroomView.vue:595`) and only
   `onScopeDispose` stops it (`usePolling.ts:70`).
3. Within 3 seconds, reopen export. `openExport()` (`ChatroomView.vue:774-777`) sets
   `exportJob = null`, so `ChatroomExportModal.vue:10` renders the configuration form.
4. A's next tick resolves, `onResult` assigns `exportJob.value` (`useChatroomExport.ts:22-24`),
   `v-if="job"` flips, and the form is replaced by A's progress state.
5. When A completes, the modal shows "Export ready" with a download button bound to A's URL
   (`ChatroomExportModal.vue:14-28`), for A's format and date range, which the user believes
   is the export they were about to configure.

Deterministic given step 3 occurs inside one poll interval (`intervalMs` default 3000,
`usePolling.ts:32`; `useChatroomExport.ts:19-25` does not override it).

## 5. Root Cause Analysis

### F-2

Causal chain:

1. `Capability.CHAT_EXPORT` is given a row in `_MATRIX` (`permissions.py:234-240`) whose
   member and guest cells are `Outcome.OWN_ONLY`.
2. `Outcome.OWN_ONLY` has exactly one interpretation site, `decide()` at
   `permissions.py:336-339`, which compares `scope.resource_owner_user_id` against
   `principal.user_id`. Nothing else in the codebase reads the outcome.
3. `decide()` is reached only through an explicit call or through the `require(...)`
   dependency factory (`shared_kernel/auth/dependencies.py:73-95`).
4. `create_export` (`exports.py:89-118`) declares no `require(...)` dependency and makes no
   `decide()` call. Its authorization is `ensure_can_read` (`exports.py:102`).
5. `ensure_can_read` (`access.py:125-136`) is a *read* gate over the four room flags. By
   construction it answers "may this caller see this room", not "how much of it may this
   caller take away".
6. `ChatExportService.build_and_upload_export` re-runs the same read gate
   (`chat_export_service.py:77-82`) and then calls `messages.all_for_chatroom`
   (`:90-95`), whose predicate list (`message_repo.py:289-297`) is chatroom, not-deleted, and
   the optional date bounds. No sender predicate exists in the signature
   (`message_repo.py:272-279`).
7. Symptom: the manifest contains every sender's messages.

**Root cause**: link 4. The export route never asks the permission matrix the question the
matrix was written to answer. Correcting link 4 (and the parameter it must then thread through
links 6 and 7) prevents the symptom; correcting anything downstream of it would be a patch on
a missing check.

**Aggravating factors, not root causes:**

- `message_repo.all_for_chatroom` has no sender-filter parameter
  (`message_repo.py:272-279`), so even a route that decided correctly has nothing to pass. This
  makes the correct fix a three-layer change rather than a one-line guard, which is a plausible
  reason the check was skipped originally.
- The export job record cannot carry a narrowing decision. `ExportJobState`
  (`export_service.py:42-57`) has fields for format and date bounds but none for sender scope,
  and `_store` / `get` (`export_service.py:111-151`) serialize exactly those fields. The
  worker (`tasks/conversation.py:216-261`) therefore has no way to learn what the API decided.
- `application/access.py:1-9` claims in its module docstring that "Permission-matrix rows 17
  (chat.send), 19 (chat.export), 20 (message.delete) resolve as `ROOM_ACL`". This is false for
  rows 19 and 20: `permissions.py:234-247` maps both to `OWN_ONLY`, and only `CHAT_SEND` is
  `ROOM_ACL` (`permissions.py:223-229`). A reader auditing the export path is told by that
  docstring that `ensure_can_read` *is* the row-19 enforcement. It is not.

### F-16

1. `usePolling` is documented as keyed (`usePolling.ts:11-12`, "keyed: `start(key)` may be
   called for several keys concurrently") and the design is correct for its other consumer,
   concurrent GraphRAG builds.
2. But cancellation was only ever needed at scope teardown, so `stop()` was written as a
   global kill switch that sets `disposed = true` permanently (`usePolling.ts:64-68`) and is
   wired to `onScopeDispose` (`:70`).
3. `useChatroomExport` has a genuinely single-slot consumer: one `exportJob` ref
   (`useChatroomExport.ts:17`) written by `onResult` for any key (`:22-24`).
4. A keyed producer feeding a single-slot consumer with no per-key cancel means the newest
   `start()` does not displace the previous key; both remain live and race for the slot.
5. `openExport()` clears the slot but cannot cancel the producer (`ChatroomView.vue:774-777`).

**Root cause**: link 2. `usePolling` offers no way to cancel one key. Everything downstream is
a correct consequence of that gap. Link 3 is an aggravating factor: even with cancellation
available, `onResult` writing unconditionally would still need a key guard for robustness.

## 6. Blast Radius and Sibling Suspects

### Blast radius (F-2)

- **Confidentiality, every chatroom with more than one participant.** Reach equals the set of
  callers passing `ensure_can_read`, which per `_satisfies_room_flags` (`access.py:96-122`)
  is: any project member where `allow_project_members`, any org member where
  `allow_org_members`, and any enrolled guest where `allow_guest_links`. Rooms with
  `allow_project_owners_only` are unaffected in practice, since every caller who passes that
  gate holds an `ALLOW` cell in row 19 anyway (`permissions.py:235,237`).
- **Data taken, per message**: raw markdown, sanitized HTML, `metadata`, version, timestamps,
  the complete prior-content edit history with `edited_by_user_id`, and each attachment's
  `filename`, `mime`, `size_bytes` and `minio_path` (`chat_export_service.py:100-131`). The
  edit history is the sharpest item: it exposes content a user deliberately revised away,
  which is not visible in the room UI to anyone.
- **Volume**: capped at 50 000 messages per job (`chat_export_service.py:36`), unbounded in
  the number of jobs (the route increments `EXPORT_JOBS` at `exports.py:116` but applies no
  per-caller quota there).
- **Data already written**: every export object generated to date under the over-broad rule.
  Retention is analyzed in §7.
- **No forensic trail.** `REQUIREMENTS.md:844` lists `message.exported` among the required
  Chat audit actions. A repo-wide grep for `message.exported` across `backend/` returns zero
  matches. We therefore cannot enumerate, after the fact, who exported which room or when.
  This is why Q-4 cannot be answered analytically.

### Security posture for the fix

This is an authorization defect with a disclosure consequence, so the fix carries constraints
beyond "make the check exist":

- **Must not weaken.** `ensure_can_read` must remain in the path, unchanged, at both
  `exports.py:102` and `chat_export_service.py:82`. Row 19 narrows *within* a room the caller
  may read; it is not a substitute for the read gate. A refactor that replaces the read gate
  with a matrix call would delete the four-flag room confidentiality tier that
  `access.py:125-131` exists to enforce, and would be a strictly worse state than today.
- **Must not weaken (worker side).** The worker re-derives `is_admin` from the database
  precisely so a stale enqueued claim cannot elevate (`chat_export_service.py:70-76`), and
  `tasks/conversation.py:233-245` refuses to trust enqueued args against stored state. The
  narrowing decision must be re-derived on the worker side under the same discipline, not read
  from Redis as an authority. Redis is server-only today, but the existing code treats it as
  untrusted and the fix must not be the first place that stops.
- **What an over-broad fix would expose.** Wiring row 19 by calling
  `decide(principal, Capability.CHAT_EXPORT, Scope(chatroom_id=...), resolver)` and treating
  a truthy `Decision` as "export everything" would change nothing at all for members (their
  cell would resolve `OWN_ONLY`, fail the ownership comparison at `permissions.py:337`, and
  fall through to deny) while leaving the disclosure intact for owners. Worse, passing
  `resource_owner_user_id = principal.user_id` to make the `OWN_ONLY` branch pass would make
  every member's `decide()` return `allowed=True` with no narrowing applied anywhere, which is
  exactly today's behavior wearing a compliance badge. The `Decision.outcome` field
  (`permissions.py:273-285`) is what must drive the filter, not `Decision.allowed`.
- **What an over-narrow fix would break.** Under Q-1 reading (a), a member's export of an
  agent chatroom contains no agent output at all. Shipping that without an explicit user
  decision converts a disclosure bug into a silent feature regression.
- **`check-security` referral.** Yes, run it in parallel rather than as the post-hoc
  Definition-of-Done gate. Its AuthZ and cross-room-leakage dimensions target exactly this
  defect class, and the sibling sweep below is the kind of finding it is built to
  cross-check. Two specific asks for that pass: (i) confirm no *other* conversation read
  surface bulk-exports across senders, and (ii) confirm the presigned-URL flow at
  `exports.py:131-137` is not reachable by a non-owner (it is gated at `:129-130` on
  `state.owner_user_id`, which reads correct, but a second opinion on job-id guessability is
  warranted given `job_id` is a `uuid4` at `export_service.py:68`).

### Sibling sweep: capabilities defined and mapped but never referenced

This is the systemic form of F-2. Every `Capability` member was grepped across `backend/`,
excluding `shared_kernel/auth/permissions.py` itself and `backend/tests/`.

| Capability | Production references | Verdict |
|---|---|---|
| `CHAT_EXPORT` (`permissions.py:66`) | none | **CONFIRMED - this defect.** Only `permissions.py:66,234` and `tests/integration/test_permission_matrix.py:102`. |
| `MESSAGE_DELETE` (`permissions.py:67`) | none as a symbol; a prose reference at `app/api/v1/messages.py:5` | **CLEARED, with a caveat.** Row 20's semantics are re-implemented inline at `messages.py:468-473`: `is_author = msg.sender_id == principal.user_id and msg.sender_type.value == "user"`, then `if not (principal.is_admin or access.is_moderator or is_author): _raise_forbidden(...)`. That is a faithful rendering of row 20 (`permissions.py:241-247`) against `RoomAccess.is_moderator` (`access.py:47-49`). The comment at `messages.py:468-470` states the duplication is deliberate. Behaviorally correct; structurally it is a second copy of the matrix and therefore an R5.05 ("single `permissions` service") drift risk. Recorded as FU-1. |
| `AUDIT_VIEW`, `USER_BAN`, `USER_DELETE_ANY`, `USER_READ_ANY` (`permissions.py:69-72`) | none | **CLEARED.** All four have empty matrix rows (`permissions.py:249-252`), meaning deny for every non-admin role, and `decide()` grants admins unconditionally at `:304-305`. Enforcement is `require_admin` on the admin routers (`app/api/v1/admin_deps.py:15`, applied at `admin_users.py:82,103,132,154,173,194,221,239,263`, `admin_projects.py:58,180`, `admin_rate_limits.py:52,82`, and a local variant at `admin_ip_bans.py:40,50,70,103`). An empty row plus a dedicated admin dependency is equivalent enforcement, not a gap. |
| `KEY_VIEW_PLAINTEXT` (`permissions.py:45`) | none | **CLEARED by design.** Universal deny, short-circuited ahead of the admin bypass at `permissions.py:301-302`, with the corresponding test at `test_permission_matrix.py:58-59`. It is enforced by the absence of any endpoint, which is the correct implementation of "no one, ever". |
| `KEY_UPLOAD`, `KEY_DELETE_OWN`, `KEY_DELETE_OTHER_IN_PROJECT`, `KEY_VIEW_USAGE_PROJECT`, `KEY_CONFIGURE` | `project_keys.py:82,122,124,160`; `key_groups.py:182,243,244`; `search_keys.py:86,112,138,161` | **CLEARED - wired.** |
| `ORG_CREATE`, `ORG_DELETE`, `ORG_OWNER_MANAGE`, `ORG_MEMBER_MANAGE` | `orgs.py:167,249,266,342,213,322,363` | **CLEARED - wired.** |
| `PROJECT_CREATE_UNDER_ORG`, `PROJECT_CREATE_UNDER_USER`, `PROJECT_DELETE`, `PROJECT_MEMBER_MANAGE` | `projects.py:139,141,221,243,303,328,354` | **CLEARED - wired.** |
| `RESOURCE_CREATE_EDIT` | `agents.py`, `rag.py`, `graphrag.py`, `knowmap.py`, `skills.py`, `chatrooms.py`, `workspaces.py`, `projects.py:189`, `agent_workspace.py:62` (25+ sites) | **CLEARED - wired.** |
| `CHAT_CREATE` | `chatrooms.py:223`; `workflows.py:123` | **CLEARED - wired.** |
| `CHAT_SEND` | `chatrooms.py:616` | **CLEARED - wired.** |
| `GUEST_LINK_MANAGE` | `chatrooms.py:581` | **CLEARED - wired.** |
| `PROMPT_STUDIO_ORG_MANAGE` | `prompt_studio.py:519` | **CLEARED - wired.** |
| `SKILL_ORG_MANAGE` | `skills.py:696,1254` | **CLEARED - wired.** |

**Conclusion of the sweep**: `CHAT_EXPORT` is the *only* capability in the 26-row matrix that
is both non-trivially mapped and entirely unenforced. Every other apparent orphan is either
enforced through an equivalent dedicated mechanism (`require_admin`), enforced by construction
(universal deny), or duplicated correctly inline (`MESSAGE_DELETE`). F-2 is not the tip of a
pile of identical holes, which is worth stating positively: the systemic problem is not
*many* unwired capabilities but the *absence of any mechanism that would have caught one*.
§8 adds that mechanism.

### Adjacent confirmed gaps found during the sweep

- **`message.exported` audit action is never emitted.** `REQUIREMENTS.md:844` lists it as a
  required Chat audit action; a repo-wide grep for `message.exported` across `backend/`
  returns zero matches. Confirmed. In scope for this fix (§7), because an authorization change
  that leaves the privileged operation unaudited is half a control.
- **`access.py:1-9` docstring is factually wrong about rows 19 and 20.** Confirmed above.
  In scope, because it actively misleads the next reader of this exact code path.
- **Frontend shows the export control to guests unconditionally.** `ChatroomHeader.vue:72-74`
  (desktop) and `:193,199` (mobile overflow) emit `export` with no role or guest predicate,
  against `docs/UI/07-conversation.md:1273`. Per R5.05 (`REQUIREMENTS.md:207`) frontend
  visibility is advisory only, so this is not a security defect. Whether to hide it depends on
  Q-2; recorded as FU-2.

### Blast radius and siblings (F-16)

- **Blast radius**: bounded to the same user, same room, and that user's own earlier export.
  No cross-user or cross-tenant consequence. The wrong download is one the caller was already
  entitled to.
- **Sibling suspect: GraphRAG build polling**, the other `usePolling` consumer named at
  `usePolling.ts:2-3` and `:11-12`. **CLEARED.** That consumer is genuinely multi-key by
  design ("building multiple GraphRAG configs at once", `usePolling.ts:12`) and routes
  `onResult` by key rather than into a single slot, which is the pattern `usePolling` was
  built for. The defect arises specifically from a single-slot consumer, and export is the
  only one.
- **Sibling suspect: other single-slot async writers in the conversation slice.**
  **CLEARED with evidence** for the message paths: `useChatroomSocket.ts` guards its async
  writes with generation counters (`replayGeneration` at `:67,92,95`; `activationGeneration`
  at `:68,118-127`) per `findings.md:519`. The one unguarded case there is a separate audit
  finding (F-19, `findings.md:513-527`) and is routed to
  `docs/tasks/2026-07-22-reconnect-reconciliation/`, not to this dossier.

## 7. Fix Design

### F-2, part 1: make the matrix the decision-maker

The correction restores the missing link (§5 link 4) rather than filtering the output. The
route must obtain a *narrowing decision* from the permission matrix and thread it through the
job record to the query predicate.

**Why not the obvious wiring.** The natural instinct is
`Depends(require(Capability.CHAT_EXPORT, scope_from_path(chatroom_param="chatroom_id")))`.
That does not work, and understanding why is load-bearing for the design:

1. `Scope(chatroom_id=...)` alone yields no roles. `TenancyRoleResolver.roles_for`
   (`role_resolver.py:31-68`) reads `org_members` and `project_members` only; it branches on
   `scope.org_id` and `scope.project_id` and ignores `chatroom_id` entirely. With only a
   chatroom id, `decide()` hits `if not roles: return Decision.deny(...)` at
   `permissions.py:324-325` and denies everyone below admin.
2. **The resolver never returns `Role.GUEST`.** `roles_for` can emit `ORG_OWNER`,
   `ORG_MEMBER`, `PROJECT_OWNER` and `PROJECT_MEMBER` (`role_resolver.py:44-61`) and nothing
   else. Guest status lives in `chatroom_guests` and is surfaced only by
   `ChatroomGuestRepository.is_guest` through `RoomAccess.is_guest`
   (`access.py:84-92`). Consequently the `GUEST` cell of row 19 (`permissions.py:239`) is
   unreachable through `decide()` no matter what scope is supplied. A `decide()`-based fix
   would silently deny all guest exports, which is a decision on Q-2 taken by accident.
3. `Outcome.ROOM_ACL` is not an escape hatch either: its resolver hook
   `is_chatroom_participant` raises `NotImplementedError` by deliberate design
   (`role_resolver.py:74-86`), with the comment explicitly directing ROOM_ACL routes to
   `conversation.application.access` instead.

**The design that follows from that evidence.** Mirror the pattern already validated for the
sibling row: row 20 is enforced inline against `RoomAccess` at `messages.py:468-473`, for
exactly these reasons. Introduce a single narrowing helper in the conversation application
layer, next to the other room-scoped ACL logic:

```
# contexts/conversation/application/access.py
def export_sender_scope(access: RoomAccess, *, principal: Principal) -> ExportSenderScope
```

returning `ALL` when `principal.is_admin or access.is_moderator` (matching the `ALLOW` cells
at `permissions.py:235,237`, where `is_moderator` is `PROJECT_OWNER or ORG_OWNER`,
`access.py:47-49`) and the narrowed value otherwise, per the Q-1/Q-2/Q-3 answers. Placing it
in `access.py` keeps the SoC boundary the module already documents at `:7-8`, and keeps it
callable from both the route and the worker.

**Why this corrects rather than masks.** Masking would be filtering the manifest after
`all_for_chatroom` returns, or hiding the export control in the UI. Both leave the query
authorized to read every sender and leave `Capability.CHAT_EXPORT` dead. This design instead
(i) makes the authorization decision at the point of authorization, before any privileged read
is issued, (ii) pushes the decision into the SQL predicate so the over-broad rows are never
loaded into process memory, and (iii) makes the row-19 cells reachable, so the matrix stops
being decorative. The `CHAT_EXPORT` enum member and its row are retained as the single
declarative statement of intent, and `export_sender_scope` is documented as their one
interpreter, exactly as `messages.py:468-470` documents itself for row 20.

### F-2, part 2: threading and re-derivation

- **Repository**: add an optional sender predicate to `all_for_chatroom`
  (`message_repo.py:272-279`), appended to the `conditions` list at `:289-297`. Under Q-1(a)
  the predicate combines `sender_type` and `sender_id`; the exact shape is fixed by Q-3.
  Default `None` preserves the existing behavior for the admin and moderator path, so the
  signature change is additive.
- **Job state**: add a sender-scope field to `ExportJobState` (`export_service.py:42-57`) and
  to `_store` / `get` (`export_service.py:133-151`, `:111-130`), so the record describes the
  export that was authorized. `_replace` (`export_service.py:154-167`) must carry it through
  unchanged, as it already does for `export_format` and the date bounds at `:163-165`.
- **Worker**: `ChatExportService.build_and_upload_export` must **re-derive** the scope from
  the database, not read it from the job record. It already re-fetches `is_admin`
  (`chat_export_service.py:70-76`) and already computes `RoomAccess`
  (`chat_export_service.py:77-82`), so `export_sender_scope(access, principal=principal)` is
  a one-line addition on data it holds. The job-record field is then a description used by
  `tasks/conversation.py:233-245`-style defence in depth: if the re-derived scope is *wider*
  than the recorded one, fail the job rather than widen it. This preserves the existing
  no-trust posture and means a compromised Redis cannot widen an export.
- **Audit**: emit `message.exported` (`REQUIREMENTS.md:844`) at the point the manifest is
  successfully uploaded, recording chatroom id, requesting user, format, resolved date bounds
  and the resolved sender scope. Never the message content or the presigned URL.

### F-2, part 3: data-repair position

**Position: this is a bounded data-repair obligation, and the decision to act on it is Q-4.**

What is retained and for how long:

- Export artifacts are written to the `exports` MinIO bucket
  (`chat_export_service.py:247-264`), which is provisioned with a one-day object lifecycle:
  `smap/bootstrap/minio_init.py:154-156`, "MinIO lifecycle is day-granular; 1 day == 24 hours
  (§21.5)", matching `REQUIREMENTS.md:1384` ("`exports` (lifecycle: 24-hour expiration)").
- Job state lives in Redis with a matching TTL, `_JOB_TTL_SECONDS = 24 * 3600`
  (`export_service.py:28`), set on every write (`export_service.py:147-151`). The rationale is
  documented at `export_service.py:9-14`.
- Retrieval requires the job id and is owner-gated: `exports.py:129-130` rejects any caller
  who is not `state.owner_user_id` and not admin. The URL minted at `:131-137` expires in 15
  minutes.

**Therefore**: no *new* party can reach a historical over-broad export. The residual exposure
is (i) an original requester re-downloading their own over-broad archive within the 24-hour
window, and (ii) presigned URLs already minted, live for at most 15 minutes. Beyond 24 hours
the server-side artifact is gone. Anything already downloaded to a user's disk is outside our
control and outside repair by definition.

The recommended action, subject to Q-4, is a one-time purge at deploy: delete all objects under
the `exports` bucket and all `chat_export:*` keys from Redis
(`export_service.py:38-39` defines the key shape), collapsing the residual window from up to
24 hours to zero. The cost is that in-flight legitimate exports fail and must be re-run, which
is a 202-and-retry inconvenience, not data loss. The counter-argument is that the purge is
unnecessary given the exposure is already bounded and owner-gated.

The honest caveat that the user needs in order to answer Q-4: because `message.exported` is
never emitted (§6), we cannot produce a list of who exported which room historically, so
"notify affected participants" is not an option we can offer. If the user's posture requires
notification, the only truthful notice is a room-level one. That is a policy call, not an
engineering one.

### F-16

Two changes, at the root cause and at the aggravating factor:

1. **`usePolling`: add per-key cancellation.** Replace the single `timers: Set` at
   `usePolling.ts:34` with a per-key timer map, and add `cancel(key: string)` to
   `PollingController` (`usePolling.ts:23-26`). `cancel` clears that key's pending timer and
   marks the key inactive so an in-flight `fetcher` promise resolving after cancellation is
   dropped rather than delivered: the check belongs alongside the existing `if (disposed)`
   guards at `:49` and `:55`. Global `stop()` (`:64-68`) keeps its current
   terminal semantics and its `onScopeDispose` wiring (`:70`), which is correct for teardown.
2. **`useChatroomExport`: one active key.** Track the active job id and have `runExport`
   (`useChatroomExport.ts:27-36`) cancel the previous key before starting the new one. Guard
   `onResult` (`:22-25`) so it writes `exportJob.value` only when the ticking key is the active
   one. Expose a `reset()` that cancels the active key and nulls `exportJob`, and call it from
   `openExport()` (`ChatroomView.vue:774-777`) in place of the bare `exportJob.value = null`.

**Why this corrects rather than masks.** Clearing `exportJob` on modal close, or gating the
`onResult` write on `exportOpen`, would both stop the visible symptom while leaving a
cancelled job's poller running for up to `maxAttempts` ticks (60 at `useChatroomExport.ts:20`,
that is up to three minutes of pointless requests). The keyed poller advertises concurrent keys
in its own contract (`usePolling.ts:11-12`) and simply lacks the cancel half of that contract;
adding it completes the abstraction rather than working around it, and leaves the GraphRAG
consumer strictly better off.

## 8. Regression Test Plan

Failing tests first, in this order. /build writes each test, observes the stated failure, then
implements the corresponding fix.

### T-1 (backend, new file) `backend/tests/unit/test_capability_wiring.py`

The systemic guard, and the test that would have caught F-2 at the moment it was introduced.

- `test_every_mapped_capability_is_enforced_somewhere` walks `Capability`
  (`permissions.py:43-78`), skips the documented enforced-by-other-means set
  (`KEY_VIEW_PLAINTEXT` and the four admin-only rows, each with the §6 evidence cited inline
  as the reason for exemption), and asserts every remaining member appears at least once under
  `backend/app/` or `backend/contexts/` outside `shared_kernel/auth/permissions.py`. The
  exemption set is a hard-coded frozenset so that adding a new capability without wiring it
  fails the test rather than silently joining the exemptions.
- **Fails today** with `CHAT_EXPORT` in the unenforced set: the grep evidence in §2 shows zero
  production references. Note `MESSAGE_DELETE` also has zero *symbol* references, so the
  assertion must accept the enforcement site rather than the symbol; the pragmatic form is to
  require either a symbol reference or an explicit registry entry naming the enforcing
  `path:line`, which forces the duplication in `messages.py:468-473` to be declared rather
  than merely present. That registry entry is also FU-1's paper trail.

### T-2 (backend, new file) `backend/tests/unit/test_export_authz.py`

The negative tests. These are the load-bearing assertions of this dossier, not supplementary
coverage: each one describes a disclosure that is live in production today.

- `test_guest_export_is_narrowed` (or `test_guest_export_is_denied`, per Q-2): a principal
  with no org/project roles and `RoomAccess.is_guest = True` in a room with
  `allow_guest_links = True` requests an export. Assert the resolved sender scope is the
  narrowed value, and that the predicate handed to `all_for_chatroom` excludes other senders.
  **Fails today**: `exports.py:97-102` performs no narrowing at all, so no scope value exists
  to assert on and `all_for_chatroom` is called with sender-blind kwargs
  (`chat_export_service.py:90-95`).
- `test_project_member_export_excludes_other_users_messages`: seed a room with messages from
  the caller and from a second user; run `build_and_upload_export` as the first user with
  `PROJECT_MEMBER` roles; assert the manifest's `messages` array contains only the caller's
  ids. **Fails today**: `message_repo.py:289-297` applies no sender predicate, so both
  messages are serialized at `chat_export_service.py:96-131`.
- `test_project_member_export_excludes_other_users_edit_history`: same setup, with an edited
  message from the second user; assert no `old_content_md` from that user appears anywhere in
  the manifest. **Fails today**: `chat_export_service.py:98,111-118` walks
  `edits.list_for_message` for every returned row unconditionally. This assertion is separate
  from the previous one because the edit history is the highest-sensitivity item in the
  payload and must not regress independently of the message body.
- `test_project_member_export_excludes_other_users_attachment_paths`: assert no `minio_path`
  belonging to another sender's attachment appears. **Fails today**:
  `chat_export_service.py:99,119-129` emits `minio_path` per row unconditionally.
- `test_project_owner_export_is_unnarrowed`: a `PROJECT_OWNER` (so `is_moderator` true,
  `access.py:47-49`) exports and receives all senders' messages. **Passes today** and must
  keep passing: this is the over-narrow guard for row 19's `ALLOW` cells
  (`permissions.py:235,237`), and it is the assertion that catches an implementer who
  over-corrects.
- `test_admin_export_is_unnarrowed`: admin bypass, matching `permissions.py:304-305` and the
  re-derivation at `chat_export_service.py:70-76`. **Passes today**, guards the same edge.
- `test_worker_refuses_to_widen_beyond_recorded_scope`: construct a job record whose recorded
  sender scope is narrower than what the worker re-derives, and assert the job fails rather
  than exporting the wider set. **Fails today**: the field does not exist
  (`export_service.py:42-57`) and no such comparison is made
  (`tasks/conversation.py:233-245` compares only chatroom and owner).
- `test_export_emits_message_exported_audit`: assert the audit action fires on successful
  upload with the resolved scope, and that neither message content nor the presigned URL
  appears in the audit payload. **Fails today**: grep for `message.exported` across `backend/`
  returns zero matches.

### T-3 (backend, existing file) `backend/tests/unit/test_chat_export_service.py`

The existing suite asserts the read-gate behavior (module docstring at `:1-6`: "ACL check
(admin pass, non-admin with access, non-admin denied)") and inspects
`all_for_chatroom.call_args` for the date window at `:378` and `:522`. Extend those two
call-arg assertions to also assert the sender predicate. **Fails today**: the kwarg does not
exist in the signature (`message_repo.py:272-279`). The existing tests at `:106,163,237,308,360,406,453,503`
must continue to pass unchanged, which is the guard that the additive signature change did not
alter default behavior for the moderator path.

### T-4 (backend, existing file) `backend/tests/integration/test_permission_matrix.py`

No behavioral change expected. `test_matrix_shape_is_26x6` (`:43-47`) and the
`_EXPECTED_ALLOW` sweep including the `CHAT_EXPORT` entry (`:102-108`) must still pass after
the fix, confirming the matrix table itself was not edited to make the wiring easier. If Q-2
resolves to "guests may not export", this file's `CHAT_EXPORT` entry at `:107` and
`permissions.py:239` change together, and that change must be visible here rather than hidden
in the route.

### T-5 (frontend, new file) `frontend/src/shared/composables/__tests__/usePolling.test.ts`

- `test: cancel(key) stops only that key`: start keys A and B with fake timers, cancel A,
  advance past `intervalMs`, assert `fetcher` was called for B and not for A, and that
  `onResult` never fired for A. **Fails today**: `PollingController` (`usePolling.ts:23-26`)
  exposes no `cancel`, so the test does not compile.
- `test: a fetch already in flight for a cancelled key does not deliver`: resolve a pending
  `fetcher` promise for A after `cancel(A)`, assert `onResult` was not called. **Fails
  today**: `tick` at `usePolling.ts:45-51` guards only on `disposed`.
- `test: stop() still cancels everything and survives onScopeDispose`: characterization of the
  existing contract at `:64-70`, so the refactor to a per-key timer map cannot regress
  teardown.

### T-6 (frontend, new file) `frontend/src/slices/conversation/__tests__/useChatroomExport.test.ts`

- `test: a superseded job's tick does not write exportJob`: start job A, start job B, resolve
  a tick for A, assert `exportJob.value` still reflects B. **Fails today**: `onResult`
  (`useChatroomExport.ts:22-24`) assigns for any key.
- `test: starting a new export cancels the previous poller`: assert `cancel` was called with
  A's id when B starts. **Fails today**: `runExport` (`:27-36`) calls only `start`.
- `test: reset() cancels the active key and clears the slot`. **Fails today**: `reset` is not
  exposed (`:38-41`).

### T-7 (frontend, existing file) `frontend/src/slices/conversation/__tests__/ChatroomView.test.ts`

- `test: reopening the export modal cancels the in-flight poller`: mount the view, run an
  export, close the modal, call `openExport()`, resolve a stale tick, assert the modal still
  renders the configuration form and not `export-status`. **Fails today**: `openExport`
  (`ChatroomView.vue:774-777`) nulls `exportJob` without cancelling, so the stale tick
  repopulates it and `ChatroomExportModal.vue:10` flips to the status branch. This is the
  end-to-end reproduction of §4 F-16 steps 3 to 5.

The e2e spec `frontend/e2e/14-export-attachments.spec.ts` needs no change: its assertions at
`:27` and `:43-44` cover a single-job happy path and remain valid.

## 9. Risks and Rollback

| Risk | Assessment | Mitigation |
|---|---|---|
| Over-correction: members and guests receive a near-empty export (own prompts, no agent replies) and file support tickets. | High likelihood under Q-1(a) if Q-3 resolves to "user messages only". This is the single largest product risk in the dossier. | Blocked on Q-1 and Q-3 before implementation. T-2's positive tests (`test_project_owner_export_is_unnarrowed`, `test_admin_export_is_unnarrowed`) bound the other direction. Whatever Q-3 resolves to should be surfaced in the export modal copy so the user knows what they are getting; if the narrowing is not visible in the UI, the support burden lands anyway. |
| Signature change to `all_for_chatroom` breaks another caller. | Very low. A repo-wide grep shows exactly one production caller, `chat_export_service.py:90`; every other match is in `tests/unit/test_chat_export_service.py`. The new parameter is optional with a `None` default. | Covered by T-3, which asserts the existing tests still pass unchanged. |
| Adding a field to `ExportJobState` breaks in-flight jobs across deploy. | Low but real. `get()` reconstructs from JSON at `export_service.py:118-130`; a record written by the old code lacks the new key. | Read the new field with `data.get(...)` and a conservative default, following the existing pattern for `export_format` at `:126`. The conservative default must be the *narrowest* scope, so a pre-deploy record can never widen. Jobs enqueued before deploy then either fail closed or produce a narrowed archive, both acceptable. |
| The worker's widen-refusal check produces false failures. | Low. Both sides re-derive from the same `RoomAccess` computation, so they diverge only if the caller's roles genuinely changed between enqueue and execution, which is precisely when failing is correct. | Failure path already exists at `tasks/conversation.py:239-245`; reuse it with a distinct error string so the case is distinguishable in logs. |
| Per-key timer map in `usePolling` regresses the GraphRAG consumer. | Low. That consumer only calls `start` and relies on `onScopeDispose`. | T-5's `stop()` characterization test is written before the refactor. |
| The Q-4 purge destroys a legitimate in-flight export. | Certain for any job running at that instant; consequence is a re-run. | Announce, or run during a low-traffic window. Not reversible, which is why it is a user decision. |

**Rollback.** The backend change is a revert of the route, service, repository, job-state and
audit edits, with no schema migration involved (job state is Redis-only per
`export_service.py:9-14`), so a revert restores the previous behavior immediately. The
frontend change is a revert of two composables and one view callsite. The Q-4 purge, if
performed, is not reversible: deleted export objects cannot be restored. That asymmetry should
be stated to the user when Q-4 is asked. Reverting after a purge restores the over-broad
behavior for *future* exports, which is a reason to treat the purge as the last step, after
the fix is confirmed in production.

## 10. Acceptance Criteria

- [ ] **AC-1**: Every test named in §8 that is stated to fail today does fail against
      unmodified `main`, and passes after the fix. Every test stated to pass today
      (`test_project_owner_export_is_unnarrowed`, `test_admin_export_is_unnarrowed`, the
      existing `test_chat_export_service.py` cases, `test_permission_matrix.py`) passes both
      before and after.
- [ ] **AC-2**: Q-1, Q-2, Q-3 and Q-4 are answered by the user and recorded in §3 before any
      F-2 implementation begins. The chosen Q-1/Q-3 semantics appear verbatim as a docstring
      on the narrowing helper.
- [ ] **AC-3**: A caller holding only `ORG_MEMBER`, only `PROJECT_MEMBER`, or only guest
      enrolment receives an export narrowed per the Q-1/Q-2/Q-3 answers. The narrowing is
      applied in the SQL predicate at `message_repo.py:289-297`, so excluded messages are
      never loaded into process memory.
- [ ] **AC-4**: A caller holding `PROJECT_OWNER`, `ORG_OWNER`, or admin receives an
      unnarrowed export, matching the `ALLOW` cells at `permissions.py:235,237` and the admin
      bypass at `permissions.py:304-305`.
- [ ] **AC-5**: `ensure_can_read` remains in the path at both `exports.py:102` and
      `chat_export_service.py:82`, unmodified. The four-flag room gate at `access.py:96-136`
      is not touched.
- [ ] **AC-6**: The worker re-derives the sender scope from the database and refuses to widen
      beyond the scope recorded in the job state, following the no-trust precedent at
      `tasks/conversation.py:233-245`.
- [ ] **AC-7**: `Capability.CHAT_EXPORT` has at least one production reference outside
      `shared_kernel/auth/permissions.py`, and `test_capability_wiring.py` (T-1) enforces this
      for every non-exempt capability going forward.
- [ ] **AC-8**: A `message.exported` audit event is emitted on successful export, per
      `REQUIREMENTS.md:844`, recording chatroom, requester, format, date bounds and resolved
      sender scope, and containing no message content and no presigned URL.
- [ ] **AC-9**: The stale docstring at `contexts/conversation/application/access.py:1-9` is
      corrected: rows 19 and 20 resolve as `OWN_ONLY` (`permissions.py:234-247`), not
      `ROOM_ACL`; only row 17 is `ROOM_ACL` (`permissions.py:223-229`).
- [ ] **AC-10**: `usePolling` exposes per-key `cancel(key)`; a cancelled key's pending timer
      is cleared and its in-flight fetch does not deliver to `onResult`; global `stop()` and
      the `onScopeDispose` wiring behave as before.
- [ ] **AC-11**: Reopening the export modal while an earlier job is polling leaves the
      configuration form displayed, and the earlier job's completion never renders into the
      modal, reproducing §4 F-16 to step 5 with the opposite outcome.
- [ ] **AC-12**: `check-security` has been run against this change (in parallel with
      implementation, per §6) and reports no new AuthZ or cross-room-leakage finding on the
      export path.
- [ ] **AC-13**: Full Definition of Done: `pytest -q`, `ruff check . && ruff format --check .`,
      `mypy .` in `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` in
      `frontend/`.
- [ ] **AC-14**: If Q-4 resolves to "purge", the `exports` bucket and all `chat_export:*`
      Redis keys are cleared as the final deployment step, after AC-1 through AC-13 are
      confirmed in production, and the operation is recorded in §12.

## 11. SRS Delta

Not "None". The analysis surfaced two genuine SRS problems, both blocked on §3.

- **Delta-1 (blocked on Q-1 and Q-3)** — `REQUIREMENTS.md:197` row 19 says
  "∘ (own messages)" against a legend (`:175`) that speaks of "resources the user owns". For
  keys and projects, ownership is a stored attribute. For a chat *export*, the exported
  artifact is a room-wide aggregate, and the row does not say what happens to the agent and
  system messages that constitute most of an agent chatroom's content. Whichever way Q-1 and
  Q-3 resolve, row 19 needs an explicit sentence in §5.2 or §13.6 stating the sender classes
  a narrowed export includes. Draft, assuming Q-1(a) with agent and system messages retained:
  "Row 19: a narrowed export contains the caller's own messages plus all agent and system
  messages in the room; messages sent by other users, and their edit histories and
  attachments, are excluded."

- **Delta-2 (blocked on Q-2)** — `REQUIREMENTS.md:197` grants Guest the row-19 circle while
  `docs/UI/07-conversation.md:1273` states guests get no export. One of the two documents is
  wrong and must be edited, not left to the implementer. If the SRS wins, the UI doc line is
  amended and `ChatroomHeader.vue` keeps showing the control to guests. If the UI doc wins,
  `REQUIREMENTS.md:197`'s Guest cell becomes `✗`, `permissions.py:239` is deleted, and
  `test_permission_matrix.py:107` drops `Role.GUEST` from the `CHAT_EXPORT` expectation.

- **No delta** for R13.17 (`REQUIREMENTS.md:678`) or R5.05 (`:207`). Both are correct as
  written; the code simply does not implement them.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 — matrix logic duplicated inline for row 20.** `messages.py:468-473` re-implements
  `Capability.MESSAGE_DELETE` rather than consulting the matrix. It is behaviorally correct
  today (§6) and the duplication is deliberately commented at `:468-470`, but it is a second
  copy of an authorization rule, against R5.05's "single `permissions` service"
  (`REQUIREMENTS.md:207`). This dossier adds a *third* such site by necessity (§7 explains why
  `decide()` cannot serve the guest tier). The right long-term fix is to extend the
  `RoleResolver` protocol so room-scoped roles including `Role.GUEST` are resolvable, which
  would let rows 19 and 20 both route through `decide()`. Out of scope here: it changes a
  `shared_kernel` protocol consumed by every capability check, which is not a change to make
  inside a disclosure fix.

- **FU-2 — frontend export control is not gated for guests.** `ChatroomHeader.vue:72-74` and
  `:193,199` emit `export` unconditionally, against `docs/UI/07-conversation.md:1273`. Advisory
  only per R5.05, and its disposition depends on Q-2. If Q-2 says guests may not export, this
  becomes a small UI change worth doing so guests are not offered a control that 403s.

- **FU-3 — no per-caller export quota.** `create_export` (`exports.py:89-118`) increments
  `EXPORT_JOBS` at `:116` but applies no rate limit, and each job may serialize up to
  `_EXPORT_MAX_MESSAGES = 50_000` rows (`chat_export_service.py:36`) plus a per-message
  `edits` and `attachments` round-trip (`chat_export_service.py:97-99`, N+1 by construction).
  A resource-exhaustion concern, not an authorization one, and narrowing the export will
  reduce but not remove it. Cleared as out of scope; worth handing to `check-security`'s
  resource-exhaustion dimension as a note when AC-12 is run.

- **FU-4 — `is_chatroom_participant` remains a landmine.** `role_resolver.py:74-86` raises
  `NotImplementedError` with a comment explaining that the previous `return True` was a
  fail-open. The refusal is correct and the current state is safe, but `Outcome.ROOM_ACL`
  (`permissions.py:140,351-356`) is consequently unusable through `decide()`, meaning row 17's
  `ROOM_ACL` cells (`permissions.py:223-229`) are enforced only via
  `chatrooms.py:616` plus `ensure_can_send`. Same underlying gap as FU-1 and would be closed by
  the same protocol change. Cleared as fragile-but-correct.
</content>
