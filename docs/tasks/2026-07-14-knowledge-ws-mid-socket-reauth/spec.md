---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.17]
---

# F-25: Config-scoped knowledge WebSockets never re-authorize access mid-socket

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-25).
Release blocker — routes through `/check-security` before merge (audit FU-1).
Builds on the owner-aware predicate introduced by
`docs/tasks/2026-07-14-concept-map-room-acl/spec.md` (F-2, its FU-1 names this fix).

## 1. Summary

The shared factory behind `/ws/graphrag/{id}`, `/ws/rag-configs/{id}`, and
`/ws/knowmap/{id}` authorizes the caller's access to a config **once at the handshake** and
then calls `connection_loop` without an `authorize=` callback. The connection watchdog
therefore enforces only token expiry and the jti denylist for these three channels — it
never re-resolves project membership or the room ACL. A principal whose project membership
is removed, whose role is tightened, or (for a chatroom-owned Concept Map) whose private-room
read access is revoked keeps streaming that config's `build.state`/ingestion events until the
access token naturally expires, even though every HTTP request 403s immediately. The
re-authorization machinery already exists and is proven on the chatroom WS
(`connection_loop(..., authorize=...)`, re-run every ~60s, closes 4403 on access loss); the
fix supplies an owner-aware `authorize` callback to the config factory so all three channels
re-check access on the same cadence.

## 2. Observed vs Expected

- **Observed** — the config factory `make_config_scoped_ws_router`
  (`backend/contexts/knowledge/interfaces/ws_config_route.py:30-35`) runs a one-time handshake
  check (`get_config` + `TenancyRoleResolver.roles_for(Scope(project_id=cfg.project_id))`,
  `:55-71`) and then calls `connection_loop` passing only `channels`, `token_expires_at`, and
  `token_jti` — **no `authorize=`** (`:78-85`). In `connection_loop`, the `authorize` probe is
  invoked only when supplied and only every `_ROOM_REAUTH_EVERY_N_TICKS` (=2) watchdog ticks
  (~60s at the 30s tick), closing `_CLOSE_FORBIDDEN=4403` on denial
  (`backend/shared_kernel/realtime/connection.py:359-389`, SEC-H2 at `:70-77`). Absent the
  probe, the watchdog enforces only token expiry (`:371-373`) and jti denylist (`:390-407`).
  The three routes all bind the identical factory
  (`backend/app/api/ws/graphrag.py:14-19`, `backend/app/api/ws/rag_configs.py:14-17`,
  `backend/app/api/ws/knowmap.py:16-19`), so the gap is uniform across all three.
- **Expected** — mid-socket, access is re-resolved on the SEC-H2 cadence and the socket is
  torn down (4403) when access is lost, matching the per-request ACL guarantee the HTTP path
  upholds and the SEC-H2 comment's own stated intent ("a user removed from a room ... /
  project membership lost ... kept receiving the room's events until their access token
  expired", `connection.py:70-75`). For chatroom-owned Concept Maps the re-check is the **room
  ACL**; for `agent_group`/`workspace` Concept Maps and for RAG/Knowledge-Map configs it is
  **project membership**. Intent: [R11.17] (private-room trust boundary), the SEC-H2
  `authorize`-hook contract.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How wide should the re-auth callback's check be? | **Project-role + room ACL.** The callback re-resolves project roles for every config and, for `owner_kind="chatroom"` Concept Maps, additionally re-checks the room ACL. | Chosen over project-role-only. F-2 closes the *handshake-time* room-ACL omission; without room re-auth here, a member removed from a private room after handshake would keep streaming a chatroom-owned Concept Map until token expiry — the exact residual window F-2's FU-1 leaves open. Covering the room ACL here makes F-25 the true mid-socket completion of F-2. |
| Q-2 | Reuse F-2's owner-aware predicate or write a new one? | **Reuse F-2's owner-aware helper**, invoked inside the callback. | F-2 introduces one helper that branches `chatroom → room ACL`, else project roles, and is the single authz predicate for the config read/subscribe surface. F-25's callback must call the same helper so handshake and mid-socket enforce identical rules. Creates an ordering dependency on F-2 (§9). |
| Q-3 | Cadence and failure mode? | Reuse the existing SEC-H2 cadence (~60s) and fail-open-per-window behavior of `connection_loop`; no new knobs. | The chatroom WS already tunes this (`_ROOM_REAUTH_EVERY_N_TICKS`); a transient authz error retries next window rather than dropping a legitimate connection (`connection.py:381-385`). Reusing it keeps one revocation-window contract across all sockets. |

## 4. Reproduction

1. Project P member M opens `/ws/graphrag/{C}` (or `/ws/rag-configs/{C}` or `/ws/knowmap/{C}`)
   for a config C in P and receives live events.
2. An owner removes M from P (or, for a chatroom-owned Concept Map C, revokes M's read access
   to the owning private room per the room-flag matrix).
3. HTTP reads of C 403 immediately, but M's open socket keeps receiving C's `build.state` /
   ingestion frames until M's access token expires or is denylisted.

Deterministic; the window is bounded only by the access-token TTL.

## 5. Root Cause Analysis

The factory omits the `authorize=` kwarg at the single `connection_loop` call site
(`ws_config_route.py:78-85`). That is the root cause: the watchdog's project/room re-auth
branch (`connection.py:378-389`) is dead code for these three channels because no probe is
supplied, leaving only token-level enforcement. Aggravating detail: `cfg.project_id` is
resolved inside the handshake block and not retained past it (`ws_config_route.py:60`), so the
callback must perform its own `get_config` lookup (the factory already closes over `get_config`
and `config_id`, so this is available). There is no data corruption — this is a missing
authorization re-check.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — a mid-socket revocation window (bounded by access-token TTL today; reduced
  to ~60s after the fix) on all three config-scoped knowledge channels, leaking build progress,
  `last_build_error` strings, ingestion progress, and document counts to a just-removed
  principal.
- **Sibling suspects:**
  - **Chatroom WS (cleared — the exemplar).** `backend/app/api/ws/chatroom.py:95-109,144-156`
    already builds and passes an `authorize` callback (`resolve_room_access` + `ensure_can_read`
    off `conn.principal`); mirror its shape, do not modify it.
  - **All three config routes (confirmed, one shared fix).** graphrag / rag-configs / knowmap
    all use `make_config_scoped_ws_router`, so a single change at
    `ws_config_route.py:78-85` covers every channel. The callback must be safe for config types
    that carry no `owner_kind` (RAG and Knowledge-Map configs) — those resolve to the
    project-membership branch (`getattr(cfg, "owner_kind", None)` is `None` / not `chatroom`).
  - **Other `connection_loop` callers (cleared).** Only the chatroom and config factories reach
    `connection_loop`; the chatroom already re-auths, the config factory is this fix.

## 7. Fix Design

Supply an owner-aware `authorize` callback to the config factory's `connection_loop` call,
reusing F-2's predicate so handshake and mid-socket enforce identical access rules.

1. **Authorize callback** — in `make_config_scoped_ws_router`
   (`ws_config_route.py:30-85`), after the handshake, define
   `async def authorize(conn: ChannelConnection) -> bool` that:
   - opens a session (`get_sessionmaker()`, mirroring the handshake block `:56-58`);
   - `cfg = await get_config(KnowledgeFacade(session), config_id)`; return `False` if `cfg is
     None` (config deleted mid-socket → deny, fail-closed);
   - `if conn.principal.is_admin: return True` (mirror the handshake admin short-circuit `:55`);
   - otherwise call **F-2's owner-aware predicate** with `(session, principal=conn.principal,
     cfg)`: `chatroom` → `resolve_room_access` + `ensure_can_read`; else `roles_for(Scope(
     project_id=cfg.project_id))` truthiness. Read the live principal from `conn.principal`
     (it may have been refreshed) exactly as the chatroom callback does
     (`chatroom.py:103`).
   - return `True`/`False`; a raised store error propagates to `connection_loop`, which logs and
     retries next window (`connection.py:381-385`).
2. **Pass it in** — add `authorize=authorize` to the `connection_loop` call
   (`ws_config_route.py:78-85`). No other kwargs change; the watchdog already activates for
   these routes via `token_expires_at`/`token_jti` (`connection.py:426-432`).
3. **No new re-auth engine** — cadence, 4403 close code, and fail-open-per-window all come from
   the existing `connection_loop`/watchdog (`connection.py:359-389`); this fix only wires the
   probe.

**Security considerations** (this is an access-control fix):
- Fail-closed on `cfg is None` and on room-not-found (`ChatroomNotFound` from
  `resolve_room_access`) — deny, never fall through to the project check.
- Admin bypass must mirror the handshake exactly so behavior is consistent across the socket
  lifetime.
- Do not weaken the `agent_group`/`workspace`/RAG/Knowledge-Map project path — only add the
  chatroom room-ACL branch, delegating entirely to F-2's helper (no re-implemented room-flag
  logic; SoC — the room ACL lives in the conversation context and is consumed via its port).
- **[R11.17] agent_group/workspace gate is broader than project membership.** The requirement
  gates those owner kinds by "project membership **plus** their `concept_map_enabled` opt-in." The
  current handshake (`ws_config_route.py:64-71`) and F-2's predicate check only project roles, so
  both handshake and mid-socket under-enforce `concept_map_enabled`. F-25 delegates to F-2's
  predicate, so whatever F-2 enforces is what re-runs here — meaning F-2's predicate must
  incorporate the `concept_map_enabled` gate for the enforcement to be complete at both points
  (see FU-3, raised against F-2). F-25 introduces no weaker check than the handshake; it must not
  introduce a stronger one either, or a socket would survive a handshake it should re-deny.
- The residual window shrinks from the token TTL to one re-auth cadence (~60s); document it as
  the intended bound, not a full close.

**Reuse inventory:**
- F-2's owner-aware predicate (from `2026-07-14-concept-map-room-acl`) — the single source of
  the chatroom-vs-project branch.
- `resolve_room_access` / `ensure_can_read` (`contexts/conversation/interfaces/access.py`),
  `TenancyRoleResolver.roles_for` / `Scope` (used at handshake `ws_config_route.py:64-68`).
- The chatroom callback as the structural template (`chatroom.py:95-109,155`).

**Data repair:** none.

## 8. Regression Test Plan

The failing-first test extends `backend/tests/unit/test_ws_config_route.py` (which today
asserts only handshake branches and captures `connection_loop` kwargs via a fake loop, but
never asserts `authorize`):

1. **`authorize` is wired (red-first)** — the success/admin cases assert the captured
   `connection_loop` kwargs now include a callable `authorize`. Fails today (kwarg absent).
2. **Callback denies on lost project membership** — invoke the captured `authorize(conn)` for an
   `agent_group`/`workspace` (or RAG/knowmap) config with a principal whose `roles_for` now
   returns empty; assert `False`. (A parallel case asserts `True` while membership holds.)
3. **Callback denies on revoked room access** — for a `chatroom`-owned Concept Map config,
   `authorize(conn)` with a principal denied room read (via F-2's predicate) returns `False`;
   a room-permitted principal returns `True`.
4. **Callback admin bypass** — `authorize(conn)` with `conn.principal.is_admin` returns `True`
   regardless of roles/room flags.
5. **Callback fail-closed on deleted config** — `get_config` returning `None` yields `False`.

Optionally add a `connection.py`-level test that a supplied `authorize` returning `False`
closes 4403 (the SEC-H2 path at `connection.py:378-389` is currently untested per
`test_ws_auth_watchdog.py`); this is shared infra and may be recorded as FU rather than blocking.

Primary red-first: (1).

## 9. Risks and Rollback

- **Ordering dependency on F-2.** The callback reuses F-2's owner-aware predicate and both
  edit the same factory function (`ws_config_route.py`). F-2 should land first or in the same
  PR; if F-25 is built alone, it must introduce (not duplicate) the predicate and coordinate
  the merge. Recorded as the primary risk.
- **Shared factory.** The callback runs for all three channels; the chatroom branch must be
  gated so non-chatroom configs (RAG/knowmap, no `owner_kind`) take the project path only.
  Covered by tests (2)-(3).
- **Extra load.** One `get_config` + role/room resolution per socket per ~60s window;
  acceptable and matches the chatroom WS budget.
- **Fail-open-per-window.** A transient authz-store error retries next window rather than
  dropping a legitimate connection (existing `connection_loop` behavior); the revocation window
  can extend by one cadence under sustained store failure — acceptable, documented.
- **Rollback** — remove the `authorize=` kwarg; behavior reverts to handshake-only. Code-only,
  no schema/data change.

## 10. Acceptance Criteria

- [ ] AC-1: The `authorize`-is-wired test (§8.1) fails before the fix and passes after.
- [ ] AC-2: All three config channels (`/ws/graphrag/{id}`, `/ws/rag-configs/{id}`,
  `/ws/knowmap/{id}`) pass an `authorize` callback into `connection_loop`.
- [ ] AC-3: Mid-socket, a principal who loses project membership/role on an
  `agent_group`/`workspace`/RAG/Knowledge-Map config is torn down (4403) within one re-auth
  cadence; a still-authorized principal is not.
- [ ] AC-4: Mid-socket, a principal who loses room read access on a `chatroom`-owned Concept Map
  is torn down (4403) within one re-auth cadence, using F-2's room-ACL predicate; a
  room-permitted principal is not.
- [ ] AC-5: Admin principals retain access across the socket lifetime; a deleted config denies.
- [ ] AC-6: `agent_group`/`workspace` Concept Maps and all RAG/Knowledge-Map configs use the
  project-membership branch only (no room-flag evaluation).
- [ ] AC-7: `/check-security` review passes for the mid-socket revocation boundary (audit FU-1).
- [ ] AC-8: `pytest -q`, `ruff check . && ruff format --check .`, and `mypy .` pass in `backend/`.

## 11. SRS Delta

None — restores the [R11.17] trust boundary and the SEC-H2 mid-socket re-auth contract for the
config-scoped knowledge channels.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1 (F-2 dependency):** this fix reuses and depends on F-2's owner-aware predicate
  (`2026-07-14-concept-map-room-acl`); coordinate the merge order.
- **FU-2 (test infra):** the `connection_loop` SEC-H2 `authorize` re-auth path
  (`connection.py:378-389`) has no direct unit test today (`test_ws_auth_watchdog.py` covers
  only token expiry/denylist). Worth a dedicated re-auth close test independent of this fix.
- **FU-3 (F-2 predicate completeness — [R11.17] `concept_map_enabled`):** [R11.17] gates
  agent_group/workspace Concept Maps by "project membership plus their `concept_map_enabled`
  opt-in", but the current handshake and (per its spec) F-2's predicate check only project roles —
  so `concept_map_enabled` is unenforced for reads/subscriptions at both the handshake and (via
  this fix) mid-socket. Raise against F-2 (`2026-07-14-concept-map-room-acl`): its owner-aware
  predicate should incorporate the `concept_map_enabled` gate so both enforcement points are
  complete. F-25 inherits whatever F-2 enforces and needs no separate change once F-2 is corrected.
