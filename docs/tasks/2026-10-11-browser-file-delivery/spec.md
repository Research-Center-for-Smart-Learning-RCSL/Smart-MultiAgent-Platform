---
type: bugfix
status: implemented
created: 2026-10-11
requirements: [R13.10, R13.11a, R13.17, R13.71, R31.21, R33.10]
depends_on: []
---

# Browser file delivery through a backend-streamed ticket endpoint

## 1. Summary

None of the platform's downloadable files reach a browser. Every browser-facing file URL is
a MinIO presigned URL signed against the Docker-internal endpoint `minio:9000`. No browser
can resolve that host, and the page CSP blocks it as an image source as well. The one
attempted workaround, the nginx `/minio-assets/` proxy used by canvas images, returns 502,
because nginx and MinIO share no network.

As a result, all of the following fail at the moment of delivery:

- chat history export, the failure reported by the requester;
- research data export;
- the admin audit CSV;
- skill bundle export;
- chat attachment images and attachment downloads;
- canvas images.

The backend work behind each of them succeeds.

This dossier replaces presigned URLs with a single backend endpoint, `GET /api/files/{ticket}`.
The endpoint verifies a short-lived HMAC-signed ticket and streams the object from MinIO. The
dossier also repairs the frontend delivery defects that sit on the same paths: download links
minted long before the click, popup-blocked `window.open` calls, a download that replaces the
chat tab, and canvas's doubled `/api` prefix.

It originates from `docs/audits/2026-10-11-chat-and-research-export/findings.md`: F-1, F-2 and
F-7, plus the shared root cause recorded there as FU-6. It also takes in two findings from
`docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md` that sit on the same
download path: F-5 (the research ZIP is reported ready after it is purged) and F-6 (the research
download is popup-blocked).

## 2. Observed vs Expected

**Observed.**

- `MinioClient.presigned_get` signs against `section.endpoint`
  (`backend/shared_kernel/storage/minio_client.py:44-50,214-248`), which defaults to
  `minio:9000` over plain HTTP (`backend/app/config/settings.py:101,107`). No overlay overrides
  it.
- Six call sites return the result to a browser unchanged or nearly so:
  1. Chat export: `backend/app/api/v1/exports.py:137-142`, 15 minutes.
  2. Research export: `backend/app/api/v1/research_export.py:72,129-134`, 72 hours.
  3. Admin audit CSV: `backend/app/api/v1/admin_audit.py:152-156`, 1 hour, returned as a raw
     dict at `:166`.
  4. Skill bundle export: `backend/app/api/v1/skills.py:1760`.
  5. Attachments: `backend/contexts/conversation/application/attachment_service.py:307-313`.
  6. Canvas images: `backend/app/api/v1/canvas.py:627-628`. This site rewrites the URL to
     `/minio-assets/...` through `_minio_url_to_proxy` (`:39-47`).
- Reproduced on the compose test stack on 2026-10-11 (audit §2):
  - All three chat export formats reached `ready`, and the returned URL failed to resolve from
    the host.
  - The same URL returned HTTP 200 from inside the compose network.
  - Through nginx, `/minio-assets/<same path and query>` returned 502, and the nginx log reads
    `minio could not be resolved (3: Host not found)`. Nginx is attached to `backend_net` and
    `frontend_net` only, while MinIO is attached to the internal `data_net` only
    (`deploy/compose/docker-compose.yml:14-22`, resolved compose config). No staging or prod
    overlay changes nginx's networks (`deploy/compose/docker-compose.staging.yml:125-142`,
    `docker-compose.prod.yml:78-96`).
- Canvas has a second, independent fault. It fetches the rewritten URL with the shared `http`
  instance, whose `baseURL` is `/api` (`frontend/src/shared/transport/axios.ts:176-183`).
  Axios prefixes any non-absolute path (`node_modules/axios/lib/core/buildFullPath.js:68-76`),
  so the request goes to `/api/minio-assets/...`
  (`frontend/src/slices/canvas/composables/useCanvasSocket.ts:50-51`). The failure is
  swallowed at `:66-68`.
- Even with a reachable URL, the frontend paths have their own delivery defects:
  - The chat export link is minted when the job becomes ready, and expires 15 minutes later,
    while the UI promises 24 hours (`useChatroomExport.ts:23-29`,
    `frontend/src/slices/conversation/locales/en.json:191`).
  - The chat export link is a same-tab `<a href>` with no download semantics
    (`ChatroomExportModal.vue:19-23`, `frontend/src/shared/ui/SButton.vue:40`).
  - The research export and admin audit downloads call `window.open` after an `await`, outside
    the user gesture (`ResearchExportButton.vue:58-59`, `AdminAuditView.vue:242-253`).
  - The research job reports `ready` for 72 hours (`research_export_service.py:19`), while the
    exports bucket deletes objects after 24 hours (`smap/bootstrap/minio_init.py:67-77,152-156`,
    `app/workers/tasks/retention.py:599-652`).

**Expected.**

- Every file the platform offers can be downloaded or displayed by the browser of a user
  entitled to it ([R13.17], [R31.21], [R33.10], [R13.71], and the attachment download paths of
  [R13.10]/[R13.11a]).
- A download control obtains its link at the moment it is used, so the link cannot have
  expired before the click (decided at audit triage, 2026-10-11, F-2).
- A download leaves the current page in place (`docs/UI/07-conversation.md` §3.9; reworded in
  §11).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How does a browser reach MinIO objects? | The backend streams them through a new endpoint, `GET /api/files/{ticket}`. | Chosen over a dedicated nginx-to-MinIO network with the existing `/minio-assets/` proxy, and over attaching nginx to `data_net`. Bytes go through the application, so the edge container gains no route into the data plane, and every delivery passes one code path where headers, rate limits and logging are enforced. The cost is that downloads occupy backend-web connections; §9 addresses this. |
| Q-2 | How is a request authenticated, given that `<img>`, anchors and `window.open` cannot send a Bearer header? | A short-lived ticket in the URL path, HMAC-signed with the existing Vault KV `smap/config/hmac-key` under a domain-separation prefix. The ticket carries bucket, key, expiry, response content type and disposition. | Same semantics as the presigned URL it replaces: a short-lived bearer capability, reusable within its lifetime. Chosen over fetching through axios into a Blob, which holds whole files (up to 1 GiB) in browser memory, needs `blob:` in `img-src`, and would rewrite all six consumers. Also chosen over re-checking the resource's authorization at download time, which needs a per-resource permission hook for no gain over today's model. |
| Q-3 | Which consumers are in scope? | All six server-side call sites. On the frontend: chat export (F-2, F-7), research export (2026-10-05 F-5, F-6), the admin audit popup, the attachment URL memo, and the canvas prefix. Research export F-12, F-13 and F-14 are excluded. | All six sites share the root cause, so fixing only exports would leave attachments and canvas broken by the same mechanism. The excluded research findings concern job state and authorization, not delivery. |
| Q-4 | What does the chat export Download button do? | It re-fetches `GET /api/exports/{job_id}` at click time for a fresh ticket, then triggers a same-origin anchor download. The file is served `attachment` with a readable filename. The UI copy describes how long the archive is retained, not how long the link lives. | Decided at audit triage (F-2). An anchor download involves no new window, so no popup blocker applies, and the chat page stays loaded. |
| Q-5 | What happens to nginx `/minio-assets/` and `canvas.py`'s `_minio_url_to_proxy`? | Both are removed. | The proxy cannot work without a network change that Q-1 rejected. Its only consumer moves to `/api/files/`. |
| Q-6 | Which types may render inline now that files are served from the app origin? | Every `/api/files` response carries `Content-Security-Policy: sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'` and `X-Content-Type-Options: nosniff`. `application/pdf` leaves the inline allowlist and downloads. Images and `text/plain` stay inline. | SEC-M2's allowlist (`attachment_service.py:60-75`) assumed a separate storage origin. On the app origin, a hostile document gains same-origin reach, so the sandbox removes script and origin privileges. Chrome refuses to render a PDF in a sandboxed document, and a user-supplied PDF is the largest remaining same-origin surface, so PDF moves to download. Accepted cost: clicking a PDF attachment downloads it instead of previewing it in a tab. |
| Q-7 | How is `/api/files` rate-limited, given that browser loads carry no principal and a classroom shares one NAT IP? | A new `FILES` bucket keyed by ticket signature, default 60 per minute per ticket. The signature is verified before the bucket is charged. Nginx serves `/api/files/` from its own location and zone instead of the per-IP `api` zone. | Keying by IP (the `OTHER` bucket at `rate_limit.py:80`, which becomes `anon:{ip}` without a principal, `shared_kernel/auth/ratelimit.py:96,175-177`) would make students behind one IP throttle each other on image-heavy rooms. A per-ticket limit bounds the abuse that matters, a leaked link replayed in bulk. Nginx's `api` zone (`nginx.conf:46`, 30 r/s, burst 60 per IP) would impose the same per-IP squeeze at the edge. |
| Q-8 | Ticket lifetime, and what the frontend memo does with it. | 15 minutes for every site. `AttachmentImage`'s module memo evicts entries 60 seconds before ticket expiry and stops caching failures. | 15 minutes matches the shortest current lifetime and is long enough for a slow image load. Click-time re-minting (Q-4) removes the need for longer lifetimes. The memo currently never evicts and caches `null` forever (`AttachmentImage.vue:7-22`), which a short ticket would turn into broken images on re-mount. |
| Q-9 | Research export: 72-hour job state against a 24-hour object (2026-10-05 F-5). | Research job state TTL becomes 24 hours, the same as the chat and skill jobs (`export_service.py:29`, `bundle_jobs.py:30`). A ticket for an object that no longer exists streams 404, which the frontend reports as "export expired". | The bucket lifecycle and the retention sweep both fix the object's lifetime at 24 hours. The job record should not outlive the thing it describes. A Redis TTL change is simpler than adding an `expired` status to two response enums. |
| Q-10 | Dependencies on other dossiers. | None. | The only non-implemented dossier on the board, `2026-10-07-display-name-validation`, mentions `canvas.py` at its `:266` label sanitizer, a different region from `:39-47,627-648`. That is not an overlap. |

## 4. Reproduction

Preconditions: the compose test stack (`docker-compose.yml` + `compose.test.yml`),
bootstrapped as in the `frontend:verify` skill, with nginx started (`up -d --no-deps nginx`).
The seeded user `e2e-user@example.com` owns a project.

1. Log in through `POST /api/auth/login`. Create an org, a project, a workspace and a room,
   and post one message.
2. `POST /api/chatrooms/{room}/export` with `{"format":"markdown","date_range":"all"}`, then
   poll `GET /api/exports/{job_id}` until `status == "ready"`.
3. Observe that `url` is `http://minio:9000/exports/<job>/export.md?...X-Amz-Expires=900...`.
   Fetching it from the host fails DNS resolution.
4. Request `https://localhost:10443/minio-assets/exports/<job>/export.md?<same query>`.
   Observe HTTP 502, and in `docker logs smap_nginx`, `minio could not be resolved`.

The scripts used on 2026-10-11 are reproduced in the audit's §2. Steps 3 and 4 are
deterministic.

## 5. Root Cause Analysis

1. A browser-facing file reference is produced by `MinioClient.presigned_get`
   (`minio_client.py:214-248`). That URL embeds the SDK client's endpoint (`:44-50`), which is
   necessarily the internal service address, because the same client also serves the backend's
   own reads and writes.
2. No layer translates that address into one the browser can reach. Five of the six call sites
   return it unchanged. The sixth (`canvas.py:39-47`) rewrites it onto an nginx location
   (`smap.conf:172-184`) whose upstream nginx cannot resolve, because the two containers share
   no network (`docker-compose.yml:14-22`).
3. **Root cause:** the design delivers files by handing the browser a URL into the storage
   plane, and the deployment deliberately isolates the storage plane from everything
   browser-facing. The first link whose correction prevents every symptom is the decision to
   expose a storage URL at all. Rewriting the host is not enough, as the canvas attempt shows,
   because something with a route to MinIO must carry the bytes.

Aggravating factors, each of which would break delivery on its own once the root cause is
fixed:

- The chat export URL is minted at ready time, not click time (`useChatroomExport.ts:28`).
- Navigation is same-tab with inline content types (`ChatroomExportModal.vue:19-23`,
  `chat_export_service.py:243-253`).
- `window.open` runs after an `await` (`ResearchExportButton.vue:59`, `AdminAuditView.vue:249`).
- Canvas's URL is prefixed with `/api` (`useCanvasSocket.ts:50-51`).
- Research job state outlives its object (`research_export_service.py:19`).
- The attachment memo never evicts (`AttachmentImage.vue:7-22`).

Why no test caught it: no test asserts the URL's reachability. The route tests mock
`presigned_get` and assert pass-through (`backend/tests/unit/test_research_export_routes.py:149-166`).
The e2e export spec accepts the failure text as a pass
(`frontend/e2e/14-export-attachments.spec.ts:41-45`). Canvas AC-3 of
`2026-09-14-canvas-crdt-bridge-defects` was left unticked for want of a running stack.

## 6. Blast Radius and Sibling Suspects

**Blast radius.**

- Every deployment built from these compose files: staging (smap.rcsl.online) and prod share
  the base network layout and nginx config.
- Six features cannot deliver a file. No data is corrupted. Objects exist and expire on their
  normal lifecycle, so nothing needs repair.

**Sibling suspects.**

- **Chat export.** Confirmed at runtime (§4).
- **Research export.** Confirmed: the same raw URL is returned (`research_export.py:130-134`),
  and it is additionally popup-blocked (`ResearchExportButton.vue:59`).
- **Admin audit CSV.** Confirmed by code: the raw URL is returned at `admin_audit.py:152-166`
  and opened with `window.open` after an `await` at `AdminAuditView.vue:242-253`.
- **Skill bundle export.** Confirmed by code: the raw URL at `skills.py:1760` is followed by an
  anchor click (`useBundleTransport.ts:116-126`).
- **Attachment image and download.** Confirmed by code:
  - `<img :src>` with a raw `http://minio:9000` URL is blocked by the CSP `img-src 'self' data:
    https:` (`smap.conf:191`), and is unresolvable regardless.
  - The download path at `useChatroomMessages.ts:369-380` navigates a pre-opened tab to the
    same host.
- **Canvas image.** Confirmed at runtime: the `/minio-assets` proxy returns 502. The `/api`
  prefix fault is confirmed by code.
- **Other `presigned_get` callers.** Cleared: the six above are every non-test caller
  (exhaustive grep, 2026-10-11).
- **Server-side MinIO reads.** Cleared: `get_object`, `download_to_path`, and the worker
  uploads and reads run inside the compose network and are unaffected.

## 7. Fix Design

**7.1 Ticket service (`shared_kernel/storage/file_tickets.py`, new).**

`issue(bucket, key, *, content_type, disposition, ttl=15 min) -> str` returns the path
`/api/files/{ticket}`.

- The ticket is `b64url(payload) + "." + b64url(mac)`.
- The payload is compact JSON `{"v":1,"b":bucket,"k":key,"t":content_type,"d":disposition,"e":exp_epoch,"kv":hmac_key_version}`.
- `mac = HMAC-SHA256(key_for_version, b"smap-file-ticket:v1\x00" + payload_bytes)`. The fixed
  prefix gives domain separation from the provider-secret envelopes that share the seed.

`verify(ticket) -> FileTicket` has three outcomes:

- It raises `TicketInvalid` on a malformed ticket, a bad MAC, or a bucket outside the
  browser-deliverable allowlist (`chat-uploads`, `exports`, `skill-bundles`, taken from
  `MinioSection`). The MAC comparison uses `hmac.compare_digest`.
- It raises `TicketExpired` once the expiry has passed.
- Otherwise it returns the ticket.

Vault needs two public methods next to the private `_current_hmac` and `_hmac_for_version`
(`shared_kernel/infra/vault.py:215-275`): `hmac_current() -> (version, key_bytes)` and
`hmac_for_version(version) -> key_bytes`. Both are cached in process, so per-request cost is a
local HMAC. A refresh every 300 seconds is a blocking KV read and runs in `asyncio.to_thread`
(`vault.py:52,215-237`).

**7.2 MinIO streaming (`minio_client.py`).**

`open_stream(bucket, key, chunk=1 MiB) -> (ObjectStat, AsyncIterator[bytes]) | None`:

- It calls `get_object` in a thread and reads each chunk with `to_thread(resp.read, chunk)`.
- `close()` and `release_conn()` run in `finally`, so an aborted client download releases the
  pool connection.
- `NoSuchKey` returns `None`.

This is the first streaming path in the codebase. No `StreamingResponse` exists today.

**7.3 Endpoint (`app/api/v1/files.py`, new; registered in `app/api/v1/__init__.py`).**

`GET /api/files/{ticket}` takes no principal dependency, so it is public by omission, like
`healthz.py:10`. Responses:

| Condition | Status |
|---|---|
| `TicketInvalid` | 404 |
| `TicketExpired` | 410 |
| Object missing | 404 |
| Otherwise | 200 `StreamingResponse` |

A 401 would make the axios interceptor attempt a refresh and possibly log the user out
(`axios.ts:282-308`), so no failure answers 401.

The 200 response headers are:

- `Content-Type` and `Content-Disposition` from the ticket.
- `Content-Length` and `ETag` from the stat.
- `Cache-Control: private, max-age=<seconds until expiry>`.
- The sandbox CSP and `nosniff` from Q-6.

`SecurityHeadersMiddleware` sets its headers with `setdefault`
(`app/api/middleware/security_headers.py:33-52`), so the handler's CSP wins. `HEAD` is answered
from the stat alone.

**7.4 Rate limiting.**

- Add `Bucket.FILES` to `shared_kernel/auth/ratelimit.py:55-96` with `Scope.TICKET`, keyed by
  the ticket's MAC. The setting is `files_per_min_ticket = 60`.
- Add `/api/files/` to the middleware's exemption list (`app/api/middleware/rate_limit.py:85-98`).
  The middleware cannot verify the MAC before charging, and without the exemption it would
  still charge the per-IP `OTHER` bucket (`:80`).
- The route charges `FILES` itself, after verification succeeds. An unverified ticket costs one
  local HMAC and is never charged.
- Nginx gets `location /api/files/` with its own zone, `files`. The zone is defined in
  `conf.d/smap.conf`, not `nginx.conf`, for the bind-mount reason recorded at `smap.conf:15-22`.
  The location is `proxy_buffering off` and keeps the `/api/` proxy headers.

**7.5 Call sites.**

Each `presigned_get` call becomes `file_tickets.issue(...)`, and `MinioClient.presigned_get` is
deleted so the mistake cannot recur.

- **Attachments** keep their content-type and disposition logic (`attachment_service.py:295-313`)
  and remove `application/pdf` from `_INLINE_SAFE_MIME` (Q-6).
- **Chat export** stores `room_name` in `ExportJobState` at create time (`access.chatroom.name`
  is already loaded at `exports.py:98`; `get` tolerates older records). The ticket uses
  `attachment; filename*=UTF-8''<room-name>-<YYYYMMDD>.<ext>`, with an ASCII `filename=`
  fallback built by `_safe_header_filename` (`attachment_service.py:78-82`). Room names are
  frequently CJK.
- **Research export** gets the same disposition handling with `research-data-<YYYYMMDD>.zip`,
  and `_JOB_TTL_SECONDS` becomes 24 hours (Q-9).
- **Admin audit** issues an `attachment` ticket for `audit-<from>-<to>.csv`, and its raw-dict
  return becomes a Pydantic `AuditExportOut` (this regenerates the client).
- **Skills** keeps its existing disposition.
- **Canvas** emits the ticket path. `_minio_url_to_proxy` and the nginx `/minio-assets/`
  location are deleted (Q-5).

**7.6 Frontend.**

- A shared `triggerDownload(url, filename?)` in `src/shared/utils/download.ts` appends an anchor
  with `download` (same-origin now, so honoured), clicks it, and removes it. It is merged from
  `useBundleTransport.ts:116-126` and the URL half of `useCanvasExport.ts:26-35`.
- **Chat export.** `useChatroomExport` exposes `download()`, which calls
  `getExport(activeJobId)` and then `triggerDownload(url)`. The modal's Download button calls
  it instead of binding `href`. The expiry copy changes (en and zh-TW) to "The archive is
  available for 24 hours." A 404 or 410 shows an "export expired" message offering a new
  export.
- **Research export.** On ready, it shows a Download button that re-fetches the status and
  calls `triggerDownload`, replacing the `window.open` at `:59`.
- **Admin audit** calls `triggerDownload(result.url)`, replacing `window.open` at `:249`.
- **Skills** uses the shared helper.
- **Attachment download** (`useChatroomMessages.ts:369-380`) keeps the pre-opened tab for
  inline types (images, `text/plain`) and uses `triggerDownload` for `attachment`
  dispositions, so a PDF no longer opens a blank tab.
- **`AttachmentImage.vue:7-22`.** Memo entries record `expiresAt`, taken from the ticket
  lifetime the API now reports (`expires_in` added to `AttachmentDownloadOut`). They are evicted
  60 seconds before expiry, and rejected fetches are not memoized.
- **Canvas** (`useCanvasSocket.ts:50-51`) requests with `{ baseURL: '', responseType: 'blob' }`,
  the precedent at `shared/composables/useNetworkStatus.ts:53`.

**7.7 Quality notes.**

*Existing debt in the touched files.* Two items are fixed here, because the change already
rewrites them:

- `admin_audit.py:117,166` returns a raw dict, against the route rule in `backend/CLAUDE.md`.
- `admin_audit.py:139-150` imports locally inside the handler.

The rest is recorded, not fixed:

- Routes import application modules directly: `exports.py:18-25` and `research_export.py:17-18`
  (already research-export FU-4).
- `export_service.py:184` keeps a dead `timedelta` reference.
- The `AttachmentImage` memo is module-global state.

Do not imitate any of these in new code.

*Patterns to follow.*

- `file_tickets.py` lives in `shared_kernel/storage` and imports only `shared_kernel`. It must
  not import a context.
- Every call site reaches it through the existing `shared_kernel.storage` export list
  (`minio_client.py:334-348` and the package `__init__`).
- `files.py` is a thin route, like `healthz.py`, with no context facade, because it serves no
  context's domain.
- Typed domain errors (`TicketInvalid`, `TicketExpired`) are mapped in the route, not by string
  matching.
- Frontend downloads go through one helper in `src/shared/utils/`. Slices import it from
  `@shared`, never from each other.

*Reuse inventory.*

- `VaultClient` HMAC key cache and version history (`vault.py:215-275`).
- The private `_b64url` and `_b64url_decode` helpers (`vault.py:466-474`), made shared rather
  than copied.
- `ObjectStat` and the `download_to_path` streaming loop (`minio_client.py:144-177`) as the
  template for `open_stream`.
- `_INLINE_SAFE_MIME` and `_safe_header_filename` (`attachment_service.py:65-82`).
- The `RateLimiter` sliding-window check (`shared_kernel/auth/ratelimit.py:185-220`).
- `get_vault_client` (`shared_kernel/auth/clients.py:61`).
- The per-request `baseURL` override precedent (`useNetworkStatus.ts:53`).
- `useToast` for the expired message.
- The existing locale files of each slice.

**7.8 Security considerations.**

- **Capability scope.** A ticket grants one object for at most 15 minutes and names a bucket
  only from the allowlist. `rag-sources`, `agent-workspace`, `prompt-assistant-files` and
  `knowmap-sources` can never be served.
- **Forgery.** The MAC is keyed by a Vault-held seed with domain separation and compared in
  constant time. The payload is authenticated before it is parsed beyond base64.
- **Leakage.** The nginx JSON access log records `$request_uri` (`nginx.conf:23-25`). On
  2026-10-11 it recorded a full presigned query, signature included. The `/api/files/` location
  therefore uses an access-log format that writes the path prefix only. The uvicorn access log
  must not record the ticket either: `/build` verifies whether it is enabled in the compose
  command and, if it is, adds a redacting filter. Tickets must not appear in audit metadata,
  matching the rule the chat export audit already follows for URLs
  (`chat_export_service.py:180-184`).
- **Same-origin content.** See Q-6. `nosniff` plus the sandbox CSP, with no scriptable type
  inline.
- **Impersonation.** Minting endpoints are already blocked for impersonation sessions
  (`impersonation.py:26-39`). The files endpoint needs no principal, so it adds no bypass.
- **Denial of service.** The per-ticket limit, the nginx `files` zone, chunked reads, and
  release of the connection on disconnect.

**Why this is not masking.** No browser-facing code path receives a storage-plane address after
this change, because the function that produced one no longer exists.

## 8. Regression Test Plan

Write these failing tests first:

- **`backend/tests/unit/test_file_tickets.py` (new).** Issue/verify round trip, tampered payload,
  tampered MAC, expired ticket, foreign bucket, wrong domain prefix, and an unknown key
  version. These fail today because the module does not exist.
- **`backend/tests/unit/test_files_route.py` (new).** Using a fake streaming MinIO:
  - 200 with the headers of §7.3, including the sandbox CSP that overrides the middleware
    default;
  - 404 for invalid and missing, 410 for expired, never 401;
  - HEAD;
  - the stream is closed when the consumer stops early.
- **Six call-site tests.** Replace the pass-through assertions at
  `test_research_export_routes.py:149-166`, `test_attachment_expiry_sweep.py:236-245`,
  `test_conversation_services.py:929-957` and `test_canvas_crdt_bridge_defects.py:185-203`. Add
  new tests for `exports.get_export`, the admin audit export and the skill bundle status. Each
  asserts the URL starts with `/api/files/`, contains no `minio` host, and decodes to the
  expected bucket, key and disposition. They fail today because every URL is
  `http://minio:9000/...` or `/minio-assets/...`.
- **`test_attachment_download_disposition.py`.** Add a case asserting that `application/pdf` is
  now served as `attachment`.
- **`backend/tests/db/test_files_stream.py` (`pytest.mark.db`, or a wiring-tier test against
  the stack's MinIO).** Put an object, issue a ticket, and GET it through the app. The bytes
  must equal the original. This is the test whose absence let F-1 ship.
- **Frontend unit tests:**
  - `triggerDownload`;
  - `useChatroomExport.download()` re-fetches before downloading;
  - the `AttachmentImage` memo evicts near expiry and does not cache failures;
  - `useCanvasSocket` requests `/api/files/...` with no `/api` prefix (it has no test today);
  - `ResearchExportButton` and `AdminAuditView` no longer call `window.open`.
- **`frontend/e2e/14-export-attachments.spec.ts`.** Remove "Failed to start export" from the
  accepted states, click Download with `page.waitForEvent('download')`, and assert a non-empty
  file whose name ends in `.md`. This runs against the full stack in CI and is the end-to-end
  pin.

## 9. Risks and Rollback

- **Backend-web carries file bytes.** A 1 GiB TUS attachment
  (`attachment_service.py:53`) now streams through a uvicorn worker instead of MinIO. Memory
  stays bounded by the 1 MiB chunk. Connection time does not, so a slow client holds a
  connection for the download's duration. Mitigations: chunked reads off the event loop, and
  nginx `proxy_buffering off` so nginx does not spool the file to disk. Revisit if staging shows
  pool or worker saturation (FU-1).
- **Range requests are not supported.** Video seeking and resumable downloads are not
  supported. MinIO did support them through presigned URLs, but no current consumer relies on
  them.
- **HMAC key rotation.** Old tickets stay valid by version for up to 15 minutes, since
  `hmac_for_version` reads KV history (`vault.py:239-275`). A key outage after startup is
  covered by the cached key (`vault.py:224-233`).
- **Same-origin serving.** This is a deliberate change of trust boundary. Q-6's sandbox CSP is
  the guard, and `/check-security` must review it during `/build`.
- **API contract changes.** The `url` fields keep their names and types, so the generated
  client is unaffected, except for the new `AuditExportOut` and `expires_in`. `pnpm run gen:api`
  is part of the change.
- **Rollback.** Revert the series. Nothing is persisted in a new format, and the Redis job
  records gain one optional field that older code ignores.

## 10. Acceptance Criteria

- [x] AC-1: The §8 backend tests (`test_file_tickets.py`, `test_files_route.py`, and the six
  call-site URL assertions) fail before the fix and pass after. Each was run red first: module
  absent, route absent, or `http://minio:9000` / `/minio-assets/` URLs.
- [x] AC-2: On the compose test stack with nginx, a chat export in each of markdown, json and
  pdf downloads through `https://localhost:10443/api/files/...` with HTTP 200. The bytes match
  the object in MinIO, and the `Content-Disposition` carries a `filename*` with the room name.
  Verified 2026-10-11: sizes 152, 517 and 81,282 bytes equal the MinIO objects;
  `filename*=UTF-8''%E8%AE%80%E6%9B%B8%E6%9C%83-...`.
- [x] AC-3: With 16 minutes between the job reaching `ready` and the click, the Download button
  still downloads the archive (the link is minted at click time). Verified live: after 16 minutes
  the old link returned 410, and a fresh status call returned a link that served 200.
- [x] AC-4: Clicking Download leaves the chatroom loaded, with no navigation and no new tab, and
  saves a file (e2e `waitForEvent('download')`). The updated `14-export-attachments` spec passed in
  Chromium against the stack with a full 11-key seed.
- [x] AC-5: A research export downloads via its Download button with no popup-blocker prompt. A
  research job older than 24 hours reports not-found instead of `ready`. Live download returned
  200 `application/zip`, with no `window.open` (unit test). The job record TTL was observed at
  86,398 s; the 24-hour expiry itself was not waited out.
- [x] AC-6: The admin audit CSV and a skill bundle each download through `/api/files/`. Both
  were verified live with 200 and `attachment` disposition.
- [x] AC-7: An attachment PNG renders inline in the bubble. An attachment `text/plain` opens in
  a tab. An attachment PDF downloads. Each response carries the sandbox CSP and `nosniff`. The
  PNG decoded in Chromium (`naturalWidth > 0`). Headers were checked live: text `inline`, PDF
  `octet-stream` + `attachment`, sandbox CSP on all three. The text-in-tab behavior is pinned by
  a unit test.
- [x] AC-8: A canvas image uploaded in one browser renders in a second browser. The request goes
  to `/api/files/...`, not `/api/api/...` or `/minio-assets/...`. Verified in Chromium: an
  upload by a second client reached the open canvas over the socket, and the browser fetched
  `/api/files/...` with 200 and the exact bytes, with no request to either old path. The pixel
  rendering inside Excalidraw was not inspected.
- [x] AC-9: An expired ticket returns 410 and a tampered ticket returns 404, and neither logs the
  user out of the SPA. Both statuses were observed live through nginx. No path answers 401
  (`files.py`), which is unit-tested.
- [x] AC-10: Sixty-one requests for one ticket within a minute yield a 429 on the 61st. Thirty
  concurrent sessions behind one IP, each loading a room with 20 images, are not throttled.
  Verified live: requests 1-60 returned 200 and the 61st returned 429. 600 image loads from one
  IP (60 sessions × 10 images, 100 in parallel) all returned 200. The first burst attempt
  exposed D-3.
- [x] AC-11: `grep -r presigned_get backend --include=*.py` finds no non-test caller, and
  `smap.conf` has no `minio-assets` location. `MinioClient.presigned_get` itself is deleted.- [x] AC-12: The §11 SRS and UI-doc changes are applied, and `scripts/traceability.py --check`
  passes.

## 11. SRS Delta

Bugfix dossiers usually carry none. This one changes the delivery mechanism the SRS names, so
the following edits were applied at approval on 2026-10-11. The new security requirement is [R19a.14], in a new §19a.7. One further edit made at the same time, for consistency: the `GET /api/attachments/{id}` row (`REQUIREMENTS.md:1696`) changed from "short-lived signed download URL" to "short-lived download link".

The edits:

- **`REQUIREMENTS.md:1694`.** Change the row to `| GET | /api/exports/{job_id} | Job status;
  when ready, a download link minted per request (valid 15 min). The archive is retained 24 h. |`
- **`REQUIREMENTS.md` API table.** Add the row `| GET | /api/files/{ticket} | Streams a stored
  file for a short-lived signed ticket issued by another endpoint; 404 invalid, 410 expired. |`
- **`REQUIREMENTS.md:1818`.** Change "`.zip` via presigned URL" to "`.zip` via a download link".
- **[R13.11a] (`:720`).** Change "the read path refuses to presign an attachment" to "the read
  path refuses to issue a download link for an attachment".
- **[R13.71] (`:832`).** Change "a presigned MinIO URL" to "a short-lived download link".
- **[R33.10] (`:2382`).** Change "delivered through a time-limited presigned URL" to "delivered
  through a download link minted when the user downloads".
- **New [R19a.xx] in the security chapter.** "Browser-facing files are served only through
  `/api/files/{ticket}`. Tickets are HMAC-signed, expire within 15 minutes, and name a bucket on
  an allowlist. Every response carries `Content-Security-Policy: sandbox` and `nosniff`. A
  storage-plane URL is never returned to a client." The number is taken from the chapter's next
  free ID at approval.
- **`docs/UI/07-conversation.md:864,886`.** Change the expiry line to "The archive is available
  for 24 hours." Change the Download button description to "fetches a fresh link and downloads
  the file; the chatroom stays open."

## 12. Deviation Log

- **D-1.** Vault exposes `hmac_sign` / `hmac_verify`, which return and check MACs, in place of
  §7.1's `hmac_current` / `hmac_for_version`, which returned key bytes. The seed never leaves
  `VaultClient`. `hmac_verify` also bounds the caller-chosen seed version to `1..current` and
  negatively caches an unreadable old version for 30 s. A failed seed refresh backs off 30 s
  instead of retrying per request (quality W2/W3, security F2).
- **D-2.** The per-ticket limit uses `ratelimit.check_raw` with key `rl:files:t:<hash>` and the
  setting `SMAP_LIMIT_FILES_PER_MIN_TICKET` (default 60), not a new `Bucket.FILES`. Reason:
  `rate_limit_policies.scope` is CHECK-constrained to user/ip/user_and_ip, so a ticket scope
  would need a migration. Consequence: the limit is not tunable through
  `/api/admin/rate-limits`.
- **D-3.** Each ticket carries a random nonce (`"n"`). Found by AC-10: tickets for one object
  minted in the same second were byte-identical and shared one 60/min budget (540 of 600 loads
  got 429).
- **D-4.** Filenames go through one RFC 6266 helper, `file_tickets.content_disposition`, which
  writes an ASCII `filename` plus a UTF-8 `filename*`. A raw CJK filename is not latin-1 and
  would have failed the response as a header value. It replaces `_safe_header_filename`.
- **D-5.** `AttachmentOut` gains `inline: bool`, computed by
  `attachment_service.serves_inline`, which is the same rule the link applies. The frontend
  reads it instead of mirroring the MIME allowlist (quality W2).
- **D-6.** Security-gate additions beyond §7:
  - `SAFE_INLINE_TYPES` in `file_tickets` is the single inline allowlist, and `issue()` refuses
    `inline` for anything else (H5). The attachment allowlist now aliases it.
  - Canvas images are served `attachment`, because the canvas fetches them into a blob, so an
    SVG never renders when opened directly.
  - Downloads served `attachment` are `no-store`; inline ones are `private, max-age` (H4).
  - OTel excludes `/api/files/` (H2).
  - The redacting nginx access-log format applies to both servers, not only the location (F3).
- **D-7.** Export artifact names and types live in one domain table,
  `contexts/conversation/domain/export_formats.py`, used by the worker and the route (quality
  W5). As a result the stored markdown object's content type is now
  `text/markdown; charset=utf-8`.
- **D-8.** An expired chat export shows a toast suggesting a new export. The ready state does not
  gain a "New export" button (§7.6 said "offering a new export"). The user closes and reopens the
  modal, which resets it.
- **D-9.** The research dialog stays open on ready with a Download button, and clears that state
  when closed. Its 72-hour presigned URL is replaced by a link minted per click.
- **D-10.** The route returns its 429 through `problem_response`, because the shared
  `HTTPException` handler drops `headers` (`Retry-After`). The handler bug itself is FU-10.
- **D-11.** The nginx `files` zone is 100 r/s with burst 600 per client key (§7.4 gave no
  numbers). The wiring CI job now starts `minio` for `tests/wiring/test_file_delivery.py`.
- **D-12.** `ObjectStream` (`open_stream`) returns the connection to the pool at EOF and closes
  it on abort. The route also releases it in a `BackgroundTask` (quality W1).
- **D-13 (resolves FU-4, done on 2026-10-11 at the requester's choice before opening the PR).**
  `nginx.conf` uses the `realip` module and keys every zone on `$binary_remote_addr`, replacing
  the left-most-hop `$limit_key` map:
  - only 127.0.0.0/8 and the Docker 172.16.0.0/12 may vouch for `X-Forwarded-For`;
  - `real_ip_recursive on`, so the right-most hop the trusted proxy added wins.

  Probe (simulated NPM: rotating client-sent first hop, fixed appended peer, 1,200 forged-ticket
  requests):
  - before the fix, 0 limited;
  - after it, 84 × 503, matching the constant-key control (87 × 503).

  **Deploy note:** `nginx.conf` is a single-file bind mount, so the nginx container must be
  recreated, not reloaded. An operator whose front proxy reaches nginx from another range adds
  a `set_real_ip_from`.
- **D-14 (resolves FU-5, same date).** At most `SMAP_LIMIT_FILES_CONCURRENT_PER_TICKET` (default
  4) streams per ticket. The slots are a Redis sorted set with a 30-minute hold window
  (`ratelimit.acquire_slot` / `release_slot`). A slot is released when the response ends (see
  D-16). A slot whose release never runs is reclaimed after the hold window. HEAD takes no slot.
  Tested in the unit tier and against real Redis in the wiring tier (cap, release, reclaim).

The entries below come from a `/code-review` of the full branch diff after D-14. The user chose
to fix all seven findings before opening the PR.

- **D-15 (review finding 1, regression introduced by D-14).** `canvas.image_added` broadcast one
  ticket to the whole room, so once D-14 capped a ticket at four streams, a busy room got 429s
  and `useCanvasSocket` silently left a placeholder. The event now carries no link. Each client
  calls the new `GET /api/chatrooms/{id}/canvas/images/{file_id}/link` (room read access, same
  check as the object list) and gets a ticket of its own. The upload creates the object row with
  the element's `fileId` as its id, so the event's `fileId` resolves. Verified on the compose
  stack: 30 clients minting their own links loaded the image 30 x 200, while the control (one
  shared link, 30 parallel loads) got 10 x 200 and 20 x 429.
- **D-16 (resolves FU-6).** The object stream and the concurrency slot are released in the
  `finally` of a `StreamingResponse` subclass, shielded from cancellation, instead of a
  `background` task, which Starlette skips on ASGI spec 2.4's `ClientDisconnect` path and when
  an enclosing `BaseHTTPMiddleware` cancels the app. Unit tests cover both paths. The leak did
  not reproduce on the current uvicorn (it reports spec 2.3, where the background task runs):
  four aborted slow downloads of one link left no slot in Redis either before or after the
  change, so this is a correctness fix for the other paths rather than an observed failure.
- **D-17 (review finding 5).** `ObjectStream` serialises access to the urllib3 response. A
  release during an in-flight read (a cancelled `to_thread` keeps running) hands the close to
  the reading thread instead of closing the response underneath it.
- **D-18 (review finding 6, part of FU-1).** `GET /api/files/{ticket}` honours a single byte range
  (`bytes=a-b`, `a-`, `-n`) with 206 and `Content-Range`, answers 416 for a range past the end,
  and serves the whole object for multiple ranges, malformed headers or a stale `If-Range`.
  Responses advertise `Accept-Ranges: bytes`. MinIO reads only the requested bytes. Verified
  through nginx (last 100 bytes of an 8 MiB attachment, byte-identical) and in the wiring tier.
- **D-19 (review finding 3).** `hmac_verify` re-reads the current seed version when a ticket
  names a newer one, at most once per 30 s, so a ticket signed by a process that already picked
  up a rotation does not 404 on one that has not. A forged high version costs at most one KV read
  per window.
- **D-20 (resolves FU-7; review finding 7).** `location /api/files/` logs errors at `crit` only,
  since the error log cannot be redacted. The access-log format now exists once: `log_format json`
  and the http-level `access_log` moved from `nginx.conf` to `conf.d/smap.conf` beside the
  redaction map, and the per-server overrides are gone. The format keeps the name `json`, so a
  stale `nginx.conf` mount alongside the new `conf.d` fails `nginx -t` on the duplicate rather
  than logging tickets (checked). Probe of 1,500 forged-ticket requests past the edge zone:
  before, 622 error-log lines quoted the ticket; after, none, with all 1,500 access entries
  redacted. The nginx container must still be recreated on deploy (D-13).

## 13. Follow-ups

- **FU-1.** Measure backend-web saturation under concurrent large downloads on staging. If
  needed, move `/api/files` to a dedicated uvicorn process. HTTP Range support landed in D-18.
- **FU-2.** Research export F-12, F-13 and F-14 of the 2026-10-05 audit (notification failure
  marks ready jobs failed; authority checked only at request; a timeout leaves the job running).
  Their planned dossier `2026-10-05-research-export-delivery` was never created.
- **FU-3.** The `docs/UI/09-admin.md:910` wording about blocked "presigned URL" endpoints should
  be refreshed once the minting endpoints are renamed or unchanged. The middleware's segment list
  (`app/api/middleware/impersonation.py:26-39`) does not need `files`, because issuance is
  already blocked.
- **FU-4 (security F1, MEDIUM, pre-existing class). Resolved by D-13.** Every nginx zone keys on the first
  `X-Forwarded-For` hop (`nginx.conf:39-42`), which a client controls even behind NPM. For
  `/api/files/` that zone is the only per-client limit on unverified tickets, because the backend
  limits per verified ticket. Fix with the `realip` module and `$binary_remote_addr`.
- **FU-5 (security F4, MEDIUM). Resolved by D-14 (concurrency cap; no byte budget).** The per-ticket limit counts request starts, not concurrency or
  bytes: a leaked ticket can open about 900 streams in 15 minutes. Consider a per-ticket
  concurrency cap (Redis INCR/DECR released with the stream), a byte budget, and `limit_conn` on
  the location.
- **FU-6 (security F5, plausible). Resolved by D-16.** On Starlette's `ClientDisconnect` path the `BackgroundTask`
  is skipped and the iterator is not `aclose()`d, so a connection can wait for GC. Add a
  disconnect test against a real uvicorn, or release in a `StreamingResponse` subclass's
  `finally`.
- **FU-7 (security H3). Resolved by D-20.** The nginx `error_log` prints the request line, ticket included, for
  limit_req rejections and upstream errors. The access-log redaction does not cover it.
- **FU-8 (security H1, hardening).**
  - Derive the ticket key with HKDF from the seed instead of prefixing messages with the raw
    seed.
  - Consider `img-src 'none'` in the file CSP.
- **FU-9.** A ticket for a 200-character CJK filename can approach the 4,096-character path cap
  (`files.py`) and would 404. Raise the cap or shorten what the payload repeats.
- **FU-10 (pre-existing).** `shared_kernel/errors/handlers.py:115-137` drops
  `HTTPException.headers`, so for example `canvas.py`'s guest 429 loses `Retry-After`.
- **FU-11 (local environment).** The local venv lacked `regex` and still lacks its type stubs.
  `test_provider_adapters.py::test_gemini_percent_encodes_the_model_id_in_the_url_path` fails
  locally (respx route matching), with no file of this task involved. The full backend unit
  tier was otherwise green locally: 11,415 passed. CI is authoritative.
- **FU-12 (pre-existing).** `exports.py` and `research_export.py` import application modules
  rather than a facade. This task adds the `ExportJobState` import.
- **FU-13 (pre-existing frontend).**
  - `useChatroomMessages.downloadAttachment` navigates the current tab when `window.open` is
    blocked.
  - `AttachmentImage` does not watch `attachmentId`.
  - `ResearchExportButton` polls by hand rather than through `usePolling`.
  - The hand-written `AttachmentDownload` type duplicates the generated one.
- **FU-14 (accepted parity).** A ticket outlives the authorization that minted it by up to 15
  minutes, as the presigned URLs did.
- **FU-15.** `AgentToolsView.test.ts` (CodeMirror) failed once under full-suite load and passed
  in isolation, matching its known intermittency.
