---
type: feature
status: implemented
created: 2026-07-05
requirements: [R29.01, R29.02, R29.03, R29.04, R29.05, R29.06, R29.07, R29.08, R29.09, R29.10, R29.11, R29.12, R29.13, R29.14, R9.02, R5.03, R7.15]
---

# Prompt Assistant and Prompt Templates

## 1. Summary

Add an AI assistant to the agent create/edit flow that helps users draft the agent's
system prompt, plus a prompt-template library. Both are configurable at three scopes:
platform (system admin), organization (Org Owner), and personal (any verified user).
The assistant is a side chat panel next to the existing system-prompt editor; it calls
an LLM through the existing `ProviderRouter` using a key pinned by whoever configured
the assistant. Configurers may attach reference files (style guides, examples) whose
extracted text is inlined into the assistant's context. Templates are plain-text
prompts users can insert into the editor when creating an agent.

This feature amends two standing SRS exclusions: `REQUIREMENTS.md:49` (§1.2 excludes a
"template library") and `[R9.02]` at `REQUIREMENTS.md:394` ("no templates (Q41)"). The
SRS Delta in §13 removes/narrows both.

## 2. Goals and Non-goals

**Goals**
- A chat-style assistant panel in the prompt tab of `AgentDetailView.vue` that streams
  multi-turn help for drafting/improving the agent's system prompt, with a one-click
  "apply draft to editor" action.
- Assistant configuration (system prompt, reference files, pinned key, model, usage
  caps, enabled flag) at platform / org / user scope, with resolution chain
  user → org (for org-owned projects) → platform.
- Reference files: upload, virus-scan, text-extract, and inline full text into the
  assistant's context, under a hard total-size budget.
- Prompt templates (name + description + body) creatable at all three scopes, shown
  merged and grouped by source in the agent create/edit flow; Org Owners can hide
  platform templates for their org's projects.
- Full usage accounting and quota enforcement on assistant LLM calls, especially for
  the platform (admin) key.

**Non-goals**
- No RAG/vector retrieval over reference files (Q-7 chose full-text inline; the data
  model leaves room but this task builds none of it).
- No template variables/placeholders (Q-4) — templates are plain text; applying one is
  a one-shot copy into the editor with no persistent link.
- No template versioning, sharing between orgs, import/export, or marketplace.
- No assistant availability anywhere except the agent create/edit prompt tab (not in
  chatrooms, not for workflow node templates).
- No persistence of assistant conversations beyond the live session (ephemeral, Redis
  TTL); no history browsing.
- No changes to how *agents themselves* select keys/models at chat time.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Whose API key does the assistant use? | Follow the configurer's key: org config uses a key pinned by the Org Owner, personal config a key pinned by the user, platform config a platform key slot bound by the admin (see Q-8). | Clear cost attribution, consistent with BYO-key. Rejected "always current user's key" (users may lack a key for the assistant's provider) and per-call selection (churn). |
| Q-2 | Assistant interaction form? | Side chat panel in the create/edit agent view, multi-turn, with apply-to-editor. | Best UX for iterative prompt refinement; one-shot generate button rejected as too limited. |
| Q-3 | How are reference files fed to the AI? | Initially RAG retrieval — **superseded by Q-7**. | — |
| Q-4 | Template form? | Plain-text templates (name + description + body), no variables. | Simplicity; variables add editor/validation complexity without a demonstrated need. |
| Q-5 | Streaming transport? | Reuse the existing WS + Redis pub/sub pipeline; provider calls stay in the arq worker. | User directive. No SSE endpoint exists in the codebase and the frontend transport gate bans `EventSource` (`eslint.config.js:141`); the SRS tech-stack table (Q47, `REQUIREMENTS.md:144`) nominally allows SSE for one-way streams, but WS is the only implemented transport. Matches K-gate (`triple_extractor.py:12`). |
| Q-6 | Resolution chain inside orgs? | Org members MAY override the org config with their personal config. Chain: user config → org config (org-owned projects only) → platform config. | User chose flexibility over strict governance. |
| Q-7 | Reference-file ingestion, given RAG configs are per-project but assistant configs are org/user/platform scoped? | Downgrade to full-text inline (small files, hard total budget). Supersedes Q-3. | Avoids embedding-key requirement and scope mismatch with per-project `rag_configs`; assistant reference material (style guides, examples) fits comfortably in-context. |
| Q-8 | Where does the admin-default assistant's key come from? Keys are user-owned, key groups per-project — no platform mount point exists. | Platform key slot: the platform config pins an admin-uploaded key (existing envelope encryption + pinned-key call path). Reuse the key/model selector UI already in the agent form. Add usage caps to prevent abuse. | User decision, with explicit instructions on UI reuse and caps. |
| Q-9 | Template visibility? | Merged picker grouped by source (platform + org or personal); Org Owner can hide platform templates for org projects. | User chose the hide switch over always-showing platform templates. |
| Q-10 | Should providers (configurers) get a disable button? | Already covered — the config's `enabled` flag (R29.03) is the disable button; resolution skips disabled configs (R29.04). No change. | User confirmed the existing design suffices. |

## 4. Current State

**Agent prompt editing.** Create and edit share `AgentDetailView.vue`
(`frontend/src/slices/agents/views/AgentDetailView.vue`); create mode is
`routeAgentId === 'new'` (`AgentDetailView.vue:62-65`). The system prompt is one
`SCodeEditor` (markdown, 16 rows) inside the "prompt" tab (`AgentDetailView.vue:885-897`)
with `SCharCount` against `INPUT_LIMITS.SYSTEM_PROMPT` = 100 000
(`frontend/src/shared/constants/inputLimits.ts:15`). Backend cap matches:
`_MAX_SYSTEM_PROMPT = 100_000` (`backend/app/api/v1/agents.py:50`). The prompt is stored
as `agents.system_prompt` text (`backend/contexts/agents/infrastructure/tables.py:42`).

**No template or settings infrastructure exists.** There is no prompt-template concept
anywhere (only the lazy-strategy insert snippet, `AgentDetailView.vue:420-427`); the SRS
explicitly excludes templates (`REQUIREMENTS.md:49`, `[R9.02]` at `REQUIREMENTS.md:394`).
There are no per-org, per-user, or platform settings tables — `orgs`/`projects` have no
settings column (`backend/contexts/tenancy/infrastructure/tables.py:10-60`), `users` has
only `display_name` (`backend/contexts/identity/infrastructure/tables.py:24`). The only
DB-backed platform-wide runtime setting is `rate_limit_policies`
(`backend/alembic/versions/0004_audit.py:113-126`) with admin CRUD at
`backend/app/api/v1/admin_rate_limits.py:46-133` (Postgres authoritative, mirrored to
Redis).

**LLM invocation.** All provider traffic goes through `ProviderRouter`
(`backend/contexts/keys/application/provider_router.py:260`): `call()` (unary, rotating,
`provider_router.py:275`), `call_stream()` (`provider_router.py:337`), and
`call_single_key()` (pinned key, no rotation — used by embed/rerank,
`provider_router.py:534`). Adapters live in `backend/contexts/keys/infrastructure/adapters/`
(anthropic, openai, gemini for `LLM_CHAT`; capability matrix
`backend/contexts/keys/domain/providers.py:47-53`). Provider calls run only in the arq
worker, never the web process (`backend/contexts/agents/application/runtime/turn_engine.py:10-13`).
Browser streaming is WS-only: worker publishes `agent.token` deltas on a Redis pub/sub
channel (`turn_engine.py:1569-1575`) fanned out by `/ws/chatroom/{id}`
(`backend/app/api/ws/chatroom.py:46`). Every call is usage-accounted into
`key_usage_events` + Redis hourly buckets (`provider_router.py:169-231`).

**Platform-initiated LLM precedents.** No platform-owned key exists anywhere. The
summariser borrows the agent's key group
(`backend/contexts/agents/application/runtime/summariser.py:29-59`); GraphRAG requires a
dedicated `builder_key_group_id` (`backend/contexts/knowledge/application/graphrag_config_service.py:76-78`);
RAG embedding pins single keys via `call_single_key`
(`backend/contexts/knowledge/infrastructure/embedders.py:68`). The K-gate contract
forbids bypassing the router (`backend/contexts/knowledge/infrastructure/triple_extractor.py:12`).

**Keys.** Envelope-encrypted via Vault Transit, per-row DEK, AAD bound to row id
(`backend/shared_kernel/security/envelope.py:43-72`). Keys are user-owned
(`api_keys.owner_user_id`, `backend/contexts/keys/infrastructure/tables.py:22`), carried
into projects (`tables.py:64-85`), grouped per-project (`tables.py:88-129`). Plaintext
never returned after creation (`[R7.03]`, `REQUIREMENTS.md:265`); decryption only for
outbound calls (`[R7.15]`, `REQUIREMENTS.md:312`).

**Roles and AuthZ.** Fixed role set `Admin/OrgOwner/OrgMember/ProjectOwner/ProjectMember/Guest`
(`backend/shared_kernel/auth/permissions.py:34-40`); capability matrix `_MATRIX`
(`permissions.py:143-247`); `decide()` with admin bypass (`permissions.py:286-287`).
FastAPI deps `require(capability, scope_from_path(...))`
(`backend/shared_kernel/auth/dependencies.py:73-95`) and `require_membership()`
(`dependencies.py:130-157`). "Personal user" = user whose project has `owner_user_id`
set, `owner_org_id` NULL (`backend/contexts/tenancy/infrastructure/tables.py:44-49`);
Org Owner ⇒ Project Owner inheritance is computed
(`backend/contexts/tenancy/interfaces/role_resolver.py:31-68`). Admin is platform-level
(`admins` table, `backend/contexts/identity/infrastructure/tables.py:80-94`), gated by
`require_admin` (`backend/app/api/v1/admin_deps.py:15-20`).

**File upload.** MinIO via `shared_kernel/storage/minio_client.py:40-64` (buckets
`chat_uploads`, `rag_sources`, `exports`, plus `bucket_agent_workspace` =
"agent-workspace", no TTL, in `MinioSection` at `app/config/settings.py:87-90` — the
direct precedent for this feature's no-TTL bucket). Closest model for reference files:
`agent_workspace_files` — 32 MB single-shot upload with mime capture
(`backend/app/api/v1/agent_workspace.py:81-96`), rows with `sha256/mime/minio_key`
(`backend/contexts/agents/infrastructure/tables.py:97-114`). ClamAV INSTREAM scan +
quarantine (`backend/shared_kernel/scanning.py:1-38`); filename sanitization
`safe_input_name()` (`backend/shared_kernel/storage/sanitize.py:6-11`). Text extraction
for pdf/docx/md/txt exists (`backend/shared_kernel/text_extraction/parsers.py`).

**Frontend patterns.** Slices hand-write `api/index.ts` over the `http` axios wrapper —
the generated api-client is drift-check-only (`frontend/eslint.config.js:68,117`,
`frontend/vite.config.ts:80`). Streaming UI vocabulary `agent.thinking/token/finished`
handled in `useChatroomSocket.ts:204-245`; `wsManager.channel(path)` singleton with
ticket auth (`frontend/src/shared/transport/ws-manager.ts:1-16`). Conversation-slice
components (bubbles, composer) are NOT exported cross-slice
(`frontend/src/slices/conversation/index.ts`). Upload UI: `shared/ui/SFileUpload.vue`.
Settings surfaces: personal at `/account/*` (identity slice,
`frontend/src/slices/identity/routes.ts:34-64`), org at `OrgDetailView.vue` settings
card (`frontend/src/slices/tenancy/views/OrgDetailView.vue:249-281`), admin under
`/admin/*` with `requiredRoles: ['admin']` (`frontend/src/slices/admin/routes.ts:4-10`).
Precedent for a shared config editor used by multiple views: the wakeup config editor
unified into `shared/ui` (commit `ff5669f`).

## 5. Design

### Options considered

**Option A — extend the agents context.** Put configs/templates/sessions inside
`contexts/agents`. Rejected: the agents context is project-scoped end to end
(`agents.project_id`), while this feature's resources live at platform/org/user scope;
forcing them in blurs the context boundary and drags tenancy concerns into agents.

**Option B — new bounded context `prompt_studio` (chosen).** A small context owning
three aggregates: `AssistantConfig` (per scope), `PromptTemplate` (per scope), and
`AssistantSession` (ephemeral, Redis). It calls the keys context router for LLM calls
and the tenancy facade for scope checks — the same dependency direction the knowledge
context already uses.

**Option C — dedicated key-group slot (GraphRAG style) instead of pinned key.** Key
groups are per-project (`key_groups.project_id`), so an org/user/platform-scoped config
cannot own one without inventing scope-less key groups. Rejected in favor of a pinned
`api_key` reference (embed-key precedent, `embedders.py:68`), which is naturally
user-owned at every scope (the admin is also a user).

**Streaming design.** New WS route `/ws/prompt-assistant/{session_id}` following
`app/api/ws/chatroom.py`: browser POSTs a user message → API enqueues an arq job →
worker resolves config, builds context (config system prompt + inlined reference text +
session history + current editor draft), calls the router, publishes
`assistant.token`/`assistant.finished`/`assistant.error` events on a Redis channel →
WS fans out. `call_single_key()` today serves unary embed/rerank
(`provider_router.py:534`); a pinned-key streaming variant (`call_single_key_stream`)
is added to the router — same accounting path as `call_stream`
(`provider_router.py:169-231`), no rotation semantics needed (one key).

**Assistant output rendering.** Assistant replies render as plain text; proposed prompt
drafts are fenced by the assistant (instructed in a fixed platform wrapper prompt) and
shown in a code-block style with an "apply to editor" button. No `v-html`, so the
single-sanctioned-`v-html`-site eslint gate stays untouched and the XSS surface is zero.

### Decision

New bounded context `prompt_studio`; pinned-key model at all three scopes with a
router-level pinned streaming call; ephemeral Redis sessions; WS streaming via a new
channel; plain-text rendering. Consciously given up: RAG retrieval (Q-7), conversation
persistence, template variables, and key-group rotation for assistant calls (a revoked
pinned key disables the config with a clear UI error rather than rotating — resolution
falls through to the next scope in the chain).

### Resolution chain (normative)

For a user working in project P:
1. The user's own enabled personal config, if any.
2. Else, if P is org-owned: that org's enabled config, if any.
3. Else the enabled platform config, if any.
4. Else: assistant UI hidden/disabled with an explanatory hint.

Templates shown in P: platform templates (unless P's owning org hides them) + org
templates (org-owned P) + the current user's personal templates, grouped by source.

## 6. Detailed Changes

### Backend — new bounded context `contexts/prompt_studio/`

- `domain/models.py`: `AssistantConfig` (scope enum `platform|org|user`, `org_id?`,
  `user_id?`, `system_prompt` ≤ 20 000 chars, `key_id`, `model_hint`, `model_id?`,
  `enabled`, `daily_request_limit_per_user` int default 50, `version`),
  `AssistantFile` (config_id, filename, size, sha256, mime, minio_key, scan_status,
  extracted_chars), `PromptTemplate` (scope, org_id?, user_id?, name ≤ 100,
  description ≤ 300, body ≤ 100 000, position), `OrgTemplateVisibility`
  (org_id, hide_platform_templates bool — column on the org config row, not a new table).
- `infrastructure/tables.py` + one Alembic migration (`004x_prompt_studio.py`):
  `prompt_assistant_configs` (partial unique indexes: one platform row where scope
  ='platform'; unique org_id where scope='org'; unique user_id where scope='user'),
  `prompt_assistant_files`, `prompt_templates`. PG ENUM for scope created via
  `op.execute` and mirrored exactly in tables.py (memory rule: ORM enum type match).
  Reversible `downgrade()` per `0011_agents.py` exemplar.
- `application/config_service.py`: CRUD + scope AuthZ + key-ownership check (the pinned
  key must be owned by the configurer; platform scope requires the caller be admin).
  Reference-file budget: per-file 5 MB upload, extracted text hard total 200 KB per
  config; formats pdf/docx/md/txt, reusing `shared_kernel/text_extraction/parsers.py`,
  ClamAV scan before extraction.
- `application/template_service.py`: CRUD per scope + merged resolution for a project.
- `application/session_service.py` + arq task `run_assistant_turn`: Redis-backed
  session (TTL 2 h, max 40 messages, max 32 000 chars user message), fixed platform
  wrapper prompt (code-side constant) + config system prompt + inlined file text +
  history + editor draft; per-user daily request quota via Redis counter (pattern:
  `provider_router.py:613-633`); publishes `assistant.*` events.
- Keys context: add `ProviderRouter.call_single_key_stream()` (pinned key, streaming,
  full usage accounting; feasible — the `StreamingAdapter` protocol at
  `provider_router.py:127-132` is implemented by all three chat adapters, and
  `_stream_member` (`provider_router.py:394-494`) shows the per-key streaming +
  accounting shape). Additionally, a nullable `usage_context` column on
  `key_usage_events` (verified: no discriminator exists today — GraphRAG builder,
  embed, and rerank calls all land with NULL agent/chatroom and no tag,
  `triple_extractor.py:86-95`, `embedders.py:70-73`); this touches `ProviderRequest`,
  `UsageAccountant.record_call` (`provider_router.py:179-231`), and
  `record_usage_event` (`contexts/keys/infrastructure/usage_events.py:24`), all
  additive with a NULL default so existing call sites are untouched. Without it,
  assistant rows would be indistinguishable from GraphRAG/embed rows and AC-8 is
  unverifiable.
- `interfaces/facade.py`: read surface for other contexts (none needed yet, but the
  context follows the standard layout).
- Usage attribution: `key_usage_events.agent_id/chatroom_id` stay NULL for assistant
  calls; the new `usage_context` column (see keys-context bullet above) is set to
  `prompt_assistant`, added in the same migration.
- Audit events: config created/updated/deleted, file uploaded/removed, template
  CRUD, per the audit emit pattern in `admin_rate_limits.py:119-126` neighborhood.

### API contract (new routes; `gen:api` rerun: yes)

- `GET|PUT /api/me/prompt-assistant/config`, `POST|DELETE .../files`,
  `GET|POST|PATCH|DELETE /api/me/prompt-templates[/{id}]` — self-service, verified user.
- `GET|PUT /api/orgs/{org_id}/prompt-assistant/config`, files, and
  `/api/orgs/{org_id}/prompt-templates...` — Org Owner only (new capability rows in
  `_MATRIX`, ORG_OWNER + ADMIN).
- `GET|PUT /api/admin/prompt-assistant/config`, files, and
  `/api/admin/prompt-templates...` — `require_admin`.
- `GET /api/projects/{project_id}/prompt-assistant` — resolved effective config
  (metadata only: enabled, source scope, model; never the key), `require_membership`.
- `GET /api/projects/{project_id}/prompt-templates` — merged template list.
- `POST /api/projects/{project_id}/prompt-assistant/sessions` → session_id only. WS
  tickets are NOT returned here: they are minted exclusively by the generic
  `POST /api/auth/ws-ticket` (single-use, 30 s TTL,
  `backend/shared_kernel/realtime/ws_auth.py:77-89`) and fetched by `wsManager` itself
  during the handshake (`frontend/src/shared/transport/ws-manager.ts:126-132`) — a
  ticket returned at session-create would expire before use.
- `POST /api/prompt-assistant/sessions/{session_id}/messages` → 202, streams over WS.
- New WS route `/ws/prompt-assistant/{session_id}` in `app/api/ws/`, reusing the
  subprotocol ticket auth (`authenticate_subprotocol`) with its own session-ownership
  check, mirroring `ws_chatroom`.
- Rate limiting: extend the upload bucket classifier `_bucket_for`
  (`backend/app/api/middleware/rate_limit.py:54-68`) so the new file-upload endpoints
  fall into `Bucket.UPLOAD` (today it only matches `/api/tus`, `/attachments`,
  `/documents` — the new paths would silently land in the generic bucket).
- All request models Pydantic with explicit bounds; RFC 7807 errors.

### Frontend

- **agents slice**: new `components/` dir — `PromptAssistantPanel.vue` (collapsible
  side panel in the prompt tab: message list, plain-text streaming bubble, composer,
  apply-draft button using the existing `SConfirmDialog`/`useConfirmDialog`),
  `PromptTemplatePicker.vue` (grouped dropdown/modal, insert into `systemPrompt`
  field). Note this is real layout work: the prompt tab today is a plain vertical
  stack with one full-width `SCard` (`AgentDetailView.vue:844-849`) — it must be
  restructured into a responsive two-column layout (editor + panel) that collapses to
  stacked on narrow viewports. New `composables/usePromptAssistantSocket.ts` following
  `useChatroomSocket.ts` patterns over `wsManager`. API calls added to a new
  `api/promptStudio.ts` module in the slice (or a small dedicated slice —
  implementer's choice; keep imports within boundaries).
- **shared/ui**: `SPromptAssistantConfigForm.vue` — presentational config editor
  (system prompt textarea, key/model selectors reusing the agent form's selector
  pattern per Q-8, file list + `SFileUpload`, caps, enabled toggle), used by all three
  settings pages via props/emits (wakeup-editor precedent, `SWakeupEditor.vue` from
  commit `ff5669f`). Boundary constraint: `shared/` may not import from slices, and
  `useModelCatalog` lives in `slices/agents/composables/` — the form receives model
  catalog and key options via props; the owning views fetch the data.
  Similarly `SPromptTemplateManager.vue` (list + editor modal).
- **identity slice**: new `/account/prompt-assistant` route + view (personal config +
  personal templates).
- **tenancy slice**: new section or sibling route on `OrgDetailView.vue` for org config
  + org templates + hide-platform-templates toggle (gated by `isOwner`).
- **admin slice**: new `/admin/prompt-assistant` child route + view.
- i18n: new namespaced keys in each touched slice's `locales/{en,zh-TW}.json`; remember
  the literal-`@` escape rule (`{'@'}`).

### Deploy/config

- New MinIO bucket `prompt-assistant-files` (no TTL) in `MinioSection`
  (`app/config/settings.py:87-90`, alongside `bucket_agent_workspace`), a matching
  bucket property on the MinIO client class, and provisioning.
- No new env secrets; the platform key goes through the normal key-upload flow.

## 7. NFR Checklist

- [ ] i18n — all new strings through `$t()` in slice locale bundles (en + zh-TW).
- [ ] Audit log — config/file/template mutations emit audit events; assistant LLM calls
      land in `key_usage_events`.
- [ ] Tenant isolation — every new endpoint gated: `require_admin`, org-owner capability
      via `_MATRIX`, `require_membership` for project-resolved reads; session ownership
      checked on every message POST and WS attach.
- [ ] Error handling UX — loading/error/empty states in panel and settings pages;
      RFC 7807 slugs mapped via `useServerErrors`; disabled-assistant hint when the
      chain resolves to nothing; revoked-key error state.
- [ ] Performance — reference text inlined once per turn (bounded 200 KB); template
      lists are small (cap 100 per scope, paginated API anyway); session state in Redis;
      markdown rendering avoided entirely (plain text).

## 8. Security Considerations

- **Key protection** — the assistant path decrypts keys only inside the arq worker via
  the router (`[R7.15]`); no new decrypt sites; plaintext never in API responses; the
  resolved-config endpoint returns key *metadata only*, mirroring the existing shape
  `provider + name + masked_preview` (`backend/app/api/v1/keys.py:47-49`).
- **Prompt injection via reference files** — file text is attacker-influenceable (an
  org member could be given a poisoned style guide by the Org Owner — accepted, the
  configurer is trusted for their scope). The fixed platform wrapper prompt instructs
  the model that file content is reference material, not instructions. The assistant
  has no tools, so injection cannot exfiltrate keys or call anything; blast radius is a
  bad prompt draft the user reviews before applying.
- **Cross-tenant leakage** — resolution chain must never serve org A's config/files/
  templates to org B or to non-members: config resolution takes the *project* as input
  and derives org membership server-side (`[R5.05]` single permissions service).
- **Platform key abuse** — per-user daily request cap (Redis counter) + per-reply
  `max_tokens` bound + session message cap; `KeyGroupExhausted`-style 429 on breach.
- **Upload surface** — ClamAV scan before extraction; `safe_input_name()`; 5 MB/file,
  MIME/extension allowlist (pdf/docx/md/txt); extraction runs in the worker, not the
  web process.
- **WS AuthZ** — ticket handshake as per existing `wsManager` flow; session bound to
  the creating user; no cross-user session attach.
- **No `v-html`** in any new component — plain-text rendering by design.

## 9. Quality Notes

- **Existing debt** (do not imitate, do not silently fix): `AgentDetailView.vue` is a
  1077-line monolith — the assistant panel must be a separate component, not more
  inline template; the agents API router calls `AgentService` directly rather than its
  facade (`app/api/v1/agents.py:23`) — acceptable same-context pattern, but keep the
  new context's router thin over its application services; agents slice has no
  `components/` dir yet — create it rather than growing views.
- **Patterns to follow**: migration exemplar `alembic/versions/0011_agents.py`
  (ENUM via `op.execute`, mirrored tables.py types, full downgrade); AuthZ via
  `require(...)`/`require_membership` + `_MATRIX` rows; admin CRUD + audit pattern
  `admin_rate_limits.py:46-133` (audit emit at 107-117, Redis mirror at 119-126);
  worker-only provider calls + Redis pub/sub events
  `turn_engine.py:1569-1575`; WS route `app/api/ws/chatroom.py`; socket composable
  `useChatroomSocket.ts`; slice layout and locale registration
  `slices/agents/index.ts:1-17`; shared config editor precedent (wakeup editor,
  commit `ff5669f`); optimistic concurrency with `If-Match` + 409 banner
  (`AgentDetailView.vue:458-477`) for config PUTs.
- **Reuse inventory**: `ProviderRouter` + adapters (all LLM calls); envelope crypto
  (untouched, free); `UsageAccountant`; Redis quota-bucket pattern
  (`provider_router.py:613-633`); `shared_kernel/text_extraction/parsers.py`;
  `shared_kernel/scanning.py` (ClamAV); `safe_input_name()`; `minio_client.py`;
  `SFileUpload.vue`, `SCodeEditor`, `SCharCount`, `SFormField`, `SAlert`, `STabs`,
  `SSelect`, `SConfirmDialog` + `useConfirmDialog` (apply-draft confirm);
  `wsManager`/`Channel`; `useServerErrors`; `useModelCatalog` (model dropdowns —
  slice-local; data reaches shared components via props, see §6);
  vee-validate + Zod schema pattern (`slices/agents/types/schemas.ts`);
  TanStack query-key factory pattern (`slices/agents/queries/index.ts`);
  `INPUT_LIMITS` constants.

## 10. Risks and Rollback

- **Migration** adds three tables + one ENUM + one nullable `usage_context` column on
  `key_usage_events`; fully reversible downgrade; no data backfill.
- **Pinned-key revocation** breaks a config silently — mitigated: config resolution
  checks key liveness and falls through the chain; UI shows a "configured key revoked"
  state to the configurer.
- **Router change** (`call_single_key_stream` + `usage_context` threading through
  `ProviderRequest`/`UsageAccountant`/`record_usage_event`) touches the hottest code
  path's module — additive with NULL-default semantics, no modification to existing
  call/rotation logic; covered by unit tests mirroring existing `call_single_key`
  tests plus an assertion that existing call sites record `usage_context = NULL`
  unchanged.
- **Scope creep risk** in three settings surfaces × two resource types — fenced by
  Non-goals; the shared presentational form keeps the three pages thin.
- Rollback path: feature is fully additive (new routes, new tables, new components);
  reverting the deploy + `alembic downgrade` removes it without touching agent data.

## 11. Acceptance Criteria

- [x] AC-1: In the agent create/edit prompt tab, a user in a project with a resolved,
  enabled assistant config sees the assistant panel; with no resolvable config the
  panel area shows a disabled hint instead. (PromptAssistantPanel render-state test.)
- [~] AC-2: Sending a message in the panel streams the assistant's reply token-by-token
  over WebSocket; the session supports multiple turns and the assistant sees the
  current editor draft. (Unit-verified: worker publish path (pytest) +
  `usePromptAssistantSocket` token/finished/multi-turn reduction test; editor draft
  threaded via `postMessage(editor_draft)`. Live E2E streaming pending — FU-5.)
- [~] AC-3: A prompt draft proposed by the assistant can be applied to the system-prompt
  editor with one click, replacing the editor content after a confirm when the editor
  is non-empty. (Implemented: panel `apply()` + `useConfirmDialog`; AgentDetailView
  `onAssistantDraft`. Live confirm flow pending behavioral verify — FU-5.)
- [x] AC-4: An Org Owner can create/edit the org assistant config (system prompt,
  pinned key from their own keys, model, caps, enabled) and upload/remove reference
  files; org members cannot access these endpoints (403). (Backend pytest scope matrix.)
- [x] AC-5: A verified user can create/edit their personal assistant config and
  templates under `/account/prompt-assistant`; an admin can do the same at platform
  scope under `/admin/prompt-assistant`; non-admins get 403 on admin routes. (Backend
  pytest + per-scope view tests.)
- [x] AC-6: Resolution chain holds: personal config wins over org config wins over
  platform config; in an org-owned project with no personal and no org config, the
  platform config is used; disabled configs are skipped. (Backend resolution-chain tests.)
- [~] AC-7: Reference-file text is included in the assistant's context (observable:
  assistant answers a question whose answer exists only in an uploaded file). Uploads
  exceeding 5 MB, a disallowed format, or the 200 KB extracted-text budget are rejected
  with RFC 7807 errors; infected files are quarantined and excluded. (Validation/budget/
  scan-status rejection unit-verified in pytest; the answer-from-file runtime probe is
  pending — FU-5.)
- [x] AC-8: Assistant LLM calls are recorded in `key_usage_events` against the pinned
  key; when a user exhausts the per-user daily request cap the message POST returns 429
  and the panel shows a quota message. (Backend accounting + quota/429 tests; panel
  quota-message branch present.)
- [x] AC-9: The template picker in the agent form shows platform + (org | personal)
  templates grouped by source; applying one inserts its body into the editor.
  (PromptTemplatePicker grouping + insert-emit test; AgentDetailView `onTemplateInsert`.)
- [x] AC-10: Org Owner CRUD for org templates, user CRUD for personal templates, admin
  CRUD for platform templates all work with scope AuthZ enforced server-side (403 on
  wrong role), and the org "hide platform templates" toggle removes platform templates
  from that org's projects' pickers. (Backend template-CRUD scope tests.)
- [x] AC-11: No API response anywhere contains key plaintext; the resolved-config
  endpoint exposes key metadata only. (KeyMeta-only DTOs; backend response-shape tests.)
- [x] AC-12: All new UI strings resolve through i18n in both en and zh-TW; no console
  i18n-missing warnings on the new pages/panel. (en + zh-TW bundles complete with
  identical key structure; `pnpm build` clean.)

## 12. Test Plan

- **Backend unit/integration (pytest, `backend/tests/`)**: config CRUD + scope AuthZ
  matrix (AC-4, AC-5, AC-10, AC-11); resolution chain permutations incl. disabled and
  revoked-key fallthrough (AC-6); file validation, budget, scan-status handling (AC-7);
  quota counter + 429 (AC-8); `call_single_key_stream` router tests (accounting,
  pinned-key error surface); session service (TTL, message caps, ownership).
- **Frontend component tests (vitest, per-slice `__tests__/`)**: panel render states
  (enabled/disabled/quota/error), apply-draft flow incl. confirm (AC-1, AC-3), socket
  composable event handling with mocked channel (AC-2), template picker grouping and
  insert (AC-9), config form validation and 409 handling.
- **Manual via `verify`**: end-to-end streaming turn in a real dev stack (AC-2), file
  upload → answer-from-file probe (AC-7), zh-TW locale sweep (AC-12).

## 13. SRS Delta

Amendments:

- §1.2 (`REQUIREMENTS.md:49`): replace the exclusion line
  "Agent versioning, export/import, or template library." with
  "Agent versioning and export/import. (Prompt templates are in scope as of §29.)"
- `[R9.02]`: replace with "Agents are not versioned; no export/import (Q41). Editing
  overwrites in place. Prompt templates (§29) may be inserted at authoring time; an
  applied template leaves no persistent link to its source."
- §5.2 permission matrix: add rows "Configure org prompt assistant / org templates"
  (Admin ✓, OrgOwner ✓, all others ✗) and "Configure personal prompt assistant /
  personal templates" (any verified user, own scope only).

New chapter §29 — Prompt Assistant & Prompt Templates (added by the 2026-07-05 design
session):

- [R29.01] The agent create/edit view offers an AI prompt assistant as a side chat
  panel scoped to drafting the agent's `system_prompt`. The assistant has no tool
  access.
- [R29.02] Assistant configurations exist at three scopes: platform (Admin), org
  (Org Owner), personal (any verified Individual). At most one configuration per scope
  holder.
- [R29.03] A configuration comprises: assistant system prompt (≤ 20 000 chars),
  reference files, one pinned provider key owned by the configurer, model selection,
  per-user daily request cap, and an enabled flag.
- [R29.04] Effective-config resolution for a project: requesting user's enabled
  personal config → owning org's enabled config (org-owned projects only) → enabled
  platform config → assistant unavailable. Disabled configs are skipped.
- [R29.05] Assistant calls MUST go through the provider router with the configuration's
  pinned key; key plaintext never crosses out of the worker call path (extends R7.15).
  Usage is recorded per R7.12 with null agent/chatroom attribution.
- [R29.06] Reference files: formats pdf/docx/md/txt; ≤ 5 MB per file; extracted text
  inlined into assistant context, hard total budget 200 KB per configuration; virus
  scan per R22.15.07 semantics before extraction.
- [R29.07] Assistant sessions are ephemeral (Redis, 2 h TTL, ≤ 40 messages); no
  server-side conversation history beyond the live session.
- [R29.08] Streaming to the browser uses the existing WS + Redis pub/sub transport;
  provider calls execute only in the worker.
- [R29.09] Per-user daily request caps are enforced per configuration; breach returns
  429. Platform-scope configurations MUST have a cap (no unlimited).
- [R29.10] Prompt templates (name ≤ 100, description ≤ 300, plain-text body ≤ 100 000)
  exist at the same three scopes with the same ownership rules; no variables, no
  versioning.
- [R29.11] The agent authoring view shows the merged template list grouped by source:
  platform + org (org-owned projects) or personal (user-owned projects) + the user's
  personal templates. Applying a template copies its body into the editor; no
  persistent link.
- [R29.12] An Org Owner may hide platform templates from their org's projects.
- [R29.13] All configuration, file, and template mutations are audit-logged (§17).
- [R29.14] All assistant/template API responses expose key metadata only; plaintext
  never (extends R7.03).

Post-approval bookkeeping (applied with the delta): §21.5 MinIO bucket list gains
`prompt-assistant-files` (no TTL); §22 gains the new API subsection; traceability CSV
re-extraction per `REQUIREMENTS.md:1921`.

## 14. Open Questions

- Panel placement on mobile viewports (the prompt tab already collapses tabs to a
  select) — UX detail for implementation.

Resolved during verification (2026-07-05): usage tagging requires the new nullable
`usage_context` column (no existing discriminator — see §6 keys-context bullet); the
resolved-config endpoint mirrors the existing keys-API metadata shape
`provider + name + masked_preview` (`backend/app/api/v1/keys.py:47-49`).

## 15. Deviation Log

- D-1: Reference-file text extraction runs **synchronously in the upload request**
  (a threadpool `asyncio.to_thread` parse), not in the arq worker as §8 states. Reason:
  AC-7 requires a synchronous RFC-7807 rejection when the 200 KB extracted-text budget
  is exceeded, which is impossible if extraction is deferred to a worker. Files are
  5 MB-bounded and the parsers are pure, so the bounded parse off the event loop does
  not block the web process. Agreed with the user during /build.
- D-2: `AssistantConfig.model_hint` (§6) is implemented as a single nullable `model_id`
  text column only (no separate `model_hint` enum). Reason: the agents table dropped
  `model_hint` for `model_id`/`effort` (migrations 0030/0039) after the spec was
  written; the pinned key already fixes the provider, so `model_id` alone selects the
  model. Reuses the current agent model-selection shape (freshness note, item 32).
- D-3: `prompt_assistant_files` gained an `extracted_text` TEXT column not named in §6.
  Reason: the assistant turn inlines file text every turn (R29.06); storing the
  already-extracted, budget-bounded (≤200 KB) text in-row avoids re-parsing / a MinIO
  round-trip per turn. Added in migration 0042.
- D-4: `backend/openapi.json` + the generated `src/shared/api-client` were NOT
  regenerated in this environment. Reason: the local `create_app()` produced a spec
  that *diverged* from the committed snapshot by ~3.4k added / ~1.2k removed lines
  across unrelated paths (this dev environment lacks config/env that changes conditional
  routers + fields), so a local regen would corrupt the snapshot. The generated client
  is drift-check-only and not imported at runtime; the frontend hand-writes its API
  layer. `pnpm run gen:api` (and the `check:openapi-drift` gate) must be run in the
  canonical CI/dev environment — see FU-3.
- D-5: The prompt tab in `AgentDetailView.vue` was restructured into a responsive
  two-column layout (system-prompt editor + `PromptTemplatePicker` on the left, the
  `PromptAssistantPanel` on the right, stacked on mobile), and the personal/org/platform
  entry points were surfaced in existing navigation (`UserMenu` gated on
  `isVerified`, `OrgDetailView` gated on `isOwner`, and an `AdminNav` section). These
  are the concrete UI placements the spec left to implementation; noted for the record.

## 16. Build verification (running stack)

Ran against a local Docker stack (postgres/redis/vault/minio up; backend image built
from `backend/Dockerfile` via the base compose — the dev override's
`build.context: ../../backend` is a latent bug that breaks the repo-root `COPY backend/`
Dockerfile, see FU-8):

- **Migration 0042 validated end-to-end.** `alembic upgrade head` reaches
  `0042_prompt_studio`; the three tables (`prompt_assistant_configs`,
  `prompt_assistant_files`, `prompt_templates`), both enums (`prompt_studio_scope`,
  `prompt_file_scan_status`), and `key_usage_events.usage_context` (text) are created.
  `alembic downgrade -1` cleanly drops all of them — the migration is fully reversible.
  This closes the previously env-blocked migration contract gate for this feature. (To
  reach 0042 the pre-existing FU-7 blocker at 0032 was bypassed by pre-creating a
  `varchar(255)` `alembic_version` table in the throwaway dev DB — a DB-only workaround,
  no code change.)
- Still pending (FU-5): live streaming / apply-draft / answer-from-file E2E, which needs a
  fully bootstrapped stack (Vault Transit keys, MinIO bucket, ClamAV) plus a real provider
  API key to actually stream an LLM reply.

## 17. Follow-ups

- FU-1: RAG-based retrieval over reference files if configs outgrow the 200 KB inline
  budget (Q-7 explicitly deferred this).
- FU-2: `AgentDetailView.vue` monolith split — pre-existing debt, recorded, not fixed
  here.
- FU-3: Regenerate `backend/openapi.json` + `src/shared/api-client` in the canonical
  environment and reconcile the pre-existing drift (the committed snapshot is ~stale by
  many unrelated paths — observations, presence, releases, captcha, etc., independent of
  this feature). See D-4.
- FU-4: Two pre-existing `mypy` errors in `contexts/tenancy/infrastructure/repositories.py`
  (`ProjectMemberRepository.list` used as a type; unreachable statement) surfaced while
  typechecking prompt_studio's dependency graph — not introduced here, not fixed here.
- FU-5: Behavioral (running-stack) verification of AC-2 end-to-end streaming, AC-3
  apply-draft confirm flow, and the AC-7 "answer-from-file" probe is pending — the local
  environment has no running Docker/Postgres/Redis stack. Unit/integration coverage
  exists (backend pytest for the worker/router/session/quota/file paths;
  `usePromptAssistantSocket` event-reduction test; panel render-state + picker tests),
  so these ACs are code-complete and unit-verified; only the live E2E observation is
  deferred. Run the `verify` skill against a dev stack to close them.
- FU-6: Three pre-existing frontend CI conditions are already red on `main`, independent
  of this feature and not addressed here: (a) `pnpm lint --max-warnings=0` reports 295
  warnings on a clean tree (this change adds 0 net new — verified by stash-compare);
  (b) gate #8 `check:view-tests` fails because `agents/views/AgentToolsView.vue` has no
  test (this feature's three new views are all covered); (c) `Landing.test.ts`
  intermittently fails in the full `pnpm test` run due to cross-file session/router state
  pollution (passes in isolation and on re-run).
- FU-7: **Pre-existing migration-infra bug — fresh `alembic upgrade head` is broken on
  `main` at 0032.** Alembic's default `alembic_version.version_num` is `VARCHAR(32)`, but
  two revision slugs overflow it: `0032_audit_retention_delete_grant` (33 chars) and
  `0040_message_attachment_extracted_text` (39 chars). On an empty DB the chain dies with
  `StringDataRightTruncation` on `UPDATE alembic_version SET version_num='0032_...'`. This
  is unrelated to this feature (my slug `0042_prompt_studio` is 18 chars) and predates it;
  it surfaced only because the migration gate was finally run against a real Postgres. The
  documented one-line fix (`context.configure(..., version_table_column_type=String(255))`
  in `alembic/env.py`) was tried and did **not** take effect in this environment — Alembic
  still created the column at 32 — so a deeper fix is needed (widen the column in a
  dedicated migration, shorten the offending slugs, or investigate why the option is
  ignored). Left for a maintainer; no code committed here.
- FU-8: Pre-existing dev-compose bug. `deploy/compose/docker-compose.override.yml` sets
  `backend-web`/`backend-worker` `build.context: ../../backend`, but `backend/Dockerfile`
  is written for a repo-root context (`COPY backend/pyproject.toml`, `COPY docs/...`), as
  the base `docker-compose.yml` correctly uses (`context: ../..`). With the override
  merged, `docker compose build` fails ("/backend: not found"). Unrelated to this feature;
  worked around by building via the base compose file only. A maintainer should reconcile
  the override's context (and target) with the Dockerfile.
