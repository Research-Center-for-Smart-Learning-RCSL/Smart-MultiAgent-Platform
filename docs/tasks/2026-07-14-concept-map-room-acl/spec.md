---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.17]
---

# F-2: Private-room Concept Map graph and status bypass the room ACL

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-2).
Release blocker — routes through `/check-security` before merge (audit FU-1).

## 1. Summary

Concept Map (GraphRAG) read surfaces authorize on **project membership only** and never
consult the private-chatroom ACL, even when the config's `owner_kind` is `chatroom`. The
REST config/status/graph endpoints and the config-scoped WebSocket handshake all resolve
the caller's roles at `Scope(project_id=...)` and ignore `owner_chatroom_id`. A project
member who is denied read access to private chatroom R can therefore obtain R's Concept
Map config UUID and read its graph, entities, relations, and build status — and subscribe
to live build updates — despite being unable to read R itself. Private-room facts derived
from user and Agent messages leak to other project members.

## 2. Observed vs Expected

- **Observed (REST)**: all three reads on the config router authorize with
  `assert_project_membership(db, principal, cfg.project_id)` only —
  `GET /api/graphrag/{id}` (`backend/app/api/v1/graphrag.py:315-328`, authz `:323-327`),
  `GET /api/graphrag/{id}/status` (`:331-350`, authz `:339-343`),
  `GET /api/graphrag/{id}/graph` (`:353-393`, authz `:372-376`). None reference
  `cfg.owner_kind` / `cfg.owner_chatroom_id`; `assert_project_membership`
  (`backend/app/api/v1/deps.py:44-66`) builds a project-only `Scope`, so no room-flag
  matrix runs.
- **Observed (WS)**: the shared factory `make_config_scoped_ws_router`
  (`backend/contexts/knowledge/interfaces/ws_config_route.py:30-87`) authorizes the
  handshake with `TenancyRoleResolver(session).roles_for(auth.principal,
  Scope(project_id=cfg.project_id))` (`:64-71`) and never branches on `owner_kind`. Bound
  for GraphRAG at `backend/app/api/ws/graphrag.py:16-20`.
- **Expected**: for `owner_kind="chatroom"` configs, read/subscribe access is gated by the
  **room ACL** — the same `resolve_room_access` + `ensure_can_read` the rest of the
  conversation surface uses (`backend/app/api/v1/messages.py:162-167`,
  `backend/app/api/ws/chatroom.py:59-64`). For `owner_kind` `agent_group` and `workspace`,
  the existing project-membership rule (plus any documented enablement) is retained.
  Intent: [R11.17] (private-room trust boundary).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Package with F-1/F-3? | Separate dossier | Independent release blocker; decoupled lifecycle. |
| Q-2 | Does this fix cover mid-socket revocation? | No — handshake-time only | Mid-socket re-authorization is the distinct finding F-25 (its own spec). F-2 closes the handshake-time and REST-read room-ACL omission; the two are explicitly separated in the audit. |

## 4. Reproduction

1. Private chatroom R exists in project P with room flags that deny read to member M
   (M holds a project role but is not permitted to read R per the §21.1 room-flag matrix).
2. R has a chatroom-owned Concept Map config C (`owner_kind="chatroom"`,
   `owner_chatroom_id=R`).
3. M learns C's UUID and calls `GET /api/graphrag/{C}/graph` (or `/status`, or the config
   GET). The request returns 200 with R's derived graph/entities/relations.
4. M opens `/ws/graphrag/{C}` and receives live `build.state` updates.
   Every step succeeds today because only project membership is checked.

## 5. Root Cause Analysis

Both the REST reads and the WS handshake authorize solely on `cfg.project_id` and never
inspect the typed owner. The root cause is the **absent owner-kind branch** at the two
authorization points:
- REST: `graphrag.py:323-327,339-343,372-376` call `assert_project_membership` with no
  room-ACL branch.
- WS: `ws_config_route.py:64-71` resolves a project-only scope with no room-ACL branch.

The correct predicate already exists and is used everywhere else
(`resolve_room_access`/`ensure_can_read`, `contexts/conversation/application/access.py:52-93,125-136`),
re-exported via `contexts/conversation/interfaces/access.py:1-15`. The owner is
recoverable from the config: `_owner_id(cfg)` (`graphrag.py:185-190`) already maps
`owner_kind -> owner_chatroom_id/…`; the chatroom id for a chatroom-owned config is
`cfg.owner_chatroom_id` (domain `contexts/knowledge/domain/graphrag.py:86`, table
`contexts/knowledge/infrastructure/graphrag_tables.py:67-72`).

## 6. Blast Radius and Sibling Suspects

- **Blast radius**: every chatroom-owned Concept Map. Leaked data = graph nodes/edges,
  entities, relations, build `status`/`last_build_error` strings, and live build events —
  all derived from private-room user and Agent messages.
- **Sibling suspects (same missing owner-kind branch):**
  - `read_config` `GET /api/graphrag/{id}` (`graphrag.py:315`): CONFIRMED same gap — fix
    with the reads.
  - `read_agent_concept_map_coverage` (`graphrag.py:555-578`): authorizes at the agent's
    project; it exposes coverage metadata, not room graph content — assess and, if it can
    reveal a private room's config existence/state, apply the same owner-aware gate. Mark
    for the implementer to confirm.
  - Config-scoped WS for **RAG** (`/ws/rag-configs/{id}`) and **Knowledge Map**
    (`/ws/knowmap/{id}`) share `make_config_scoped_ws_router` but those owners are not
    chatrooms (project-scoped configs), so the room ACL does not apply — CLEARED for F-2.
    (Their *mid-socket* re-auth gap is F-25, separate.)
  - Write endpoints (`update`/`delete`/`trigger_build`, `graphrag.py:396,425,487`) use
    `_assert_edit` (project RESOURCE_CREATE_EDIT). Out of scope: the finding is about
    reads/subscriptions; note but do not expand scope without the user.

## 7. Fix Design

Introduce one owner-aware authorization helper and call it from every chatroom-capable
read/subscribe point, so `chatroom`-owned configs go through the room ACL while
`agent_group`/`workspace` keep the project rule.

- **Shared helper** (knowledge interface or a small authz function): given the resolved
  `cfg` and the `principal`/session, branch on `cfg.owner_kind`:
  - `chatroom`: `access = await resolve_room_access(db, principal=principal,
    chatroom_id=cfg.owner_chatroom_id)`; `ensure_can_read(access, is_admin=principal.is_admin)`.
    Admin bypass mirrors the existing handshake admin short-circuit
    (`ws_config_route.py:55`).
  - `agent_group` / `workspace`: keep the current project-membership check.
- **REST**: replace the bare `assert_project_membership` in `read_config`, `read_status`,
  `read_graph` (`graphrag.py:315-393`) with the owner-aware helper.
- **WS**: in `make_config_scoped_ws_router` (`ws_config_route.py:56-76`), after resolving
  `cfg`, apply the same owner-aware branch before entering `connection_loop`. Because the
  factory is shared across GraphRAG/RAG/Knowledge-Map, gate the room branch on
  `owner_kind == "chatroom"` so non-chatroom configs are unaffected (their configs never
  carry that owner kind). Close code `4403` on room-ACL denial, matching the existing
  project-denial path (`:69-71`).

**Security considerations** (this fix is the access control):
- Fail-closed: a `ChatroomNotFound` from `resolve_room_access` (room deleted) must deny,
  not fall through to the project check.
- Guests: `ensure_can_read` already handles the guest flag via the room-flag matrix; reuse
  it verbatim rather than re-deriving guest logic.
- Do not weaken the `agent_group`/`workspace` paths — only *narrow* the `chatroom` path.
- Mid-socket revocation is explicitly **not** covered here (F-25); document the residual
  window in §13 so it is not mistaken for closed.

**Reuse inventory:**
- `resolve_room_access` / `ensure_can_read` /
  `RoomAccess` (`contexts/conversation/application/access.py:52-93,125-136,35-49`),
  re-exported at `contexts/conversation/interfaces/access.py`.
- `_owner_id(cfg)` (`graphrag.py:185-190`) — owner-kind -> owner-id map already present.
- Precedent call sites to copy: `messages.py:162-167`, `chatroom.py:59-64`.

**Patterns to follow (SoC):** the room ACL lives in the conversation context and is
consumed via its public interface (`contexts/conversation/interfaces/access.py`) — the
knowledge/API layer calls that port; it must not re-implement room-flag evaluation.

**Data repair:** none — this is an authorization gap; no bad data was written.

## 8. Regression Test Plan

Failing-first tests (fail against current code, pass after):

1. **REST room-ACL (integration/route test)** — a principal with project membership but
   denied room read gets 403 from `GET /api/graphrag/{id}/graph`, `/status`, and the
   config GET for a `chatroom`-owned config. Fails today (returns 200).
2. **REST non-chatroom unaffected** — the same principal with project membership still
   gets 200 for an `agent_group`- and a `workspace`-owned config. Guards against
   over-tightening.
3. **WS room-ACL** — subscribing to `/ws/graphrag/{id}` for a `chatroom`-owned config as a
   room-denied member closes with `4403`; a room-permitted member connects. Fails today.
4. **Admin bypass** — an admin reads and subscribes regardless of room flags (parity with
   the existing handshake admin short-circuit).

## 9. Risks and Rollback

- **Risk**: the WS factory is shared; a careless branch could tighten RAG/Knowledge-Map
  channels. Mitigate by gating strictly on `owner_kind == "chatroom"` and testing all
  three channel bindings.
- **Risk**: an extra DB round-trip (`resolve_room_access`) per read/handshake for
  chatroom-owned configs — acceptable; mirrors every other room-scoped route.
- **Rollback**: revert the commits; the change is additive authorization (no schema/data
  migration), so prior behavior returns cleanly.

## 10. Acceptance Criteria

- [ ] AC-1: The four regression tests in §8 fail before the fix and pass after.
- [ ] AC-2: For `owner_kind="chatroom"` configs, `read_config`, `read_status`, and
  `read_graph` deny a room-denied project member (403) and permit a room-permitted member.
- [ ] AC-3: The `/ws/graphrag/{id}` handshake applies the room ACL for chatroom-owned
  configs (close `4403` on denial) and is unchanged for `agent_group`/`workspace`.
- [ ] AC-4: RAG (`/ws/rag-configs/{id}`) and Knowledge-Map (`/ws/knowmap/{id}`) channels
  and all `agent_group`/`workspace` GraphRAG reads behave exactly as before.
- [ ] AC-5: Admin principals retain full read/subscribe access regardless of room flags.
- [ ] AC-6: `/check-security` review passes for the private-room ACL / cross-tenant read
  boundary (audit FU-1).

## 11. SRS Delta

None — restores the [R11.17] private-room trust boundary for Concept Map read surfaces.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (F-25, separate spec)**: the config-scoped WebSockets do not re-authorize
  mid-socket. Even after this fix, a member removed from the room/project *after* a
  successful handshake keeps streaming until the access token expires. Closing that window
  is F-25 and must reuse the owner-aware predicate introduced here.
- **FU-2**: confirm whether `read_agent_concept_map_coverage` (`graphrag.py:555`) can
  reveal a private room's config existence/state to a room-denied member; if so, apply the
  same owner-aware gate.
