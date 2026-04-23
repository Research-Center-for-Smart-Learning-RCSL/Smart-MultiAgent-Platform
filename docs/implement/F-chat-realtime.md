# Phase F — Chat & Real-time

**Goal.** Deliver the complete chat experience: Workspaces → Chat Rooms, messages (with edit / delete rules, hard-delete on manual, 5-year retention), attachments via the tus resumable protocol, the WebSocket endpoints (`/ws/*`), SSE-style push via the same WS surface for run progress, Guest link access, chat export, and full-text search.

**Size.** L
**Depends on.** C (auth, orgs, projects, rate-limits, web-security), D (keys), E (agents).
**Unblocks.** G, H.
**Refs.** `REQUIREMENTS.md` §13 (all), §19a.6, §21.1 chat tables, §21.5 buckets, §22.10, §22.14, §22.15.

## F.0 Scope summary

By phase close:

- Projects contain **1..N Workspaces**; each Workspace has **≥ 1 Chat Room** (R13.01 / R13.02). Deleting the last Chat Room auto-creates a default.
- Chat rooms have the four independent boolean access flags exactly as §21.1.
- Messages store `content_md` + `content_tsv`; `metadata jsonb` holds rag chunks, graphrag refs, mcp_calls, compact_summary, tool_calls.
- Guest links follow R6.12 / R13.05–R13.07: **permanent, no revoke, no expiry, no use cap, no password**; revocation only by deleting the room or banning the user.
- Attachments up to 1 GB use tus v1.0.0 at `/api/tus`; incomplete uploads expire after 24h; attachments in `chat-uploads` bucket expire after 3 days.
- WebSocket endpoints `/ws/user/{id}`, `/ws/chatroom/{id}`, `/ws/rag-configs/{id}` live with subprotocol auth; admin-only `/ws/admin/tail` live.
- Export + search endpoints live.

## F.1 Workspaces — **CODE** — S

**Deliverables.**

- Alembic revision `0009_workspaces`:
  ```
  workspaces (id, project_id fk projects, name, created_at, deleted_at);
  ```
- Endpoints (§22.10): `GET /api/projects/{pid}/workspaces`, `POST /api/projects/{pid}/workspaces` (auto-creates one default Chat Room), `DELETE /api/workspaces/{id}` (soft).
- Workspaces cannot nest (R13.01).

**Key IDs.** `[R13.01]`, `[R13.03]`, §22.10.

**Exit criteria.** Create workspace → default chatroom appears atomically.

## F.2 Chat rooms schema & CRUD — **CODE** — M

**Deliverables.**

- Alembic revision `0010_chatrooms`:
  ```
  chatrooms (
    id uuid pk, workspace_id fk workspaces, name,
    allow_org_members bool, allow_project_members bool,
    allow_project_owners_only bool, allow_guest_links bool,
    guest_token text UNIQUE,            -- CSPRNG 32-byte base64url ≈ 192 bits entropy
    version int not null default 1,
    created_at, deleted_at
  );
  chatroom_agents (chatroom_id fk, agent_id fk, PRIMARY KEY (chatroom_id, agent_id));
  chatroom_guests (chatroom_id fk, user_id fk, joined_via_token text,
                   joined_at, PRIMARY KEY (chatroom_id, user_id));
  ```
- Endpoints (§22.10):
  - `GET /api/workspaces/{id}/chatrooms`, `POST /api/workspaces/{id}/chatrooms`.
  - `PATCH /api/chatrooms/{id}` (name, access flags; `If-Match: <version>`).
  - `DELETE /api/chatrooms/{id}` (soft); deleting the last one in a workspace auto-creates a default (R13.02).
  - `GET/POST/DELETE /api/chatrooms/{id}/agents`.
  - `GET /api/chatrooms/{id}/guest-link` returns the permanent URL `https://<host>/g/{chatroom_id}/{guest_token}`.
- UI auto-corrects semantically useless flag combinations (R13.04) — backend accepts any subset.

**Key IDs.** `[R13.02]`, `[R13.04]`, §22.10, §21.1 chatrooms.

**Exit criteria.** CRUD green; last-room-delete creates default.

## F.3 Messages schema & CRUD — **CODE** — M

**Deliverables.**

- Alembic revision `0011_messages`:
  ```
  messages (
    id uuid pk, chatroom_id fk chatrooms,
    sender_type enum('user','agent','system'),
    sender_id uuid,                              -- user_id or agent_id depending on sender_type
    content_md text, content_tsv tsvector,
    metadata jsonb,                              -- {rag_chunks, graphrag_refs, mcp_calls,
                                                 --  compact_summary, tool_calls}
    version int not null default 1,
    created_at, edited_at timestamptz null, deleted_at timestamptz null
  );
  CREATE INDEX ON messages (chatroom_id, created_at DESC) WHERE deleted_at IS NULL;
  CREATE INDEX ON messages USING GIN (content_tsv);
  message_edits (
    id, message_id fk messages, old_content_md text,
    edited_by_user_id fk users, edited_at
  );
  message_attachments (
    id, message_id fk messages, filename, mime, size_bytes, minio_path,
    status enum('active','quarantined','expired') default 'active',
    scan_status enum('pending','clean','quarantined','skipped') default 'pending',
    scan_at timestamptz null,
    expires_at
  );
  CREATE INDEX ON message_attachments (expires_at);
  ```
- `content_tsv` maintained by trigger on insert/update of `content_md`.
- Guests are registered users (R5.04); `sender_type='user'` is used for them. `chatroom_guests` flags the relationship.

**Key IDs.** `[R13.08]`–`[R13.11]`, §21.1 messages tree.

**Exit criteria.** Insert/update/tsvector trigger tests pass.

## F.4 Message lifecycle — edit & delete rules — **CODE** — M

**Deliverables.**

- Send: `POST /api/chatrooms/{id}/messages` with `{content_md, attachment_ids}`.
- List: `GET /api/chatrooms/{id}/messages?before=<id>&limit=` and `?since=<id>` for WS-reconnect delta (R13.20).
- Permalink: `GET /api/messages/{id}` scope-checked.
- Edit: `PATCH /api/messages/{id}` with `If-Match: <version>`:
  - **Users** may edit **their own** messages within 5 minutes of creation (R13.21). After 5 min, only Admin / Project Owner may edit.
  - **Agents** cannot edit their own past messages (R13.22).
  - **Admin / Project Owner** may edit any message in scope; emits `message.updated` WS event and audit `message.edited_by_moderator` (R13.23).
  - Every edit preserves a `message_edits` row (R13.21).
- Delete: `DELETE /api/messages/{id}` — **hard delete** (R13.16):
  - Removes the `messages` row and its `message_edits` rows immediately.
  - `content_tsv` GIN index drops the entry synchronously (DB handles).
  - Deletion permission matrix: own messages any time (self), Admin / Project Owner any in scope (R13.16 + §5.2 capability 20).
  - Scheduled retention purge (F.8) is separate from manual delete.

**Key IDs.** `[R13.15]`, `[R13.16]`, `[R13.21]`–`[R13.24]`, §22.10.

**Exit criteria.** 5-min boundary test; agent-self-edit rejected; moderator edit audited; manual delete vanishes from GIN search in same request.

## F.5 Attachments & tus — **CODE** — L

**Deliverables.**

- Single-shot multipart: `POST /api/chatrooms/{id}/attachments` for ≤ 32 MB (R22.15 indirectly by deferring to tus above that).
- `GET /api/attachments/{id}` returns metadata + short-lived MinIO signed URL.
- **tus v1.0.0 at `/api/tus`** (R22.15):
  - Library: pinned `tuspy-server` (or equivalent vetted implementation).
  - Required headers: `Tus-Resumable: 1.0.0`, `Upload-Length`, `Upload-Offset`, `Upload-Metadata`.
  - `Upload-Metadata` MUST include:
    - `purpose` ∈ {`chat_attachment`, `rag_source`}.
    - `project_id` (UUID).
    - `chatroom_id` (when `purpose=chat_attachment`).
    - `rag_config_id` (when `purpose=rag_source`).
    - `filename`, `mime`.
  - Auth: `Authorization: Bearer <access_token>` on Creation POST.
  - Caller must have upload permission for the declared project / chatroom / rag-config.
  - Max total size 1 GB → 413 if larger on Creation.
  - Per-PATCH chunk cap: **16 MB**.
  - Incomplete upload abandoned and cleaned after **24 h**.
  - Completion response: `Location: /api/tus/{upload_id}` plus `X-SMAP-Resource: /api/{chat|rag}/.../{new_id}`.
  - Rate limits (R22.15.06): Creation 20 / min / user; PATCH 300 / min / user.
  - Post-completion: backend dispatches `file.scan_requested` task; operators may wire ClamAV or equivalent. Unconfigured = no-op pass. Scan failure → `status='quarantined'` + audit `attachment.quarantined` / `rag.document.quarantined` (R22.15.07).
- Storage layout (§21.5 / R13.10): `chat-uploads/{project_id}/{chatroom_id}/{msg_id}/{filename}`; bucket lifecycle = 3-day expiration; on expiry the UI substitutes `[attachment expired]` text for the message reference (R13.11).
- `rag-sources` bucket kept as long as the `rag_documents` row lives.
- `exports` bucket lifecycle 24h.

**Key IDs.** `[R13.08]`–`[R13.11]`, `[R22.15.01]`–`[R22.15.07]`, §21.5.

**Exit criteria.** 600 MB resumable upload with mid-transfer disconnect completes; PATCH cap enforced; 413 at 1GB+1.

## F.6 WebSocket endpoints — **CODE** — L

**Deliverables.** Per §22.14, five separate endpoints; **no multiplexed `/ws`**.

| Path | Pushes |
|---|---|
| `/ws/user/{user_id}` | notifications, ban-kick, `rag.document.status_changed`, `rag.document.failed` (scoped to caller's projects) |
| `/ws/chatroom/{id}` | see R13.19 event list below |
| `/ws/workflow-runs/{id}` | step transitions, approval prompts |
| `/ws/rag-configs/{id}` | `document.ingesting / ready / failed` + per-document progress % |
| `/ws/admin/tail` | Admin-only live audit tail |

- **Authn**: `Sec-WebSocket-Protocol: bearer.<access_token>` subprotocol; server echoes it on handshake. Token expiry handled in-socket via `{"type":"refresh","access_token":"..."}` message.
- **R13.19 chatroom event set** (exact): `message.created`, `message.updated`, `message.deleted`, `agent.thinking`, `agent.token` (streaming), `agent.finished`, `presence.joined`, `presence.left`, `approval.requested`, `approval.resolved`, `workflow.state_changed`.
- **Presence**: Redis sets `ws:presence:{room_id}` and `ws:user:{user_id}` track online state.
- **Per-user WS cap**: max 5 concurrent per user (R19.03); 6th rejected with structured error.
- **Reconnect delta**: server does not replay; client fetches `GET /api/chatrooms/{id}/messages?since=<id>` on reconnect (R13.20).
- **Backpressure**: bounded outbound queue per connection; slow consumer disconnected without blocking peers.

**Key IDs.** `[R13.19]`, `[R13.20]`, `[R19.03]`, §22.14.

**Exit criteria.** 6th concurrent WS rejected; token-refresh in-socket keeps the connection alive across expiry; chaos-kill of a subscriber reconnects via delta fetch.

## F.7 Markdown render & sanitisation — **CODE** — M

**Deliverables.**

- Server side (R13.14): `bleach` allowlist strips `<script>`, event handlers, `javascript:`/`data:text/html` URIs, `<object>`, `<embed>`, `<iframe>`, `<form>`, `<meta http-equiv="refresh">`. **CSS allowlist strips `url()`, `@import`, and `expression()`**.
- Supported rendering (R13.12): full markdown, inline HTML (allow-listed), fenced code blocks, **LaTeX via KaTeX**, **Mermaid** diagrams.
- Linked images (R13.13): no domain allowlist; UI warns users that images are fetched by the browser, not the server.
- Client-side pipeline (R24.41 / R24.42): `markdown-it` → `DOMPurify` → then KaTeX / Mermaid / highlight.js via DOM-mutation APIs only. Render lives in `slices/conversation/lib/renderMarkdown.ts`; no other `v-html` anywhere (ESLint gate in J).
- Chat exports (R13.14) include only the sanitised HTML; raw markdown preserved separately in the JSON manifest.

**Key IDs.** `[R13.12]`–`[R13.14]`.

**Exit criteria.** OWASP XSS cheat-sheet stored raw → rendered clean; CSS payloads (`url(evil.svg)`, `expression(...)`, `@import url(...)`) stripped.

## F.8 Retention purge — **CODE** — S

**Deliverables.**

- Nightly worker hard-deletes messages older than **5 years** (R13.15 / R13.25). Associated MinIO attachments deleted as part of the sweep.
- Emits audit `message.purged_by_retention` with `{chatroom_id, count, oldest_kept_at}`.
- 5-year window is a platform default, not user-configurable in v1.
- Manual delete (F.4) is independent and hard-deletes immediately.

**Key IDs.** `[R13.15]`, `[R13.25]`.

**Exit criteria.** Fast-forward clock test purges eligible rows + their attachments.

## F.9 Guest link access — **CODE** — S

**Deliverables.**

- Guest link = `https://<host>/g/{chatroom_id}/{guest_token}` (R13.05). `guest_token` stored in `chatrooms.guest_token` (UNIQUE).
- Opening without login → registration page preserving the token; after sign-up + email verification, user is auto-enrolled as `chatroom_guests` with `joined_via_token = guest_token` (R6.11 / R13.06).
- **Guest links have NO expiry, NO use cap, NO password, NO revoke** (R6.12 / R13.07). To revoke, delete the chat room or ban the user.
- Guests scoped to their room only (R5.04); global guard checks `chatroom_guests` membership alongside normal members.
- Guest search (R13.18) restricted to rooms they belong to.

**Key IDs.** `[R6.11]`, `[R6.12]`, `[R13.05]`–`[R13.07]`, `[R13.18]`.

**Exit criteria.** Token URL lands on registration flow; invalid token → 404; banning a guest user yields 403 on room enter.

## F.10 FTS search & export — **CODE** — S

**Deliverables.**

- `GET /api/chatrooms/{id}/search?q=` uses `content_tsv` + `ts_rank_cd`; snippet via `ts_headline` (R13.18). Scope-filtered (Guests only see their rooms).
- Export job (R13.17):
  - `POST /api/chatrooms/{id}/export` queues an Arq job returning `{job_id}`.
  - Worker writes JSON manifest + attachments folder into MinIO `exports` bucket (24h lifecycle).
  - `GET /api/exports/{job_id}` returns a short-lived signed URL.
  - Manifest preserves raw markdown AND sanitised HTML (R13.14).

**Key IDs.** `[R13.17]`, `[R13.18]`, §22.10.

**Exit criteria.** 1M-row corpus: search p95 < 200 ms; export a 10k-message room completes.

## F.11 Frontend conversation slice — **CODE** — L

**Objective.** `slices/conversation/` fully built (per §24.2).

**Deliverables.**

- Views: `WorkspaceListView`, `ChatroomListView`, `ChatroomView` (message list, composer, attachment dropzone, presence list, agent panel, search bar), `ChatroomSettingsView`, `GuestLandingView`.
- `useChatroomSocket(roomId)` composable is the sole WS subscriber; emits into TanStack Query (`setQueryData`/`invalidateQueries`) and a slice Pinia store for presence / ephemeral state (R24.21 / R24.22).
- Reconnect replays via `GET /api/chatrooms/{id}/messages?since=<last_id>` (R24.23).
- Composer: markdown editor with live preview, `@agent` mentions, `/compact` slash command (other slash commands added in G).
- Attachment UX: drag-drop → tus client (`/api/tus`) with progress, pause, resume, cancel.
- Guest landing: strips the token from Router history via `history.replaceState` to `/c/<chatroom_id>` (R24.43).
- Render pipeline: `lib/renderMarkdown.ts` = single `v-html` site.

**Key IDs.** §24.2, §24.7, §24.21–§24.23, §24.43, §22.10, §22.14, §22.15.

**Exit criteria.** Two browsers live chatting; 600 MB tus upload mid-network blip; guest link flow completes.

## F.∞ Phase gate

- [ ] Workspaces present; last-chatroom auto-creates default.
- [ ] Chatrooms match §21.1 flag set exactly.
- [ ] Messages use `sender_type/sender_id/content_md/content_tsv/metadata`.
- [ ] Manual delete hard-deletes; moderator edit audited.
- [ ] tus `/api/tus` with full metadata contract; 16MB chunk cap; 24h abandoned TTL.
- [ ] `chat-uploads / rag-sources / exports` buckets with correct lifecycles.
- [ ] WS endpoints: five separate paths + subprotocol auth + in-socket refresh.
- [ ] Mermaid + KaTeX + highlight render after DOMPurify.
- [ ] Guest link is permanent; `chatroom_guests` row on join; no revoke path.
- [ ] Search p95 < 200 ms; export job + signed URL work.
- [ ] `00-overview.md` §0.8: F = done.

## Cross-cutting checklist

1. **AuthZ tap.** Capability 17 (send), 18 (guest invite), 19 (export), 20 (delete) per §5.2.
2. **Audit tap.** `chatroom.created/deleted`, `message.sent/deleted/exported`, `attachment.uploaded/expired/quarantined`, `guest.joined`, `message.edited_by_moderator`, `message.purged_by_retention`.
3. **Rate limit bucket.** `chat-send` 60/min/user (R19.02), `upload` 10/min/user, `tus-create` 20/min/user, `tus-patch` 300/min/user.
4. **Observability.** `ws_connections_active`, `ws_per_user_rejections_total`, `tus_upload_bytes_total`, `message_sanitize_rejections_total`, `export_jobs_total`.
5. **RFC 7807.** `https://smap.local/problems/{message-edit-window, message-immutable, attachment-too-large, attachment-quarantined, tus-offset-mismatch, ws-per-user-limit}`.
6. **Migration policy.** `0009_workspaces`, `0010_chatrooms`, `0011_messages` — each N-1 compatible; content_tsv trigger in its own revision.
7. **Secrets.** None new; guest tokens are non-secret (room-scoped identifiers).

## Risks

- **WS fan-out under 100 users.** If Redis pub/sub lags, swap to per-room Redis Streams (flagged §26).
- **tus corruption.** Library + server verify offsets per chunk; final object SHA-256 compared to `Upload-Metadata` hash when supplied.
- **Markdown-HTML bypass.** Double sanitisation + `v-html` ESLint allowlist (J) + CSS url/import/expression strip.
- **Guest over-exposure.** Banning a user immediately 403s their guest sessions via `ws/user` ban-kick.
