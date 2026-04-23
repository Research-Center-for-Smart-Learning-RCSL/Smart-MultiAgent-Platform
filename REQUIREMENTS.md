# SMAP — Smart Multi-Agent Platform
## Software Requirements Specification (SRS)

**Document status:** Draft v1.0 — derived from `SMAP.md` design diagrams and the stakeholder Q&A session.
**Scope:** Full platform (no phased MVP). Self-hosted, containerized.
**Audience:** Architects, backend/frontend engineers, DevOps, QA.

---

## 0. How to read this document

- Chapters 1–5 describe the product and domain model.
- Chapters 6–15 describe each functional subsystem in detail.
- Chapters 16–20 describe cross-cutting concerns (security, observability, NFR).
- Chapters 21–25 describe the technical architecture, API surface, persistence schema, and deployment topology.
- Every requirement is tagged with an ID of the form `[Rxx.yy]` so that tests, tickets, and code reviews can reference it unambiguously.
- Where an answer was deferred to the design team during the Q&A, the decision is recorded here and marked with **"Recommendation applied"**.

### Companion documents

| File | Scope |
|---|---|
| `docs/workflow.schema.json` | Normative JSON Schema for `workflows.definition`. |
| `docs/workflow.schema.md` | Workflow execution model, SMAP Expression Language (SEL) v1, semantic linter rules, example. |
| `docs/operations.md` | Operational manual: logging, health checks, resource limits, Alembic policy, bootstrap CLI, RFC 7807 error catalog, runbooks. |
| `deploy/vault/policies/*.hcl` | Vault policies for backend (runtime) and rotation (operator). |
| `deploy/vault/README.md` | Vault bootstrap, key rotation, disaster scenarios. |

---

## 1. Product Overview

SMAP (Smart Multi-Agent Platform) is a self-hosted web application that lets users compose, orchestrate, and converse with groups of LLM-powered agents. Users bring their own API keys from third-party model providers (Anthropic Claude, OpenAI ChatGPT, Google Gemini). SMAP does not charge usage fees; all model costs are billed directly by the providers to the key owner.

### 1.1 Primary user value

1. Centralize personal and organizational AI keys with rotation, quota, and cost-control rules.
2. Compose agents with system prompts, RAG, Graph RAG, MCP tools, and cross-agent messaging.
3. Orchestrate multi-agent collaboration in chat rooms and workflows with visual editing.
4. Invite guests into workspaces via links without giving up account control.

### 1.2 Out-of-scope (explicitly)

- Paid subscriptions / platform billing.
- Content moderation of user-generated or guest-generated text/images.
- Public programmatic API for third parties.
- Automated backups and DR tooling (operator responsibility).
- Native mobile apps (responsive web is sufficient).
- Agent versioning, export/import, or template library.
- Cross-organization project migration or cloning.
- SSO, OAuth, or MFA in v1 (email + password only).
- Voice/audio input.

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Admin** | Platform operator. Has privileges equal to the owner of every Org/Individual resource, plus platform-level actions (ban, unban, delete users/IPs, read any data). |
| **Individual** | An end-user account (email + password). |
| **Organization (Org)** | A tenant container for shared projects and keys. Created by an Individual ("original creator"). |
| **Original Creator** | The Individual who created an Org. Immutable role. Cannot be demoted, cannot leave, only this user can delete the Org. |
| **Org Owner** | Role within an Org. Multiple Org Owners allowed. The Original Creator is always an Org Owner. |
| **Org Member** | Non-owner member of an Org. |
| **Project** | Collaboration unit. Owned by an Individual *or* by an Org. |
| **Project Owner / Project Member** | Project-scoped roles. |
| **Guest** | A registered Individual who has been invited into a specific Chat Room via a link. Has no permissions outside that room. |
| **API Key** | A credential issued by a model provider and uploaded by a user. |
| **Individual Key** | Key owned by an Individual, usable in any project they participate in. |
| **Project Key** | Key attached to a specific project. Includes Individual Keys "carried" into a project (see §7). |
| **Key Group** | Ordered list of 1..N keys bound to an Agent; used for rotation and failover. |
| **Agent** | A configured LLM persona: system prompt, key group, RAG, MCP tools, A2A, Graph RAG. |
| **Workspace** | A project-scoped environment containing Chat Rooms and Workflow definitions. |
| **Chat Room** | A conversational channel inside a Workspace. |
| **Workflow** | A visual, enterprise-grade execution graph for multi-agent collaboration. |
| **A2A** | Agent-to-Agent protocol: an internal message-passing mechanism defined by SMAP (custom spec). |
| **MCP** | Model Context Protocol. Both built-in tool servers and user-provided servers are supported. |
| **Wake-up** | The event that causes a dormant agent to produce output. |
| **/compact** | Operation that summarizes context when it hits its cap, modeled after Claude Code's `/compact`. |

---

## 3. High-level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Browser (Vue 3 SPA, PWA)                      │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ HTTPS  /  WebSocket
┌──────────────────────────────▼───────────────────────────────────────┐
│                    Nginx (TLS, gzip, static, WS)                     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│                   FastAPI API Gateway (stateless)                    │
│      - AuthN/AuthZ middleware    - Rate limiter    - i18n            │
└──┬────────────┬─────────────────┬───────────────┬────────────────────┘
   │            │                 │               │
   ▼            ▼                 ▼               ▼
┌──────┐  ┌───────────┐  ┌─────────────────┐  ┌─────────────────┐
│ WS   │  │ Workflow  │  │ Agent Runner(s) │  │ RAG / GraphRAG  │
│ Hub  │  │ Engine    │  │ (worker pool)   │  │ Builder workers │
└──┬───┘  └─────┬─────┘  └────────┬────────┘  └────────┬────────┘
   │            │                 │                    │
   │            └──── Event Bus (Redis Streams) ───────┘
   │                                      │
   ▼                                      ▼
┌───────────┐     ┌──────────┐     ┌─────────┐     ┌───────────┐
│PostgreSQL │     │  Redis   │     │ Qdrant  │     │  Neo4j    │
│(RDBMS +   │     │ (cache + │     │(vector) │     │ (graph)   │
│ FTS GIN)  │     │  streams)│     └─────────┘     └───────────┘
└───────────┘     └──────────┘

                  ┌──────────────────────────────┐
                  │  MCP Sandbox (Docker-in-     │
                  │  Docker, gVisor, egress GW)  │
                  └──────────────────────────────┘

                  ┌──────────────────────────────┐
                  │  Object Store (MinIO or FS)  │
                  │  for uploads, TTL 3 days     │
                  └──────────────────────────────┘

                  ┌──────────────────────────────┐
                  │  Secrets: HashiCorp Vault    │
                  │  (Transit engine for DEK)    │
                  └──────────────────────────────┘
```

**[R3.01]** The entire stack MUST be deployable via `docker compose up` from a single repository.
**[R3.02]** The system MUST sustain 100 concurrent heavy users (sending 1 message per 10 s on average, streaming responses, mixed RAG/Graph) on a 16-core / 32 GB host without visible UI lag (p95 server processing ≤ 500 ms excluding LLM roundtrip).
**[R3.03]** All services MUST expose `/healthz` and `/readyz` HTTP probes for orchestration.
**[R3.04]** The system follows **Domain-Driven Design** with bounded contexts: Identity, Tenancy, Keys, Agents, Knowledge (RAG/GraphRAG), Conversation, Workflow, Audit, Notification. Each context owns its own modules/tables; cross-context communication goes through application services and domain events.

---

## 4. Technology Stack (authoritative)

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | Vue 3 + Vite + TypeScript + Pinia + Vue Router | Stakeholder choice. |
| UI toolkit | Element Plus or Naive UI | Mature, responsive, mobile-friendly. |
| Realtime transport | **WebSocket (native, via FastAPI)** for chat; SSE for one-way event streams (usage, audit tail) | Recommendation applied (Q47). WS is bidirectional and fits chat; SSE is simpler for push-only. Polling rejected as it cannot meet latency at 100 users. |
| Backend | Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic | Stakeholder choice. |
| Worker runtime | **Arq** on Redis; Workflow engine uses custom FSM over the event bus. | Async long-running tasks, scheduled wake-ups, Graph RAG builders. Chosen over Celery because Arq is async-native (matches FastAPI), smaller surface, and reuses the same Redis instance as the event bus. |
| Event bus | Redis Streams (in-process consumers via Arq / python-redis) | Stakeholder choice. |
| RDBMS | PostgreSQL 16 | Stakeholder choice. Full-text search uses `tsvector` + GIN. |
| Cache / sessions / rate-limit | Redis 7 | Stakeholder choice. |
| Vector store | **Qdrant** | Recommendation applied (Q9). Docker-native, gRPC+REST, payload filtering, scales beyond 100 users. `pgvector` considered but loses performance on rerank workloads. |
| Graph store | Neo4j 5 Community | Stakeholder choice. |
| Object store | MinIO (S3-compatible) | Self-hosted, containerized. Supports lifecycle rules for 3-day expiry. |
| Secrets | HashiCorp Vault (Transit + KV) | Envelope encryption for API keys. See §7.6. |
| Sandbox | Docker + gVisor runtime, ephemeral containers, egress proxy | See §11.3. |
| Reverse proxy | Nginx | TLS, WS upgrade, static caching. |
| i18n | `vue-i18n` on frontend, `babel`/`gettext` on backend | English locale only in v1; structure for N locales. |
| Observability | OpenTelemetry SDK → OTLP collector → Grafana Tempo/Loki/Prometheus (optional; structural only) | Audit logs are a product feature (see §17), separate from ops observability. |

---

## 5. Roles, Scopes, and Permission Matrix

### 5.1 Role scoping

Roles are scoped to resources, not global. One user may simultaneously be `OrgOwner(A)`, `OrgMember(B)`, `ProjectOwner(P)`, `ProjectMember(Q)`, `Guest(ChatRoom R)`.

- Fixed role set (Q19): `Admin`, `OrgOwner`, `OrgMember`, `ProjectOwner`, `ProjectMember`, `Guest`. No custom roles.
- **[R5.01]** `Admin` is platform-level and NOT assignable through the UI; set via a bootstrap CLI or a `seed_admins` env list on first boot.
- **[R5.02]** The **Original Creator** flag on an Org is a separate, immutable bit, orthogonal to `OrgOwner`. The Original Creator is always an Org Owner and cannot demote, leave, or be removed.
- **[R5.03]** Individuals can create projects without an Org; such projects are owned by the Individual (`owner_type = 'user'`). Org Owners are automatically Project Owners on every project in their Org (`OrgOwner → ProjectOwner` inheritance is computed, not stored, to avoid drift).
- **[R5.04]** `Guest` is a registered Individual account that has been granted access to a specific Chat Room via an invite link. Guest status is per-Chat-Room; a Guest in Room A may be a full member elsewhere.

### 5.2 Permission matrix

Legend: ✓ allowed, ✗ denied, ∘ allowed only on resources the user owns, `—` not applicable.

| # | Capability | Admin | Org Owner | Org Member | Project Owner | Project Member | Guest |
|---|---|---|---|---|---|---|---|
| 1 | View API key **plaintext** (ever) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 2 | Upload API key (Individual scope) | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| 3 | Delete own uploaded key | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| 4 | Delete *others'* key within Project | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ |
| 5 | View Key usage (project scope) | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| 6 | Adjust Key settings (rotation/limits) | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ |
| 7 | Create Org | ✓ | — | — | — | — | ✗ |
| 8 | Delete Org | ✓ | only Original Creator | ✗ | — | — | ✗ |
| 9 | Add/remove Org Owner | ✓ | ✓ (cannot touch Original Creator) | ✗ | — | — | ✗ |
| 10 | Invite/remove Org Member | ✓ | ✓ | ✗ | — | — | ✗ |
| 11 | Create Project under Org | ✓ | ✓ | ✓ | — | — | ✗ |
| 12 | Create Project under Individual (self-owned) | ✓ | ✓ (owned by self, not by Org) | ✓ (owned by self) | — | — | ✗ |
| 13 | Delete Project | ✓ | ✓ | ∘ (only if created and currently Project Owner) | ✓ | ✗ | ✗ |
| 14 | Invite/remove Project Member | ✓ | ✓ (inherited) | ✗ | ✓ | ✗ | ✗ |
| 15 | Create/edit Agent, Key Group, RAG set | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ |
| 16 | Create Workspace, Chat Room, Workflow | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ |
| 17 | Send messages in Chat Room | ✓ (any) | per room ACL | per room ACL | per room ACL | per room ACL | per room ACL |
| 18 | Create / revoke Guest invite link | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ |
| 19 | Export chat history | ✓ | ✓ | ∘ (own messages) | ✓ | ∘ (own messages) | ∘ (own messages) |
| 20 | Manually delete chat message | ✓ | ✓ | ∘ (own) | ✓ | ∘ (own) | ∘ (own) |
| 21 | View audit log | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 22 | Ban user / IP | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 23 | Delete any user | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 24 | Read *any* user's data | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |

**[R5.05]** All authorization MUST be enforced server-side in a single `permissions` service. Frontend visibility is advisory only.

---

## 6. Identity & Access Management

### 6.1 Registration and login

- **[R6.01]** Sign-up: email + password + CAPTCHA challenge (hCaptcha or Cloudflare Turnstile — Admin-configurable). Password policy: ≥ 10 chars, ≥ 1 letter, ≥ 1 digit, ≥ 1 symbol. Hashing: **Argon2id** (memory 64 MiB, time 3, parallelism 2). Argon2id is chosen over Bcrypt to avoid Bcrypt's 72-byte silent truncation and to get memory-hardness against GPU cracking.
- **[R6.02]** Email verification: user receives a verification token link; account is in `pending` state until verified. Unverified accounts cannot create Orgs/Projects nor accept Guest invites. Verification tokens are delivered via `GET /api/auth/verify-email?token=<b64url>` (query form, magic-link pattern) and consumed server-side.
- **[R6.03]** Login returns:
  - a short-lived **JWT access token** (15 min, signed **RS256**; signing private key stored as a Vault Transit key — never on the application filesystem; public key(s) distributed to backends for verification).
  - a long-lived **refresh token** (30 days, rotating; the hash of the token is the key in Redis `session:{sha256(refresh_token)}` with the session record as value).
  - Access-token revocation: an in-memory + Redis `jti_denylist:{jti}` set with TTL equal to token TTL; the auth middleware checks each request's `jti` against the denylist. Adding a jti happens on logout, password change, ban, or detected compromise.
  - Key rotation: JWT keys carry a `kid` header; rotated quarterly, with a 7-day overlap where both old and new `kid` verify.
- **[R6.04]** Failed-login throttling: 5 attempts / 15 min / account + 20 attempts / 15 min / IP → 15 min lockout, audit logged.
- **[R6.05]** Password reset via emailed one-time token (valid 30 min, single-use).
- **[R6.06]** Users can change their email (requires re-verification) and password (requires current password). A password change invalidates all existing refresh tokens of that user and denylists all currently-valid access tokens.
- **[R6.07]** Account self-deletion soft-deletes the account; the Admin can still recover within 60 days. **Exception**: if the user is the Original Creator of any Org with other active members, self-deletion is blocked until the Original-Creator role is transferred (see §8.5).
- **[R6.08]** Session termination: user can revoke individual refresh tokens ("sessions") from their profile page. Revoking a session also denylists the jti of any access token issued from that session's last refresh.

### 6.2 Invitations

- **[R6.09]** Org invitations: Org Owner sends invite by email. If invitee has no account, the invite link lands on a sign-up page; after sign-up + email verification, they are automatically enrolled in the Org.
- **[R6.10]** Project invitations: same UX, Project-scoped.
- **[R6.11]** Chat Room Guest links (§13): a URL token that anyone (registered or not) can open; if not logged in, user is redirected to register; after registration, they join the room as Guest.
- **[R6.12]** Guest links **cannot be revoked, expired, password-protected, or use-capped** (explicit stakeholder decision, Q43). Guest access is revoked only by deleting the Chat Room or banning the specific user.
- **[R6.13]** Bans (Admin only): can ban by user-id, by email, or by IP. Banned users/IPs receive HTTP 403 on any endpoint. All bans are audit-logged.

---

## 7. API Key Management

### 7.1 Providers

- **[R7.01]** Supported providers and their declared capabilities:

  | Provider | `llm_chat` | `embedding` | `rerank` |
  |---|:---:|:---:|:---:|
  | `claude` (Anthropic) | ✓ | ✗ | ✗ |
  | `openai` | ✓ | ✓ | ✗ |
  | `gemini` (Google) | ✓ | ✓ | ✗ |
  | `voyage` | ✗ | ✓ | ✗ |
  | `cohere` | ✗ | ✗ | ✓ |

  A Key may be used only where its provider declares the capability. A Key Group (§7.4) accepts only `llm_chat`-capable keys. RAG embedding configs (§10.1) accept only `embedding`-capable keys. RAG rerank configs (§10.2) accept only `rerank`-capable keys. Validation is done at Agent/RAG save time by the backend; mismatches are rejected with 422.

- **[R7.02]** Each provider has a validation endpoint hard-coded in the platform:
  - Anthropic: `POST /v1/messages` with a 1-token stub.
  - OpenAI: `GET /v1/models`.
  - Gemini: `GET /v1/models`.
  - Voyage: `POST /v1/embeddings` with `input:["ping"]`.
  - Cohere: `GET /v1/models`.
  These are invoked at upload time (R7.03 §7.2).

### 7.2 Upload flow

1. User submits `{provider, name, secret}` over HTTPS to `POST /api/keys`.
2. Backend immediately performs the provider validation call.
3. On success: `test_status = 'ok'`, key stored encrypted (see §7.6).
4. On failure: the record is stored with `test_status = 'failed'` and the failure message surfaced in the UI. The user may retry validation (`POST /api/keys/{id}/retest`) or delete the key.
5. **[R7.03]** **Plaintext of the key is never returned to any caller, including the uploader, including the Admin, after the initial creation request ends.** The API response after upload contains a masked preview (e.g., `sk-ant-...xE9a`) and the test status only. There is no "reveal" endpoint.

### 7.3 Key scopes

- **Individual Key**: owned by a user, listed under their personal key registry.
- **Project Key**: Individual Key **carried** into a specific project. Mechanically this is a `key_projects` junction row with `carried = true` plus a per-project override of rotation/limit settings. The *secret* is not copied.
- **[R7.04]** When a user leaves a Project or is removed, all `key_projects` rows where `key.owner_user_id = removed_user_id` are revoked. Active outbound calls using those keys complete but no new calls are issued.
- **[R7.05]** Carried keys can be used by Project Owners to build Key Groups. Project Owners can view **usage**, can view **rotation/limit settings**, but cannot view the secret (R7.03) and cannot transfer the key to another project.

### 7.4 Key Groups (rotation + limitation)

- **[R7.06]** A Key Group is an **ordered** list of keys (explicit priority: 1, 2, 3, …). Stakeholder chose ordered priority over round-robin (Q27).
- **[R7.07]** Rotation triggers are defined by the group creator (Q25). Configurable per group:
  - `rotate_on_error_codes`: list of HTTP/provider codes (e.g. 429, 500, 502, 503). When any listed code is returned, advance to next key immediately.
  - `rotate_on_token_quota`: boolean. If true, once the current key hits its hourly token cap (§7.5), the scheduler skips it until refresh.
  - `retry_on_error`: boolean. If true, apply exponential backoff before rotating. Parameters: `initial_delay_ms` (default 500), `multiplier` (default 2.0), `max_delay_ms` (default 30 000), `max_retries` (default 3), `jitter_pct` (default 20).
- **[R7.08]** When **all** keys in the Group are unavailable:
  - If the unavailability is due to **token quota only** (no errors), the scheduler queues the request up to `queue_wait_seconds` (default 60) and retries at the earliest refresh time. If still unavailable, returns `503 Key Group exhausted` to the caller.
  - If the unavailability is due to **API errors** from all keys, the scheduler continues exponential backoff until `max_retries` is exhausted on every key, then returns `503`.
- **[R7.09]** Usage Limitation scope: set per Key, inside the Key Group. Unit: **per hour** (Q28). Fields:
  - `max_input_tokens_per_hour`, `max_output_tokens_per_hour`, `max_requests_per_hour`. Any field omitted = unlimited.
- **[R7.10]** The scheduler maintains a per-key sliding-window counter in Redis (1-minute buckets for the trailing 60 minutes) to enforce hourly limits accurately.
- **[R7.11]** **UI notification**: when any key in a Group exceeds 80 % of *any* of its hourly limits, raise an in-app notification (Q29) to all Project Members with usage-view permission. No email or webhook.

### 7.5 Usage accounting

- **[R7.12]** Every outbound provider call is recorded: `(key_id, agent_id, chatroom_id, input_tokens, output_tokens, request_ms, http_status, error_code_if_any, timestamp)`. This feeds the per-hour limiter and the UI usage dashboard.
- **[R7.13]** Usage is retained for **13 months** to allow year-over-year comparisons. After 13 months, rows older than the cutoff are aggregated into daily totals and the raw rows deleted.
- **[R7.14]** SMAP does **not** reconcile with provider-side bills. The counters reflect only SMAP-side observations (stakeholder decision: no billing responsibility).

### 7.6 Key encryption at rest (Recommendation applied, Q22)

**Chosen scheme: Vault Transit + envelope encryption with per-record DEK.**

1. A Vault Transit key `smap/keys/provider-secret` exists (AES-256-GCM, exportable = false, deletion_allowed = false).
2. Per-record flow:
   - Generate a 256-bit DEK via `vault write -f transit/datakey/plaintext/smap/keys/provider-secret`.
   - Encrypt the provider secret locally with `DEK` (AES-256-GCM, fresh 96-bit nonce per write).
   - Store in Postgres: `ciphertext (bytea)`, `nonce (bytea)`, `dek_wrapped (bytea)` (the Transit-wrapped DEK), `ciphertext_hmac (bytea)` for tamper detection.
   - The plaintext DEK is zeroized from process memory immediately after use.
3. At outbound-call time, the Agent Runner:
   - Fetches `(ciphertext, nonce, dek_wrapped)` from DB.
   - Calls `vault write transit/decrypt/...` with `dek_wrapped` → plaintext DEK.
   - Decrypts ciphertext in memory, uses the secret for one outbound HTTPS call, zeroizes.
4. Vault root token and unseal keys live outside the Postgres database and outside the application container. Vault must be unsealed at boot by an operator (Shamir 3-of-5 in production, auto-unseal optional).
5. Postgres backups are encrypted at rest (operator-managed) but even a full Postgres dump does not leak plaintext keys without access to Vault.

**[R7.15]** No API, UI, or admin tool shall decrypt a key to display it. Decryption is invoked only by the Agent Runner for outbound calls, and the plaintext does not cross process boundaries.

---

## 8. Organizations, Individuals, Projects

### 8.1 Organization lifecycle

- **[R8.01]** Any verified Individual can create an Org. The creator is tagged `is_original_creator = true, role = OrgOwner`. Both bits are persisted.
- **[R8.02]** An Org may have multiple Org Owners.
- **[R8.03]** The Original Creator cannot:
  - Leave the Org (`DELETE /api/orgs/{id}/members/self` returns 403).
  - Demote themselves from OrgOwner to OrgMember.
  - Be demoted or kicked by any other Org Owner or Admin (Admin *can* delete the Org entirely, which also deletes the creator's membership).
- **[R8.04]** Only the Original Creator can delete an Org. Admin can also force-delete (platform governance).
- **[R8.05]** Org creation automatically creates a default Project named `Default Project` (Q30).

### 8.2 Individual-owned projects

- **[R8.06]** Any verified Individual can create a Project they personally own (`owner_type = 'user'`), independent of any Org. They become Project Owner. They can invite other Individuals as Project Members.
- **[R8.07]** Individual-owned projects cannot be moved into an Org (Q31: no migration).

### 8.3 Project membership

- **[R8.08]** Within an Org, all Org Owners are implicitly Project Owners of every project in that Org (computed at authorization time, not persisted, per R5.03).
- **[R8.09]** Org Members are not implicitly Project Members; they must be invited to each Project individually.
- **[R8.10]** Project Owners can invite/remove Project Members (both Org Owners and Org Members of the same Org, when Org-owned; any Individual when Individual-owned).

### 8.4 Deletion semantics (soft delete, Q32)

- **[R8.11]** Deleting an Org or a Project soft-deletes it (`deleted_at = now()`). It becomes invisible to non-Admin users immediately.
- **[R8.12]** A nightly job permanently deletes rows whose `deleted_at` is older than 60 days. Cascaded cleanups:
  - All Key Groups in the project (Keys themselves remain on owner's Individual registry).
  - All Agents, Workspaces, Chat Rooms, Messages, Attachments, Workflows, Graph RAG data.
  - All RAG documents and vector entries.
- **[R8.13]** Within the 60-day window, Admin may restore (`POST /api/admin/restore/{type}/{id}`).
- **[R8.14]** Individual account deletion triggers soft-delete of all **Individual-owned** Projects, and removes the user from all other memberships. For Orgs the user is the Original Creator of, see §8.5 — account deletion is blocked until the role is transferred or the Org is deleted. Same 60-day recovery window applies to soft-deleted resources.

### 8.5 Original Creator role transfer

The Original Creator is the only user who can delete the Org (R8.04), so losing this user without succession would orphan the Org. To prevent this, the role is **transferable but only with explicit consent**.

- **[R8.15]** The Original Creator can initiate a transfer to any other **current Org Owner** (not an Org Member; they must be promoted to Owner first). Targets who are not already Org Owners are rejected.
- **[R8.16]** Transfer protocol:
  1. Original Creator calls `POST /api/orgs/{id}/original-creator-transfers` with `{target_user_id}`. Returns `transfer_id`.
  2. The target Org Owner receives an in-app notification with accept/reject actions.
  3. Target accepts: `POST /api/orgs/{id}/original-creator-transfers/{transfer_id}/accept`. The `org_members.is_original_creator` bit flips atomically from initiator → target. Audit-logged.
  4. Target rejects or ignores: request expires after **7 days** and is discarded.
  5. Initiator can cancel: `DELETE /api/orgs/{id}/original-creator-transfers/{transfer_id}` before acceptance.
- **[R8.17]** At most one pending transfer per Org at any time. New requests while one is pending are rejected with 409.
- **[R8.18]** When a user with Original Creator status attempts account self-deletion:
  - If the user is Original Creator of **zero Orgs with other active members**, self-deletion proceeds (R8.14).
  - If the user is Original Creator of **one or more Orgs with other active members**, self-deletion is blocked (HTTP 409) with a list of affected Org IDs. The user must either:
    (a) transfer Original Creator to another Org Owner in each such Org, or
    (b) delete each such Org first (which, as Original Creator, they can do).
- **[R8.19]** An Admin can force-transfer Original Creator bypassing consent, in cases where the Original Creator account is inaccessible (bounced email, permanent absence). Always audit-logged as `org.original_creator_force_transferred`.

---

## 9. AI Agents

### 9.1 Composition

An Agent is defined by:

| Field | Type | Notes |
|---|---|---|
| `name` | string | Unique per project. |
| `model_hint` | enum | `claude` / `openai` / `gemini` — picks routing among the selected Key Group. |
| `key_group_id` | FK | Exactly one (Q34). |
| `system_prompt` | text | See §9.2 for read strategy. |
| `prompt_strategy` | enum | `full` or `lazy` (see §9.2). |
| `rag_config_id` | FK nullable | See §10. |
| `graphrag_config_id` | FK nullable | See §11. |
| `mcp_servers` | list | See §12. |
| `a2a_enabled` | bool | Whether this agent can call/receive A2A. |
| `context_mode` | enum | `general` or `compact` (Q39). |
| `context_token_cap` | int | Trigger for `/compact` when `context_mode = compact`. |
| `wakeup_config` | JSON | See §15. |
| `workflow_capabilities` | JSON | `{can_instruct, can_approve, can_create_subagent, max_subagents_alive}` — booleans + max-subagents cap per §15.6. |

- **[R9.01]** A Project Owner may create unlimited agents. Hard platform cap: 1 000 agents per project (prevents runaway DoS).
- **[R9.02]** Agents are not versioned; no export/import; no templates (Q41). Editing overwrites in place.
- **[R9.03]** Deleting an Agent soft-deletes it for 60 days (consistent with R8.11) since agents may be referenced by historical chat messages.

### 9.2 Prompt Read Strategy (Q36 — Recommendation applied)

The stakeholder asked to compare "inline every call" vs. "retrieve on demand" and offer both as choices.

**Analysis.** Two production patterns exist:

1. **Full / Inline (default).** The entire system prompt is concatenated into every model call. Pros: deterministic, no extra round trips, no retrieval errors. Cons: every call pays the tokens; long system prompts (e.g., skill libraries) dominate the context.
2. **Lazy / Sectioned (on-demand retrieval).** The system prompt is authored as *sections* with YAML front-matter (`id`, `when_to_invoke`, `description`). Only a small **index** (all sections' titles + descriptions) is included in every call. Individual section bodies are fetched via a tool call (`load_section(id)`) when the model decides it needs them. This mirrors the pattern used by Anthropic's skills feature and by Claude Code's skill-loader tool (referenced by the stakeholder, Q66).

**Design.**

- **[R9.04]** `prompt_strategy` is per-agent and selectable by the Agent Designer.
- **[R9.05]** When `full`: `system_prompt` is stored as a single markdown blob and sent verbatim.
- **[R9.06]** When `lazy`: `system_prompt` is parsed as a multi-section markdown document. Each section must begin with a fenced YAML block:
  ```markdown
  ---
  id: refund-policy
  title: Refund Policy
  description: Rules for issuing customer refunds. Invoke when the user asks about returns, refunds, or cancellations.
  ---
  <section body …>
  ```
  The system auto-generates an **index prompt** ("You have N sections available: …") plus a built-in tool `load_prompt_section(id)`. The model invokes the tool when it needs a section's body.
- **[R9.07]** Section bodies loaded within a turn are cached for that turn only; the next turn re-runs retrieval (agents are dynamic; authors can edit).
- **[R9.08]** The lazy strategy is incompatible with providers that don't support tool use. For such a case, the system falls back to `full` silently and raises a UI warning.

### 9.3 Context Management (Q39)

- **[R9.09]** `context_mode = general`: unbounded growth. The system sends the entire chat history (subject to provider's hard context limit, at which point the provider will error; this is surfaced to the UI).
- **[R9.10]** `context_mode = compact`: when the running token count of the *next* request would exceed `context_token_cap` (default: 75 % of provider's context limit), the system runs `/compact`:
  1. Select the oldest un-compacted range of messages.
  2. Use the same Agent's Key Group to issue a summarization call with a system prompt modeled after Claude Code's `/compact` ("Summarize the following conversation preserving decisions, file paths, and open questions. Output ≤ 2 000 tokens").
  3. Replace the range with a single system-role message tagged `"type":"compact_summary"`.
  4. The user-visible chat log is unchanged; only what is sent to the model changes.
- **[R9.11]** `/compact` is retryable; on failure the system keeps the original context and logs the failure to audit.

### 9.4 A2A (Agent-to-Agent)

Custom SMAP protocol (Q35).

- **[R9.12]** Transport: internal only, no external exposure. Messages flow via the event bus.
- **[R9.13]** Message schema (JSON):
  ```json
  {
    "id": "uuid",
    "from_agent": "uuid",
    "to_agent": "uuid | 'broadcast:workspace'",
    "workflow_run_id": "uuid | null",
    "type": "call | reply | notify | instruct",
    "payload": { "text": "...", "tool_calls": [...] },
    "correlation_id": "uuid",
    "created_at": "RFC3339"
  }
  ```
- **[R9.14]** Each agent has an inbox (Redis Stream named `a2a:agent:{agent_id}`). Agents consume in FIFO order.
- **[R9.15]** Synchronous A2A: the `call` type blocks the caller's current turn until a matching `reply` arrives (timeout default 60 s, configurable per call).
- **[R9.16]** Asynchronous A2A: `notify` is fire-and-forget; `instruct` requires acknowledgement (see §15.6 for instruct semantics).
- **[R9.17]** **A2A access scope.** An Agent may send A2A to another Agent only when:
  1. Both agents live in the **same Project**.
  2. Both agents have `a2a_enabled = true`.
  3. The caller is invoking from a context (ChatRoom or Workflow run) that the callee is also attached to, OR the callee is configured with `wakeup_config.call_only.enabled = true` at the project level.
  Cross-project A2A is always denied. A2A to agents in soft-deleted projects is denied. Violations return `a2a_forbidden` and are audit-logged.

---

## 10. RAG Subsystem

### 10.1 Ingestion

- **[R10.01]** Supported source formats: `pdf`, `docx`, `md`, `txt` (Q37).
- **[R10.02]** Per-upload limit: 1 GB (shared with chat-room upload limit, §13). Files older than 3 days are auto-deleted from object store.
- **[R10.03]** Parser pipeline:
  - PDF: `pypdf` + OCR fallback (`tesseract`, opt-in per agent).
  - DOCX: `python-docx`.
  - MD / TXT: raw.
- **[R10.04]** Chunking strategy (Q37): user picks per RAG config:
  - **Fixed-size**: `chunk_size_tokens` (default 512), `chunk_overlap_tokens` (default 64).
  - **Semantic**: sentence-aware splitter (`semantic-text-splitter`) with target `max_tokens_per_chunk` (default 512) and `similarity_threshold` (default 0.6).
- **[R10.05]** Embedding model: choose per RAG config from `openai:text-embedding-3-small`, `openai:text-embedding-3-large`, `gemini:text-embedding-004`, `voyage-3` (if user supplies Voyage key). Keys come from the owning project's Key Groups.
- **[R10.06]** Vectors are stored in Qdrant with collection name `rag_{project_id}` and payload `{doc_id, chunk_idx, agent_ids[]}`.

### 10.2 Retrieval

- **[R10.07]** Default top-k = 8. Configurable per agent.
- **[R10.08]** Rerank (optional, Q37): `cohere:rerank-3` or a local `bge-reranker-v2-m3`. User choice per RAG config. If Cohere, requires a user-supplied Cohere key; if local, uses the app's bundled reranker service (CPU inference acceptable at 100 users).
- **[R10.09]** Retrieved chunks are inserted as system-role messages tagged `type:"rag"` immediately before the user's turn.

### 10.3 Permissions

- **[R10.10]** Uploading RAG documents requires Project Owner role.
- **[R10.11]** Documents are scoped per project; they can be shared with multiple Agents in the same Project but never across Projects.

---

## 11. Graph RAG Subsystem

### 11.1 Purpose

Maintain a project-scoped knowledge graph (Neo4j) built incrementally from chat content. Agents can query the graph alongside (or instead of) RAG.

### 11.2 Build flow

- **[R11.01]** Each Graph RAG config declares its own **Key Group** (Q40) — distinct from the consumer agent's Key Group, so graph-builder costs are billed to a separate owner if desired.
- **[R11.02]** Build triggers (Q40) mirror the Agent `wakeup_config`, with one semantic difference: where an Agent's wake-up counts *user* messages, the Graph RAG builder counts **all chat-room messages** (user + agent). Supported triggers:
  - Every N messages in the chat room.
  - Silence of T minutes in the chat room.
  - Manual (`POST /api/graphrag/{id}/build`).
- **[R11.03]** The builder worker consumes the *delta* since the last build:
  1. Gather new messages.
  2. Prompt the builder LLM (via its Key Group) to extract `(subject, relation, object, confidence, evidence_msg_ids)` triples.
  3. Upsert nodes/edges into Neo4j with `project_id` and `graphrag_config_id` labels.
  4. Embed node text into Qdrant (collection `graphrag_{project_id}`) so queries can be hybrid vector+graph.
- **[R11.04]** Each build is transactional across Neo4j and Qdrant using a **two-phase commit with compensation**; a failure does not leave inconsistent state (see §11.2a).
- **[R11.05]** A Graph RAG config is attached to at most one Agent (1:1 in v1).

### 11.2a Two-phase commit across Neo4j + Qdrant

Because Neo4j and Qdrant are independent systems, a naïve "write both" sequence can leave one committed and the other not. SMAP uses an **ordered 2-phase commit with a deterministic compensation** path:

1. **Prepare.** Extract `(subject, relation, object, confidence, evidence_msg_ids)` triples + node text. Stage them in a local Python `BuildBatch` object; set `graphrag_configs.last_build_state = 'running'`.
2. **Phase 1 — Neo4j.** Open a Neo4j transaction, upsert all nodes/edges, include a `build_id uuid` property on every created/updated row so compensation can identify them. Commit. Set `last_build_state = 'neo4j_committed'`.
3. **Phase 2 — Qdrant.** Upsert the batch into Qdrant with the same `build_id` in the payload. Qdrant upserts are idempotent on `point_id`. On success, set `last_build_state = 'qdrant_committed'` then immediately to `idle`.
4. **Failure in Phase 1** → nothing is committed; set `last_build_state = 'failed'` and surface the error.
5. **Failure in Phase 2** → set `last_build_state = 'failed_compensating'`. A reconciliation worker runs every 60 s scanning rows in this state and:
   - Retries the Qdrant upsert up to 5 times (exp backoff).
   - If all retries fail, it **compensates by rolling back Neo4j**: deletes nodes/edges with this `build_id` that did not exist before the build (pre-build snapshot of `(node_id, version)` is cached in Redis under `graphrag:build:{config_id}:{build_id}`). On successful rollback, `last_build_state = 'failed'`.
- **[R11a.01]** The build lock is a single Redis key `graphrag:lock:{config_id}` with 10-minute TTL, released on completion. Only one build per config runs at a time.
- **[R11a.02]** Admin can manually force `last_build_state = 'idle'` via `POST /api/admin/graphrag/{id}/reset` in case reconciliation is stuck; this is always audit-logged.

### 11.3 Query

- **[R11.06]** At agent invocation time, the agent's turn triggers a hybrid query:
  - Vector search on graphrag collection → top entities.
  - From each top entity, expand 1–2 hops in Neo4j.
  - Return entities + relations + evidence-message excerpts (max 2 KB total) as a system message tagged `type:"graphrag"`.

---

## 12. MCP Integration

### 12.1 MCP sources (Q38)

1. **Built-in tools** (always available to any agent that enables them):
   - `file`: read/write within the agent's sandboxed workspace directory.
   - `web_search`: a platform-written adapter that calls an external search provider (Brave / Serper / Tavily / Google CSE). The **provider's API key is supplied by the Project Owner** (see §12.4); SMAP does not pay search bills, consistent with the BYO-key business model. If no key is configured on the Project, the tool is unavailable and any agent that lists it as allowed receives a clear error rather than a silent no-op.
   - `code_exec`: executes Python or JavaScript snippets in a sandboxed container (see §12.3).
2. **User-provided MCP servers**:
   - **URL**: user supplies a remote MCP endpoint (HTTPS only). SMAP connects over the MCP protocol.
   - **Package**: user supplies an installable MCP server (npm package name, pip package name, or OCI image reference). SMAP builds an ephemeral container on demand.

### 12.2 Agent binding

- **[R12.01]** An agent declares a list of `mcp_servers`, each with `{source, reference, allowlist_of_tools}`.
- **[R12.02]** Each MCP call is a distinct audit entry.

### 12.3 Sandbox design (Recommendation applied, Q38)

**Threat model.** User-provided MCP code is untrusted. Worst cases: exfiltration of platform secrets (API keys, Vault token), SSRF into internal network, crypto-mining, disk exhaustion, privilege escalation.

**Defenses.**

- **[R12.03]** Every user-supplied MCP server runs in an **ephemeral container** with these constraints:
  - Runtime: Docker with **gVisor (`runsc`)** runtime for syscall filtering, or alternatively Kata Containers if gVisor not supported by host kernel.
  - Image: pinned by digest; rebuilt from the declared package at cold start, cached per-agent-version for 24 h, purged on update.
  - User: UID ≥ 10 000, no `root`, no `sudo`, `no-new-privileges`.
  - Filesystem: root is **read-only**, a single 100 MB `tmpfs` at `/workspace`, no volume mounts to host.
  - Network: attached only to `smap_egress_net`, whose only reachable destination is the **Egress Proxy** (see below). DNS resolves only names on the allowlist.
  - Resources: `--memory=512m --cpus=0.5 --pids-limit=128 --ulimit nofile=512`.
  - Lifetime: container is `--rm`, destroyed on agent turn completion. The sandbox's root filesystem is ephemeral. Persistent state for the built-in `file` tool lives on a **per-agent named Docker volume** (`smap-agent-fs-{agent_id}`) mounted read-write at `/workspace`; this volume is NOT the tmpfs and NOT the root fs — it is a quota-limited persistent directory (hard quota 100 MB enforced via the `size` volume option on tmpfs or via a dedicated ext4 loopback mount). User-provided MCP containers do NOT receive this mount; only the built-in `file` tool container does. When an Agent is soft-deleted, its volume is retained for the 60-day recovery window, then removed by the nightly cleanup.
- **[R12.04]** **Egress Proxy** is a FastAPI-based HTTPS forward proxy that:
  - Enforces a project-scoped allowlist of hostnames (defaults: empty; Project Owner must add).
  - Blocks all RFC 1918, link-local, loopback, and metadata addresses (169.254/16, GCP/Azure/AWS metadata IPs).
  - Does not forward any `Authorization` header from the sandbox; MCP servers cannot impersonate the platform's own keys.
  - Logs every request with truncated bodies for audit.
- **[R12.05]** Built-in `code_exec` uses the same sandbox but with a curated image (python:3.12-slim + common scientific libs) and the same gVisor policy.
- **[R12.06]** URL-based MCP calls do not use the sandbox but are always routed through the Egress Proxy; the same allowlist applies.

### 12.4 Web Search provider configuration (Decision applied — option B)

- **[R12.07]** The built-in `web_search` tool is a platform-written adapter with a pluggable provider interface. Supported providers in v1: **Brave Search**, **Serper**, **Tavily**, **Google Programmable Search (CSE)**.
- **[R12.08]** Search API keys are **supplied by Project Owners**, never by the platform. Keys are stored using the same envelope-encryption scheme as LLM provider keys (§7.6), in a separate row class (`search_keys`) so they cannot be confused with LLM keys at the API layer.
- **[R12.09]** Per Project, at most one **active** search provider may be configured at any time; switching the provider is a single write that invalidates cache.
- **[R12.10]** If a Project has no active search key:
  - Agents that do **not** list `web_search` in `allowed_tools` are unaffected.
  - Agents that **do** list it receive a structured error (`tool_unavailable: search_key_not_configured`) on invocation. The UI's agent editor flags the mis-config on save as a warning.
- **[R12.11]** Adapter contract. Each provider adapter implements:
  ```python
  class SearchAdapter(Protocol):
      async def search(self, query: str, *, top_k: int, locale: str,
                       freshness: Literal["any","day","week","month","year"]
                       ) -> list[SearchResult]:
          ...
  SearchResult = {"title": str, "url": str, "snippet": str,
                  "published_at": Optional[datetime], "score": float}
  ```
- **[R12.12]** `top_k` default 5, hard max 20. Results are capped at 4 KB total serialized size before being returned to the agent, to prevent context bloat.
- **[R12.13]** Caching. The adapter caches `(provider, query_normalized, top_k, locale, freshness)` → results in Redis for 10 minutes (key `search:{hash}`) to avoid billing duplicate user questions. Cache is scoped per Project; no cross-Project cache hits.
- **[R12.14]** Rate limiting. Per Project, max 60 searches / minute (configurable by Admin) to shield against runaway agents. Exceeding the rate returns `tool_rate_limited`.
- **[R12.15]** All search calls are audit-logged as `mcp.tool_invoked` with `tool="web_search"`, truncated query preview (≤ 256 chars), provider name, HTTP status, result count.
- **[R12.16]** Outbound search HTTP requests leave the platform through the **Egress Proxy** (§12.3), not from within any user-facing container. The Proxy's allowlist is seeded with the four providers' documented hostnames (`api.search.brave.com`, `google.serper.dev`, `api.tavily.com`, `www.googleapis.com`).

### 12.5 Initial adapter rollout

- **[R12.17]** The v1 release ships **one** adapter implemented end-to-end plus the plug-in framework; the remaining three adapters are thin classes that conform to the same protocol and ship in the same release only after a parity test. The **default adapter to implement first** is tracked as an open item (see §26); the stakeholder selection will be recorded there before implementation begins.

---

## 13. Chat Rooms

### 13.1 Structure

- **[R13.01]** A Project contains **N Workspaces**, 1:N (Q42). Workspaces cannot be nested.
- **[R13.02]** Every Workspace contains **at least one Chat Room** (Q42). Deleting the last Chat Room in a Workspace creates a new default one.
- **[R13.03]** A Workspace can also define workflows (§15) that operate on its chat rooms.

### 13.2 Access modes (Q43)

Four composable flags per chat room:

- `allow_org_members` (project-owned + org-owned only)
- `allow_project_members`
- `allow_project_owners_only` (overrides the two above; if true, only Project Owners enter)
- `allow_guest_links` (if true, Guest Link URL is active and shareable)

**[R13.04]** These flags are independently togglable; any subset is valid. The UI prevents semantically useless combinations (e.g., `project_owners_only = true` while `project_members = true` is auto-corrected).

### 13.3 Guest links (Q18, Q43)

- **[R13.05]** A Chat Room exposes a permanent URL of form `https://<host>/g/<chatroom_id>/<opaque_token>`.
- **[R13.06]** Opening the URL without login lands on the registration page with the token preserved; after sign-up + email verification the user is auto-joined as Guest.
- **[R13.07]** No expiry, no use cap, no password, **no revocation** (explicit stakeholder decision). To revoke access, delete the chat room or ban the user.

### 13.4 Input (Q44)

- **[R13.08]** Supported input types: text (markdown source), image (`png`, `jpg`, `webp`), and document attachments (`pdf`, `md`, `txt`, `docx`). **No audio** (stakeholder removed voice support).
- **[R13.09]** Single-upload size cap: 1 GB. Rejection at frontend and hard-enforced at gateway.
- **[R13.10]** All attachments are stored in MinIO under `/chat-uploads/{project_id}/{chatroom_id}/{msg_id}/{filename}`. Lifecycle rule deletes objects after 3 days.
- **[R13.11]** Messages keep a pointer to the object and, after expiry, surface the text `[attachment expired]` in the UI.

### 13.5 Output / rendering (Q45)

- **[R13.12]** Supported rendering: **full markdown**, inline **HTML** (stakeholder explicitly allowed), fenced **code blocks** with syntax highlighting, **LaTeX** via KaTeX, **Mermaid** diagrams.
- **[R13.13]** Image embedding via public links is allowed. There is **no domain allowlist** (stakeholder accepted the SSRF tradeoff). A warning banner explains that linked images are fetched by the user's browser, not by the server, so server-side SSRF is inherently impossible.
- **[R13.14]** For HTML: **double sanitization**. On write, the backend sanitizes with `bleach` (Python) using the same allowlist as the frontend; the sanitized result is stored and indexed. On read, the frontend additionally sanitizes with DOMPurify before render. Both layers deny: `<script>`, event handlers (`on*`), `javascript:` / `data:text/html` URIs, `<object>`, `<embed>`, `<iframe>`, `<form>`, `<meta http-equiv="refresh">`. Inline `style` attributes are allowed but CSS is stripped of `url()`, `@import`, and `expression()` to prevent external-resource or script-injection via CSS. Chat exports (R13.17) include only the sanitized form; raw markdown is preserved separately in the JSON manifest.

### 13.6 History (Q46)

- **[R13.15]** Messages are retained for **5 years** from creation by default, platform-wide.
- **[R13.16]** Users (with their permission scope) can **manually delete** messages; deleted messages are removed immediately from both DB and search index.
- **[R13.17]** Users can **export** a chat room's history as JSON + a folder of attachments (attachments are regenerated from MinIO if still alive; missing ones are noted).
- **[R13.18]** Search is **database-only** using PostgreSQL `tsvector` with GIN index on message content. No external search engine. Guest users can search only within rooms they belong to.

### 13.7 Realtime

- **[R13.19]** Clients connect to `WSS /ws/chatroom/{id}` after authenticating. Events pushed:
  - `message.created`, `message.updated`, `message.deleted`
  - `agent.thinking`, `agent.token` (streaming), `agent.finished`
  - `presence.joined`, `presence.left`
  - `approval.requested`, `approval.resolved`
  - `workflow.state_changed`
- **[R13.20]** Server maintains per-room WebSocket hub. On backend restart, the client reconnects and requests a delta via REST (`GET /api/chatrooms/{id}/messages?since=<id>`).

### 13.8 Message edit and deletion rules

- **[R13.21]** **Users** may edit their **own** messages within **5 minutes** of creation. Beyond 5 minutes the message is immutable to the author; only Admin/Project Owner can edit it. All edits preserve the original via a `message_edits` audit row (`id, message_id, old_content, edited_at, edited_by_user_id`) retained until the parent message is hard-deleted.
- **[R13.22]** **Agents** cannot edit their own past messages. An agent wishing to correct itself must send a new message.
- **[R13.23]** **Admin / Project Owner** may edit any message in their scope; edits emit a `message.updated` WS event and an audit row `message.edited_by_moderator`.
- **[R13.24]** Deletion (R13.16) removes the content row and index; edit history for that message is also purged.

### 13.9 Retention purge

- **[R13.25]** Messages older than **5 years** (R13.15) are hard-deleted nightly. Each purge emits an audit event `message.purged_by_retention` with `{chatroom_id, count, oldest_kept_at}`. Associated attachments (if still in MinIO) are likewise deleted. The 5-year window is a platform default and is not user-configurable in v1.

---

## 14. Workflow Engine

### 14.1 Model

- **[R14.01]** Stakeholder asked for enterprise-grade flexibility with a visual editor (Q48). The engine is a **hybrid**: nodes are placed on a DAG canvas, but each node is an **FSM sub-unit** that can self-loop and fire events. Concretely: workflows are authored as DAGs of *activities*; each activity is driven by an internal state machine; activities communicate via events on the event bus.
- **[R14.02]** A workflow has nodes of these types: `trigger`, `agent_invocation`, `approval_gate`, `condition`, `instruct`, `subagent_spawn`, `wait_for_event`, `parallel`, `join`, `set_variable`, `end`. The normative definition lives in `docs/workflow.schema.json`; REQUIREMENTS.md must be updated whenever the schema adds or renames a node type.
- **[R14.03]** Workflow definitions are stored as JSON (`workflow.definition_json`). A separate JSON-schema validator ensures integrity.

### 14.2 Visual editor

- **[R14.04]** Frontend uses Vue Flow (`@vue-flow/core`) for the canvas.
- **[R14.05]** Each node type has a Vue component for its configuration panel (prompt, agent selection, branch conditions, timeout, etc.).
- **[R14.06]** The editor supports: drag-and-drop, copy/paste, undo/redo, versionless save (edits are in place per R9.02), linting (unreachable nodes, missing references), and a dry-run simulator that steps through with synthetic inputs.

### 14.3 Execution

- **[R14.07]** A workflow run is started by a trigger (`manual`, `cron`, `message_received`, `a2a_event`, or `wakeup_signal` — the full list matches `trigger_config.trigger_type` in `docs/workflow.schema.json`).
- **[R14.08]** The engine maintains a `workflow_run` record with `state: running | waiting | succeeded | failed | cancelled` and a `step_trace` list of activity records.
- **[R14.09]** Agent invocations inside a workflow respect all agent settings (wake-up, key group, prompt strategy). Workflow-issued invocations are logged with `origin = 'workflow'`.
- **[R14.10]** The trace (Q55) is stored in the DB and visible to Admin + Project Owners in a dedicated **backstage** panel. It is **not** surfaced in the chat room UI.

---

## 15. Wake-up, Approval, Instruct, Sub-Agents

### 15.1 Wake-up configuration (Q49)

Per-agent JSON:

```json
{
  "triggers": {
    "every_n_messages": { "enabled": true, "n": 3 },
    "silence_minutes":   { "enabled": true, "t_minutes": 2,
                           "autostop_rounds": 5, "autostop_max_default": 100 },
    "call_only":         { "enabled": false }
  },
  "allow_self_open":     false
}
```

- **[R15.01]** `every_n_messages.n` counts **all messages** in the room (user + agent), scoped to the room (Q49).
- **[R15.02]** `silence_minutes.t_minutes` starts counting when the room contains at least one connected live user and no message arrives for T minutes. Guests who are logged in but not inside the room do not prevent silence (Q49).
- **[R15.03]** `autostop_rounds`: a "round" is an agent message followed by no user message (Q49). After `autostop_rounds` such rounds, the silence trigger stops for the room until a user sends a new message.
- **[R15.04]** `autostop_rounds` default and hard cap: default 5, hard cap 100.
- **[R15.05]** `allow_self_open = false`: an agent cannot speak first in a room where nobody has yet sent a message (Q49).
- **[R15.05a]** **`call_only` trigger**: when `triggers.call_only.enabled = true`, the agent ignores `every_n_messages` and `silence_minutes` and only wakes when it receives an A2A `call` or `instruct` message targeting it. An agent with all three trigger sub-objects disabled is inert (permanent silence until manually prodded).
- **[R15.05b]** **Guest presence and silence.** For the purpose of `silence_minutes`, a "live user" is any user (including a Guest) who currently has an open WebSocket connection to the Chat Room (`ws:presence:{room_id}` contains their user_id). Users logged in elsewhere but not in this room do not count. When the live-user set becomes empty, the silence timer pauses.

### 15.2 Self-modification of wake-up (Q50)

- **[R15.06]** An Agent can modify **only** these two fields during a run, via a built-in tool `update_wakeup({every_n_messages?, silence_minutes?})`: `every_n_messages.n` and `silence_minutes.t_minutes`.
- **[R15.07]** Hard bounds enforced server-side: `n ∈ [1, 1000]`, `t_minutes ∈ [1, 1440]`. Out-of-range values are clamped and the clamp is audit-logged.
- **[R15.08]** Platform Admin can also set *soft* per-agent bounds at creation time; self-modification must respect these.

### 15.3 Setting refresh (Q51)

- **[R15.09]** The Agent Designer can configure a `refresh_every_hours` value. Every T hours, the wake-up configuration is reset to the Agent Designer's initial values (which equal the most recent human value since there is no versioning — Q51 acknowledged).

### 15.4 Approval (Q52)

- **[R15.10]** An Approval Gate node declares:
  - `mode`: `single | majority | consensus`
  - `approvers`: list of agent ids
  - `leader_agent_id`: required; fallback authority for timeout or no-consensus
  - `timeout_seconds`: mandatory; integer 1..86400
- **[R15.11]** `single`: if the leader approves, passes; else fails.
- **[R15.12]** `majority`: > 50 % of listed approvers must approve. Ties are broken by the leader.
- **[R15.13]** `consensus`: all approvers must propose, debate, and converge on the same verdict. If not converged by `timeout_seconds`, the leader's verdict wins.
- **[R15.14]** Approver agents consume tokens from **their own** Key Group (Q52: "whoever owns the key"). The leader agent's Key Group covers the final decision announcement.

### 15.5 Instruct (Q53)

- **[R15.15]** An instructed agent **cannot refuse**. The instruction is enqueued into its A2A inbox with `type = instruct`.
- **[R15.16]** **Loop detection (Recommendation applied):**
  - Every instruction carries a `chain_id` (uuid) and a `path` array of agent ids traversed so far.
  - When an agent is about to dispatch an instruction to agent X:
    1. Reject if `X in path` → cycle detected. Return error to the issuing agent and audit log.
    2. Reject if `len(path) >= max_chain_depth` (platform default 5, configurable at project level up to 20).
    3. Reject if the issuing agent has exceeded `max_instructions_per_wakeup` (default 5).
  - The chain has a hard wall-clock budget (`max_chain_seconds`, default 120); if exceeded, the root workflow_run is aborted.
- **[R15.17]** Instruction audit records include `chain_id`, `path`, `issuer`, `target`, `payload_hash`, `result`, `chain_depth_at_issue`.

### 15.6 Sub-agents (Q54)

- **[R15.18]** An agent with `workflow_capabilities.can_create_subagent = true` may call `spawn_subagent(parent_agent_id, task_description)`. The sub-agent inherits the parent's Key Group.
- **[R15.19]** **Recursion depth is exactly 1**: sub-agents cannot spawn sub-sub-agents. Attempt raises an error and is audit-logged.
- **[R15.20]** `max_subagents_alive_simultaneously` is configurable per parent agent (default 3, hard cap 20).
- **[R15.21]** Life cycle: a sub-agent exists only for its task. On task end (completion or error), its runtime state and ephemeral context are purged. Sub-agents are persisted as ephemeral rows in `agent_instances` for audit purposes and deleted after 30 days.
- **[R15.22]** **Sub-agent configuration inheritance** (from parent agent):

  | Field | Inherited? | Notes |
  |---|---|---|
  | `key_group_id`            | ✓ (enforced) | Usage accrues to the parent's key owner. |
  | `system_prompt` + `prompt_strategy` | ✓ | Parent's prompt forms the sub-agent's base. The spawn task description is appended as a user-role message. |
  | `model_hint`              | ✓ | Same provider chain. |
  | `a2a_enabled`             | ✗ (forced `false`) | Sub-agents cannot initiate A2A to prevent fan-out loops. |
  | `mcp_servers`             | ✓ | Same toolset, same egress allowlist. |
  | `rag_config_id`           | ✗ (forced null) | Sub-agents operate on task context only. |
  | `graphrag_config_id`      | ✗ (forced null) | Same reason. |
  | `context_mode` + cap      | ✓ | Same compaction rules. |
  | `wakeup_config`           | ✗ | Sub-agents respond to exactly one task and end; wake-up is not applicable. |
  | `workflow_capabilities.can_create_subagent` | ✗ (forced `false`) | Enforces depth = 1 (R15.19). |
  | `workflow_capabilities.can_instruct, can_approve` | ✗ (forced `false`) | Sub-agents do only the delegated task. |
- **[R15.23]** **Usage attribution for sub-agents.** `key_usage_events.agent_id` stores the sub-agent's ephemeral id, and a new column `parent_agent_id FK agents NULL` aggregates cost to the parent for dashboard display. Billing (API provider side) always lands on the parent's Key Group owner since the keys themselves are inherited.

---

## 16. Admin Console

Admin (platform operator, Q17) has a dedicated `/admin` area.

- **[R16.01]** View any Org, Project, Workspace, Chat Room, Key metadata, Agent configuration, Workflow run, Audit log, Usage report.
- **[R16.02]** Ban/unban user by id or email; ban/unban IP range; the ban list is consulted by the rate-limit middleware.
- **[R16.03]** Soft-delete any user; hard-delete (after 60-day grace) any user.
- **[R16.04]** Impersonation for support: "view as" (read-only) per-user session, always audit-logged.
- **[R16.05]** Admin **never** sees API key plaintext (R7.15) even via Admin tools.
- **[R16.06]** Admin UI surfaces workflow traces, sub-agent chains, instruction chains, approval histories.

---

## 17. Audit Logging (Q56)

### 17.1 Scope (Recommendation applied)

The system records a structured event for every action in the following categories. All events are JSON with common fields `{id, actor_user_id, actor_ip, action, resource_type, resource_id, metadata, created_at, session_id, request_id}`.

| Category | Example actions |
|---|---|
| Auth | `auth.login.success`, `auth.login.failed`, `auth.logout`, `auth.password_reset_requested`, `auth.password_changed`, `auth.email_changed`, `auth.email_verified`, `auth.session_revoked` |
| User lifecycle | `user.created`, `user.deleted`, `user.banned`, `user.unbanned`, `user.impersonated_begin`, `user.impersonated_end` |
| Org | `org.created`, `org.deleted`, `org.restored`, `org.member_invited`, `org.member_removed`, `org.owner_promoted`, `org.owner_demoted` (blocked for Original Creator) |
| Project | `project.created`, `project.deleted`, `project.restored`, `project.member_invited`, `project.member_removed` |
| Keys | `key.uploaded`, `key.test_success`, `key.test_failed`, `key.deleted`, `key.carried_into_project`, `key.withdrawn_from_project`, `key.usage_threshold_hit`, `key.rotation_triggered`, `key.retry_exhausted` |
| Search keys | `search_key.uploaded`, `search_key.test_success`, `search_key.test_failed`, `search_key.activated`, `search_key.deactivated`, `search_key.deleted` |
| Agents / RAG / GraphRAG / MCP | `agent.created`, `agent.edited`, `agent.deleted`, `rag.document_uploaded`, `rag.indexed`, `graphrag.build_started`, `graphrag.build_finished`, `mcp.tool_invoked` (with tool name + truncated args), `mcp.egress_blocked` |
| Chat | `chatroom.created`, `chatroom.deleted`, `message.sent`, `message.deleted`, `message.exported`, `attachment.uploaded`, `attachment.expired`, `guest.joined` |
| Workflow | `workflow.created`, `workflow.edited`, `workflow.run_started`, `workflow.run_finished`, `workflow.step_started`, `workflow.step_finished`, `workflow.step_failed`, `approval.requested`, `approval.resolved`, `instruct.issued`, `instruct.rejected_loop`, `subagent.spawned`, `subagent.destroyed` |
| Admin | `admin.ban_user`, `admin.unban_user`, `admin.delete_user`, `admin.restore_resource`, `admin.view_as_started`, `admin.view_as_ended` |

- **[R17.01]** Retention: **365 days**. After retention, rows are deleted nightly.
- **[R17.02]** Visibility: **Admin only** (Q56). No other role sees audit logs.
- **[R17.03]** Message-send events store metadata only: sender, room, byte size, hash of content; **not** the content (content is available in `messages` table). `mcp.tool_invoked` stores a truncated arg preview (≤ 1 KB) after **secret redaction**: any JSON key matching the case-insensitive regex `^(authorization|api[_-]?key|secret|password|token|bearer|private[_-]?key|cookie|session)$` has its value replaced with `"<redacted>"` before logging. String values whose content matches known secret shapes (e.g., `sk-ant-…`, `sk-…` ≥ 40 chars, PEM headers) are likewise redacted regardless of the key name.
- **[R17.04]** Audit writes are **append-only** at the DB level via a trigger that denies UPDATE/DELETE except by a nightly retention job.

---

## 18. Notifications

- **[R18.01]** Channel: **in-app UI only** (Q29, Q58). No email, no webhook, no Slack in v1.
- **[R18.02]** Events that trigger a notification:
  - Key usage hit 80 % of any hourly limit (to users with usage-view permission).
  - Key test failed at upload or on retest.
  - Org/Project invitation received.
  - Approval requested to you (if you are a human approver — note: v1 approvals are agent-only per §15.4, so this is reserved for future).
  - Admin ban reason (visible on next login to the banned user for 1 rendering, then hidden).
- **[R18.03]** Delivery: persisted in `notifications` table + pushed through the user's WebSocket presence channel (`/ws/user/{id}`). Bell badge reads unread count.

---

## 19. Rate Limiting & Abuse

- **[R19.01]** Every HTTP endpoint **requires authentication** (Q57). Two exceptions: `/api/auth/register`, `/api/auth/login`, `/api/auth/request-password-reset`, `/api/auth/verify-email` — all four are rate-limited aggressively.
- **[R19.02]** Global middleware enforces (per-user-id AND per-IP) sliding-window rate limits stored in Redis:
  - Auth endpoints: 10 req / min / IP.
  - Chat send: 60 msg / min / user.
  - File upload: 10 req / min / user.
  - Other endpoints: 300 req / min / user.
- **[R19.03]** WS connections: max 5 concurrent per user; excess connections are refused.
- **[R19.04]** Admin can tune defaults via a `rate_limit_policies` table at runtime.
- **[R19.05]** Banned users/IPs are short-circuited at the earliest middleware layer (403).
- **[R19.06]** Rate-limit responses use **HTTP 429** with headers `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`. Body is RFC 7807 problem+json with `type = https://smap.local/problems/rate-limited`.

---

## 19a. Web Security, Transport, and Trust Boundaries

This chapter consolidates cross-cutting security controls that apply to every HTTP and WS endpoint.

### 19a.1 Transport

- **[R19a.01]** HTTPS only. Nginx terminates TLS.
- **[R19a.02]** TLS configuration: TLS 1.2 minimum, TLS 1.3 preferred; only AEAD cipher suites (`TLS_AES_128_GCM_SHA256`, `TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`, ECDHE+AES-GCM). No 3DES, no CBC, no RC4, no TLS renegotiation, no compression.
- **[R19a.03]** Certificates are operator-managed (Let's Encrypt via certbot, or internal CA). Private keys never committed to Git; placed in the host-side `/etc/smap/tls/` with mode 0400.
- **[R19a.04]** HSTS: `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`. (Operators must confirm they want preload before first production deploy.)

### 19a.2 Response security headers

Emitted by Nginx for all HTML responses and echoed by the backend for all JSON responses where applicable:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'` |
| `Strict-Transport-Security` | see R19a.04 |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=()` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |

- **[R19a.05]** `img-src https:` is intentionally broad because agents produce markdown with public-link images (R13.13). Other directives stay tight to limit XSS damage if sanitization fails.
- **[R19a.06]** The CSP is applied in **report-only** mode during beta behind a `SMAP_CSP_REPORT_ONLY` env flag, with reports posted to `/api/csp-report`. Enforcing mode is the default for production.

### 19a.3 CORS

- **[R19a.07]** Default topology: frontend and API served from the same origin via Nginx. CORS is not needed and the backend sets `Access-Control-Allow-Origin` to the same-origin request's `Origin` with an allowlist of exactly one entry (the configured public origin).
- **[R19a.08]** Cross-origin is explicitly unsupported in v1. Operators who want split origins must configure the allowlist themselves; CSRF risk must be re-evaluated in that case.

### 19a.4 CSRF

- **[R19a.09]** Tokens are carried in `Authorization: Bearer <access_token>` (never in cookies). This makes traditional form-based CSRF inapplicable. Any future cookie-based auth (e.g., for SSR) must introduce double-submit CSRF tokens — out of v1 scope.

### 19a.5 Trusted proxy / client-IP resolution

- **[R19a.10]** The backend trusts `X-Forwarded-For` **only when** the immediate peer's address is in the configured `TRUSTED_PROXIES` CIDR list (defaults: `127.0.0.0/8`, the Docker bridge subnet, and the operator-configured front-proxy subnet). Untrusted peers' `X-Forwarded-For` is ignored; the peer's own address is used as `actor_ip`.
- **[R19a.11]** The trusted-proxy parser takes the **right-most** address in `X-Forwarded-For` that is NOT itself a trusted proxy, so an attacker cannot spoof the left-most entry.

### 19a.6 Abuse / automation

- **[R19a.12]** Registration (`POST /api/auth/register`) requires a CAPTCHA token verified server-side (R6.01). The CAPTCHA provider's public/private keys live in Vault KV (`secret/smap/config/captcha`).
- **[R19a.13]** Admin may set an email-domain allowlist or denylist in runtime config to block disposable-mail signups.

---

## 20. Non-Functional Requirements

- **[R20.01]** Concurrency: sustain 100 heavy users, p95 end-to-end (excluding LLM roundtrip) ≤ 500 ms for API calls, ≤ 200 ms for WS event dispatch.
- **[R20.02]** Horizontal scalability: FastAPI nodes are stateless; session/rate-limit state in Redis; scaling is linear by adding backend replicas behind Nginx.
- **[R20.03]** Resource footprint (reference: single 16-core / 32 GB host): PG 4 GB, Redis 2 GB, Neo4j 4 GB, Qdrant 4 GB, MinIO 4 GB, Vault 1 GB, backend 8 GB, remainder headroom.
- **[R20.04]** Startup: `docker compose up` brings the full stack to ready state in ≤ 3 minutes on a warm host.
- **[R20.05]** i18n: all user-facing strings wrapped in translation helpers; English locale in v1; structural readiness for any number of locales (Q59).
- **[R20.06]** Timezone: all timestamps in UTC at storage; the frontend renders in the user's browser timezone.
- **[R20.07]** Browser support: last 2 stable versions of Chrome, Edge, Firefox, Safari. Mobile: iOS Safari 15+, Chrome Android 110+.
- **[R20.08]** Accessibility: WCAG 2.1 AA target on core flows (login, chat, agent list).
- **[R20.09]** Backup / DR: **out of scope** (Q61). The operator is expected to snapshot Postgres / Neo4j / Qdrant / MinIO volumes externally.
- **[R20.10]** No public API (Q62).

---

## 21. Data Architecture

### 21.1 PostgreSQL schema (summary — abridged types)

All tables use `id uuid primary key default gen_random_uuid()` unless noted. All time columns are `timestamptz default now()`. Soft-delete tables have `deleted_at timestamptz null`. Tables that are mutable via PATCH with `If-Match` include a `version int not null default 1` column incremented on every UPDATE via a trigger.

#### 21.1.0 Entity → Table coverage matrix

Confirms every domain concept defined elsewhere in this document has a persistence location. "Redis" / "Neo4j" / "Qdrant" / "MinIO" / "Vault" entries indicate storage outside PostgreSQL.

| Domain concept | Table / Store | Defined in |
|---|---|---|
| Individual account | `users` | §6 |
| Platform Admin marker | `admins` | §16, §22.13 |
| Auth refresh session | `sessions` | §6.03 |
| Access-token denylist | Redis `jti_denylist:{jti}` | §6.03 |
| Password reset token | `password_reset_tokens` | §6.05 |
| Email verification token | `email_verify_tokens` | §6.02 |
| IP ban list | `ip_bans` | §6.13 |
| Admin view-as session | `admin_impersonation_sessions` | §16.04 |
| Organization | `orgs` | §8 |
| Org membership + Original-Creator flag | `org_members` | §5, §8 |
| Original-Creator transfer request | `original_creator_transfers` | §8.5 |
| Project (Individual- or Org-owned) | `projects` | §5, §8 |
| Project membership | `project_members` | §8.3 |
| Invite (Org or Project) | `invites` | §6.2, §22.2a |
| LLM / embedding / rerank API Key | `api_keys` | §7 |
| Key carried into Project | `key_projects` | §7.3 |
| Key Group | `key_groups` | §7.4 |
| Key Group member (ordered, with rotation + limits) | `key_group_members` | §7.4 |
| Key usage event | `key_usage_events` (+ `key_usage_daily` for > 13 mo) | §7.5 |
| Search-provider key (BYO) | `search_keys` | §12.4 |
| Search result cache | Redis `search:{hash}` | §12.4 R12.13 |
| Agent definition | `agents` | §9 |
| Agent MCP server binding | `agent_mcp_servers` | §12 |
| Agent per-instance runtime row (incl. sub-agent) | `agent_instances` | §15.6 |
| MCP project egress allowlist | `mcp_egress_allowlist` | §12.3 |
| Built-in `file` tool persistent volume | Docker volume `smap-agent-fs-{agent_id}` | §12.3 (R12.03) |
| A2A inbox | Redis Stream `a2a:agent:{agent_id}` | §9.4 |
| RAG config | `rag_configs` | §10.1 |
| RAG source document | `rag_documents` (source bytes in MinIO) | §10.1 |
| RAG chunk (text + vector ptr) | `rag_chunks` + Qdrant `rag_{project_id}` | §10.1 |
| Graph RAG config | `graphrag_configs` | §11 |
| Graph RAG nodes & edges | Neo4j `:Entity` + `:REL` | §11, §21.3 |
| Graph RAG entity embedding | Qdrant `graphrag_{project_id}` | §11.2a |
| Graph RAG build lock | Redis `graphrag:lock:{config_id}` | §11a.01 |
| Workspace | `workspaces` | §13.1 |
| Chat Room | `chatrooms` | §13 |
| Chat Room ↔ Agent binding | `chatroom_agents` | §13.1 |
| Chat Room Guest membership | `chatroom_guests` | §13.3 |
| Chat message | `messages` | §13 |
| Message edit history | `message_edits` | §13.8 |
| Chat attachment | `message_attachments` (bytes in MinIO `chat-uploads`) | §13.4 |
| Workflow definition | `workflows` | §14 |
| Workflow run | `workflow_runs` (+ `workflow_runs_archive` > 90 d) | §14.3 |
| Workflow step trace | `workflow_steps` | §14.3 |
| Approval gate instance | `approvals` | §15.4 |
| Approval vote | `approval_votes` | §15.4 |
| Instruction (with chain / loop detection) | `instructions` | §15.5 |
| Notification | `notifications` | §18 |
| Audit log | `audit_logs` | §17 |
| Rate-limit counter | Redis `ratelimit:{scope}:{key}:{window}` | §19, §21.2 |
| Rate-limit policy config | `rate_limit_policies` | §19.04 |
| tus resumable upload state | tus-library-managed in MinIO `tus-state/` | §22.15 |
| WebSocket presence | Redis `ws:presence:{room_id}`, `ws:user:{user_id}` | §13.7, §21.2 |
| Provider-secret encryption DEK wrap | Vault Transit `smap-provider-secret` | §7.6 |
| Guest-link signing key | Vault Transit `smap-guest-link` | deploy/vault/README |
| Platform service config (SMTP, CAPTCHA, etc.) | Vault KV `secret/smap/config/*` | deploy/vault/README |
| JWT signing key | Vault Transit `smap-jwt-sign` (RS256, with `kid` rotation) | §6.03 |
| Operational logs | stdout (JSON), host-managed | operations §1 |


```sql
-- Identity
users                   (id, email citext, password_hash, email_verified bool,
                         status enum('active','pending','banned','deleted'),
                         banned_reason text null, banned_at, deleted_at,
                         last_login_at, created_at,
                         -- email is unique only among live accounts; soft-deleted
                         -- accounts keep their row for audit but free the address
                         -- for re-registration.
                         UNIQUE (email) WHERE deleted_at IS NULL)
ip_bans                 (cidr cidr, reason text, banned_at)
sessions                (id, user_id, refresh_token_hash, created_at, last_used_at,
                         user_agent, ip_inet)
password_reset_tokens   (id, user_id, token_hash, expires_at, used_at)
email_verify_tokens     (id, user_id, token_hash, expires_at, used_at)

-- Tenancy
orgs                    (id, name citext, creator_user_id FK users,
                         version int not null default 1,
                         created_at, deleted_at,
                         -- Org name is globally unique among live orgs; soft-deleted
                         -- rows keep their name for audit but the name is free for reuse
                         -- after hard-delete.
                         UNIQUE (name) WHERE deleted_at IS NULL)

org_members             (org_id FK, user_id FK, role enum('owner','member'),
                         is_original_creator bool, joined_at,
                         UNIQUE(org_id, user_id),
                         -- exactly one original creator per Org
                         EXCLUDE USING btree (org_id WITH =) WHERE (is_original_creator))

-- Polymorphic ownership split into two nullable FKs with a CHECK so integrity
-- is enforced at the DB level (no orphan projects possible).
projects                (id, owner_user_id FK users NULL, owner_org_id FK orgs NULL,
                         name,
                         version int not null default 1,
                         created_at, deleted_at,
                         CHECK ((owner_user_id IS NOT NULL) <> (owner_org_id IS NOT NULL)),
                         UNIQUE (owner_user_id, name) WHERE deleted_at IS NULL,
                         UNIQUE (owner_org_id,  name) WHERE deleted_at IS NULL)

project_members         (project_id FK, user_id FK, role enum('owner','member'),
                         joined_at,
                         UNIQUE(project_id, user_id))

-- Keys
-- provider enum aligned with §7.1: voyage for embedding only, cohere for rerank only.
-- Capability validation is done at Key Group / RAG-config attach time in application.
api_keys                (id, owner_user_id FK users,
                         provider enum('claude','openai','gemini','voyage','cohere'),
                         name, ciphertext bytea, nonce bytea, dek_wrapped bytea,
                         ciphertext_hmac bytea,
                         masked_preview text, test_status enum('ok','failed','untested'),
                         test_error text null, last_test_at, created_at, deleted_at)
key_projects            (key_id FK, project_id FK, carried bool default true,
                         added_by_user_id FK users, added_at,
                         PRIMARY KEY(key_id, project_id))
key_groups              (id, project_id FK, name, created_at, deleted_at)
key_group_members       (group_id FK, key_id FK, priority int,
                         rotate_on_error_codes int[],
                         rotate_on_token_quota bool,
                         retry_on_error bool,
                         retry_initial_delay_ms int, retry_multiplier numeric,
                         retry_max_delay_ms int, retry_max int, retry_jitter_pct int,
                         max_input_tokens_per_hour bigint null,
                         max_output_tokens_per_hour bigint null,
                         max_requests_per_hour int null,
                         UNIQUE(group_id, key_id),
                         UNIQUE(group_id, priority))

-- Usage
key_usage_events        (id bigserial, key_id FK, agent_id FK null,
                         parent_agent_id FK agents NULL,  -- §15.23 rollup
                         chatroom_id FK null,
                         input_tokens int, output_tokens int, request_ms int,
                         http_status int, error_code text null, at timestamptz)
-- partitioned monthly; older than 13 months aggregated to key_usage_daily

-- Agents
agents                  (id, project_id FK, name, model_hint, key_group_id FK,
                         system_prompt text, prompt_strategy enum('full','lazy'),
                         rag_config_id FK null, graphrag_config_id FK null,
                         context_mode enum('general','compact'),
                         context_token_cap int null,
                         a2a_enabled bool,
                         wakeup_config jsonb, workflow_capabilities jsonb,
                         version int not null default 1,  -- optimistic lock, §E9
                         created_at, deleted_at,
                         UNIQUE (project_id, name) WHERE deleted_at IS NULL)
agent_mcp_servers       (id, agent_id FK, source enum('builtin','url','package'),
                         reference text, allowed_tools text[], config jsonb)

-- Web search (per-project; BYO search key — §12.4)
search_keys              (id, project_id FK, provider enum('brave','serper','tavily','google_cse'),
                         ciphertext bytea, nonce bytea, dek_wrapped bytea,
                         ciphertext_hmac bytea, masked_preview text,
                         test_status enum('ok','failed','untested'),
                         test_error text null, last_test_at,
                         is_active bool,
                         config jsonb,  -- e.g. google_cse.cx, tavily.search_depth
                         created_at, deleted_at,
                         UNIQUE(project_id) WHERE is_active AND deleted_at IS NULL)

-- RAG
rag_configs             (id, project_id FK, name, chunk_strategy enum('fixed','semantic'),
                         chunk_params jsonb,
                         embed_key_id FK api_keys NULL,     -- which key pays for embeddings
                         embed_provider text, embed_model text,
                         rerank_enabled bool,
                         rerank_key_id FK api_keys NULL,    -- which key pays for rerank
                         rerank_provider text null, rerank_model text null,
                         top_k int,
                         created_at, deleted_at,
                         UNIQUE (project_id, name) WHERE deleted_at IS NULL)
rag_documents           (id, rag_config_id FK, filename, mime, size_bytes,
                         minio_path,
                         status enum('ingesting','ready','failed','quarantined'),
                         scan_status enum('pending','clean','quarantined','skipped') default 'pending',
                         scan_at timestamptz null,
                         uploaded_by FK users, uploaded_at)
rag_chunks              (id bigserial, document_id FK, chunk_idx int, text text,
                         qdrant_point_id uuid)

-- Graph RAG
graphrag_configs        (id, project_id FK, agent_id FK UNIQUE,
                         builder_key_group_id FK, trigger_config jsonb,
                         last_build_at,
                         last_build_state enum('idle','running','neo4j_committed',
                                               'qdrant_committed','failed_compensating','failed'),
                         last_build_error text null,
                         -- state machine for the 2-phase commit across Neo4j+Qdrant
                         -- (see §11.2a): idle → running → neo4j_committed →
                         -- qdrant_committed → idle. Failure in step 2 leaves the
                         -- row in failed_compensating; a reconciliation worker
                         -- either rewinds Neo4j or retries Qdrant.
                         created_at, deleted_at)

-- Workspaces and chat
workspaces              (id, project_id FK, name, created_at, deleted_at)
chatrooms               (id, workspace_id FK, name,
                         allow_org_members bool, allow_project_members bool,
                         allow_project_owners_only bool, allow_guest_links bool,
                         -- guest_token: 32 random bytes from a CSPRNG, base64url
                         -- encoded, giving ≈192 bits of entropy (collision prob
                         -- under the entire platform's lifetime is < 2^-128).
                         guest_token text UNIQUE,
                         version int not null default 1,
                         created_at, deleted_at)
chatroom_agents         (chatroom_id FK, agent_id FK, PRIMARY KEY(chatroom_id, agent_id))
chatroom_guests         (chatroom_id FK, user_id FK, joined_via_token text,
                         joined_at, PRIMARY KEY(chatroom_id, user_id))
messages                (id, chatroom_id FK, sender_type enum('user','agent','system'),
                         sender_id uuid, content_md text, content_tsv tsvector,
                         metadata jsonb,   -- {rag_chunks, graphrag_refs, mcp_calls,
                                            --  compact_summary, tool_calls}
                         version int not null default 1,  -- optimistic lock for PATCH /api/messages/{id}
                         created_at, edited_at, deleted_at)

-- Preserves the pre-edit state of a message per R13.21. Retained until the
-- parent message is hard-deleted.
message_edits           (id, message_id FK, old_content_md text,
                         edited_by_user_id FK users, edited_at)

message_attachments     (id, message_id FK, filename, mime, size_bytes,
                         minio_path,
                         status enum('active','quarantined','expired') default 'active',
                         scan_status enum('pending','clean','quarantined','skipped') default 'pending',
                         scan_at timestamptz null,
                         expires_at)

-- Workflows
workflows               (id, workspace_id FK, name, definition jsonb,
                         version int not null default 1,   -- optimistic lock, §E9
                         created_at, deleted_at,
                         UNIQUE (workspace_id, name) WHERE deleted_at IS NULL)
workflow_runs           (id, workflow_id FK, trigger_type text, started_by_user_id FK,
                         state enum('running','waiting','succeeded','failed','cancelled'),
                         started_at, ended_at)
-- Retention: workflow_runs (+ their workflow_steps) older than 90 days are
-- either deleted or moved to workflow_runs_archive by a nightly job. The
-- archive is plain jsonb-summarized rows; no steps are preserved. Admin
-- can adjust the cutoff in rate_limit_policies-style runtime config.
workflow_steps          (id, run_id FK, node_id text,
                         state enum('pending','running','succeeded','failed','skipped','cancelled'),
                         started_at, ended_at,
                         input jsonb, output jsonb, error text null)

-- Approvals and A2A
approvals               (id, workflow_run_id FK, mode, leader_agent_id, timeout_seconds,
                         state enum('pending','approved','rejected','timeout_leader'),
                         started_at, ended_at)
approval_votes          (approval_id FK, voter_agent_id FK, vote bool, rationale text,
                         cast_at, PRIMARY KEY(approval_id, voter_agent_id))
instructions            (id, chain_id uuid, path uuid[], depth int,
                         issuer_agent_id FK, target_agent_id FK, payload jsonb,
                         state enum('issued','delivered','completed','rejected_loop',
                                    'timeout'),
                         issued_at, resolved_at)
agent_instances         (id, agent_id FK, parent_id uuid null, chatroom_id FK,
                         run_context jsonb, spawned_at, destroyed_at)

-- Notifications and audit
notifications           (id, user_id FK, kind text, payload jsonb, read_at, created_at)
audit_logs              (id bigserial, actor_user_id FK null, actor_ip inet,
                         action text, resource_type text, resource_id uuid null,
                         metadata jsonb, session_id uuid null, request_id uuid null,
                         created_at)

-- Platform administrators (§16, §22.13). Append-only bootstrap + runtime
-- add/remove via /api/admin/admins. The last row cannot be deleted (enforced
-- at application layer + a trigger that counts active rows).
admins                  (user_id FK users PRIMARY KEY,
                         promoted_by_user_id FK users null,
                         promoted_at timestamptz,
                         revoked_at timestamptz null)

-- Record of "view-as" sessions (R16.04). Paired with audit events
-- admin.view_as_started / admin.view_as_ended. Used to enforce per-session
-- read-only mode and to stamp every derived action with impersonation context.
admin_impersonation_sessions (id, admin_user_id FK users,
                         target_user_id FK users,
                         started_at, ended_at timestamptz null,
                         started_request_id uuid)

-- Original-Creator transfer requests (§8.5). At most one row per Org with
-- resolved_at IS NULL, enforced by a partial unique index.
original_creator_transfers (id, org_id FK orgs,
                         initiator_user_id FK users,
                         target_user_id   FK users,
                         state enum('pending','accepted','rejected','cancelled','expired','admin_forced'),
                         created_at, resolved_at timestamptz null, expires_at timestamptz,
                         UNIQUE (org_id) WHERE resolved_at IS NULL)

-- Unified invitation record for both Org and Project invites (§22.2a).
-- scope_type distinguishes Org vs Project; scope_id points to the owning row.
-- invitee_user_id is null until the invitee registers+verifies (the email is
-- the stable identity during that window).
invites                 (id, scope_type enum('org','project'),
                         scope_id uuid,
                         role enum('owner','member'),
                         inviter_user_id FK users,
                         invitee_email citext,
                         invitee_user_id FK users null,
                         state enum('pending','accepted','rejected','revoked','expired'),
                         token_hash text,   -- SHA-256 of the one-use link token
                         expires_at,
                         created_at, resolved_at timestamptz null,
                         UNIQUE (scope_type, scope_id, invitee_email) WHERE state = 'pending')

-- Per-project allowlist of outbound hostnames that the MCP Egress Proxy
-- will forward to (R12.04). Empty by default; Project Owners add entries.
mcp_egress_allowlist    (id, project_id FK, hostname text,
                         added_by_user_id FK users, added_at, note text null,
                         UNIQUE (project_id, hostname))

-- Workflow run archive (compacted history beyond the 90-day retention window).
-- No workflow_steps preserved; only a jsonb summary for audit-level inspection.
workflow_runs_archive   (id, workflow_id FK workflows, trigger_type text,
                         started_by_user_id FK users null,
                         state text,
                         started_at, ended_at,
                         summary jsonb,   -- {node_count, failures, total_tokens, ...}
                         archived_at)

rate_limit_policies     (key text primary key, window_sec int, max_count int,
                         scope enum('user','ip','user_and_ip'))
```

Indexes of note:

| Table | Index | Purpose |
|---|---|---|
| `users` | `UNIQUE (email) WHERE deleted_at IS NULL` | live-account uniqueness |
| `orgs`  | `UNIQUE (name) WHERE deleted_at IS NULL` | live-org uniqueness |
| `org_members` | `EXCLUDE (org_id WITH =) WHERE is_original_creator` | at most one Original Creator per Org |
| `projects` | `UNIQUE (owner_user_id, name) WHERE deleted_at IS NULL` + same for `owner_org_id` | live-project uniqueness per owner |
| `project_members` | `(project_id, user_id)` primary-unique + `(user_id)` for "my projects" | membership lookup |
| `api_keys` | `(owner_user_id, provider)` + `(provider)` | per-user listing, test-call fanout |
| `key_group_members` | `UNIQUE (group_id, priority)` | no duplicate priorities |
| `key_usage_events` | `(key_id, at DESC)` + `(agent_id, at DESC)` + monthly partitioning on `at` | usage dashboards + hourly limiter lookup |
| `agents` | `UNIQUE (project_id, name) WHERE deleted_at IS NULL` + `(key_group_id)` | lookup |
| `messages` | `(chatroom_id, created_at DESC) WHERE deleted_at IS NULL` | scroll-back |
| `messages` | `GIN (content_tsv)` | FTS search (R13.18) |
| `message_attachments` | `(expires_at)` for the nightly expiry sweep | TTL enforcement |
| `workflows` | `UNIQUE (workspace_id, name) WHERE deleted_at IS NULL` | lookup |
| `workflow_runs` | `(workflow_id, started_at DESC)`, `(state, started_at)` | list + retention job |
| `workflow_steps` | `(run_id, started_at)` | step trace |
| `instructions` | `(chain_id)` + `(issuer_agent_id, issued_at DESC)` | loop detection + audit |
| `audit_logs` | `(actor_user_id, created_at DESC)`, `(resource_type, resource_id)`, `(created_at)` (retention job) | queries + retention |
| `notifications` | `(user_id, read_at NULLS FIRST, created_at DESC)` | unread-first dashboards |
| `invites` | `UNIQUE (scope_type, scope_id, invitee_email) WHERE state='pending'`, `(invitee_email, state)`, `(token_hash)` | dedup + invite page lookup |
| `original_creator_transfers` | `UNIQUE (org_id) WHERE resolved_at IS NULL`, `(target_user_id, state)` | single pending + inbound list |
| `mcp_egress_allowlist` | `UNIQUE (project_id, hostname)` | dedup |
| `search_keys` | `UNIQUE (project_id) WHERE is_active AND deleted_at IS NULL` | single active per project |
| `sessions` | `(user_id)`, `(last_used_at)` for idle-session cleanup | auth |
| `admins` | `(revoked_at)` partial for `WHERE revoked_at IS NULL` | last-admin safeguard |

### 21.2 Redis layout

| Key pattern | Purpose | TTL |
|---|---|---|
| `session:{refresh_token_hash}` | Active refresh tokens | 30 d rotating |
| `ratelimit:{scope}:{key}:{window}` | Sliding-window counters | window size |
| `keyuse:{key_id}:{minute_bucket}` | Per-minute token/request counters | 61 min |
| `a2a:agent:{agent_id}` | Stream of A2A messages | – (consumed) |
| `ws:presence:{room_id}` | Set of connected user_ids | – |
| `ws:user:{user_id}` | Set of WS connection ids | – |
| `notify:stream:{user_id}` | Push stream for new notifications | – |
| `idempotency:{req_id}` | Dedup of idempotent POSTs | 1 h |

### 21.3 Neo4j

Per-project subgraph labeled with `:P_{project_id}`.

```
(:Entity {project_id, graphrag_config_id, name, kind, confidence})
-[:REL {type, weight, evidence_message_ids, created_at}]->
(:Entity ...)
```

### 21.4 Qdrant

Two collections per project:
- `rag_{project_id}` — RAG document chunks.
- `graphrag_{project_id}` — Graph RAG entity descriptions.

Both with payload filters: `doc_id`, `chunk_idx`, `agent_ids`, `kind`, `project_id`.

### 21.5 MinIO buckets

- `chat-uploads` (lifecycle: 3-day expiration).
- `rag-sources` (kept as long as the RAG document row exists).
- `exports` (lifecycle: 24-hour expiration).

---

## 22. REST API Surface

This section enumerates endpoints. Unless noted, all require a valid JWT and respect §5.2 authorization. Responses are JSON. Errors use RFC 7807 `application/problem+json`. Idempotency is available on POST create endpoints via `Idempotency-Key` header.

### 22.1 Authentication

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create user `{email, password}`. Emails verification link. |
| POST | `/api/auth/verify-email` | Body `{token}`. Transitions user to `active`. |
| POST | `/api/auth/login` | `{email, password}` → `{access_token, refresh_token}`. |
| POST | `/api/auth/refresh` | `{refresh_token}` → rotates, returns new pair. |
| POST | `/api/auth/logout` | Invalidates current refresh token. |
| POST | `/api/auth/request-password-reset` | `{email}` → emails token. |
| POST | `/api/auth/reset-password` | `{token, new_password}`. |
| POST | `/api/auth/change-password` | `{current, new}`. |
| POST | `/api/auth/change-email` | `{new_email, password}`; sends re-verification. |
| GET | `/api/auth/me` | Current user profile. |
| GET | `/api/auth/sessions` | List active refresh tokens. |
| DELETE | `/api/auth/sessions/{id}` | Revoke one. |

### 22.2 Organizations

| Method | Path | Description |
|---|---|---|
| GET | `/api/orgs` | List Orgs the caller belongs to. |
| POST | `/api/orgs` | Create Org; caller becomes Original Creator + Owner; default project auto-created. |
| GET | `/api/orgs/{id}` | Read. |
| PATCH | `/api/orgs/{id}` | Rename. Requires `If-Match: <version>`. |
| DELETE | `/api/orgs/{id}` | Soft-delete (Original Creator or Admin). |
| POST | `/api/orgs/{id}/restore` | Admin only. |
| GET | `/api/orgs/{id}/members` | List members. |
| POST | `/api/orgs/{id}/invites` | `{email}` — emails invitation. |
| DELETE | `/api/orgs/{id}/members/{uid}` | Remove member (Owner). |
| PATCH | `/api/orgs/{id}/members/{uid}` | Change role (except Original Creator). |
| POST | `/api/orgs/{id}/original-creator-transfers` | §8.5 — initiate transfer `{target_user_id}`. |
| POST | `/api/orgs/{id}/original-creator-transfers/{tid}/accept` | Target accepts. |
| DELETE | `/api/orgs/{id}/original-creator-transfers/{tid}` | Initiator cancels, or Admin cancels. |
| GET | `/api/orgs/{id}/original-creator-transfers` | List pending (max one). |

### 22.2a Invitations (inbound view)

| Method | Path | Description |
|---|---|---|
| GET | `/api/invites?state=pending\|accepted\|rejected` | List invites addressed to the caller (both Org and Project). |
| POST | `/api/invites/{id}/accept` | Accept. |
| POST | `/api/invites/{id}/reject` | Reject. |

### 22.3 Projects

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects?scope=user|org&id=…` | List projects in scope. |
| POST | `/api/projects` | `{owner_type, owner_id, name}`. |
| GET | `/api/projects/{id}` | Read. |
| PATCH | `/api/projects/{id}` | Rename. |
| DELETE | `/api/projects/{id}` | Soft-delete. |
| POST | `/api/projects/{id}/restore` | Admin only. |
| GET | `/api/projects/{id}/members` | List. |
| POST | `/api/projects/{id}/invites` | `{email}`. |
| DELETE | `/api/projects/{id}/members/{uid}` | Remove. |
| PATCH | `/api/projects/{id}/members/{uid}` | Change role. |

### 22.4 Keys

| Method | Path | Description |
|---|---|---|
| GET | `/api/keys` | List caller's keys (no secrets). |
| POST | `/api/keys` | `{provider, name, secret}`; runs test call. |
| POST | `/api/keys/{id}/retest` | Re-runs test call. |
| DELETE | `/api/keys/{id}` | Delete. |
| GET | `/api/projects/{pid}/keys` | List keys carried into project. |
| POST | `/api/projects/{pid}/keys` | Carry a key into project `{key_id}`. |
| DELETE | `/api/projects/{pid}/keys/{key_id}` | Withdraw. |
| GET | `/api/projects/{pid}/keys/{key_id}/usage?window=1h|24h|7d|30d` | Usage. |

### 22.5 Key Groups

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/{pid}/key-groups` | List. |
| GET | `/api/key-groups/{id}` | Read a group including member keys (masked), priority order, per-key limits, and rotation settings. |
| POST | `/api/projects/{pid}/key-groups` | Create. |
| PATCH | `/api/key-groups/{id}` | Edit rotation/limit settings at the group level. |
| DELETE | `/api/key-groups/{id}` | Delete. |
| POST | `/api/key-groups/{id}/keys` | `{key_id, priority, limits, rotation}`. |
| PATCH | `/api/key-groups/{id}/keys/{key_id}` | Change priority/limits. |
| DELETE | `/api/key-groups/{id}/keys/{key_id}` | Remove from group. |
| POST | `/api/key-groups/{id}/reorder` | `{priorities:{key_id:int}}` — bulk re-number priorities atomically. |

### 22.6 Agents

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/{pid}/agents` | List. |
| POST | `/api/projects/{pid}/agents` | Create. |
| GET | `/api/agents/{id}` | Read. |
| PATCH | `/api/agents/{id}` | Edit. |
| DELETE | `/api/agents/{id}` | Soft-delete. |

### 22.7 RAG

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/{pid}/rag-configs` | List. |
| POST | `/api/projects/{pid}/rag-configs` | Create. |
| PATCH | `/api/rag-configs/{id}` | Edit. |
| DELETE | `/api/rag-configs/{id}` | Delete (cascades chunks). |
| POST | `/api/rag-configs/{id}/documents` (multipart) | Upload doc. |
| GET | `/api/rag-configs/{id}/documents` | List. |
| DELETE | `/api/rag-documents/{id}` | Delete one doc. |

### 22.8 Graph RAG

| Method | Path | Description |
|---|---|---|
| POST | `/api/agents/{id}/graphrag` | Create config. |
| PATCH | `/api/graphrag/{id}` | Edit trigger, key-group. |
| POST | `/api/graphrag/{id}/build` | Manually trigger build. |
| GET | `/api/graphrag/{id}/status` | Last build status, size. |
| DELETE | `/api/graphrag/{id}` | Delete (cascades neo4j subgraph). |

### 22.9 MCP

| Method | Path | Description |
|---|---|---|
| GET | `/api/agents/{id}/mcp` | List attached servers. |
| POST | `/api/agents/{id}/mcp` | Attach `{source, reference, allowed_tools}`. |
| DELETE | `/api/agents/{id}/mcp/{mcp_id}` | Detach. |
| POST | `/api/agents/{id}/mcp/{mcp_id}/test` | For `url`- and `package`-source MCP servers: performs a handshake (`initialize` + `tools/list` per MCP protocol) in a short-lived sandbox and returns `{ok, tool_names[], duration_ms, error?}`. No state is retained. |
| GET | `/api/projects/{pid}/mcp/egress-allowlist` | Read. |
| PUT | `/api/projects/{pid}/mcp/egress-allowlist` | Set. |

### 22.9a Web Search keys (BYO, §12.4)

| Method | Path | Description |
|---|---|---|
| GET    | `/api/projects/{pid}/search-keys` | List (masked; never returns secret). |
| POST   | `/api/projects/{pid}/search-keys` | Upload `{provider, secret, config}`; runs live test. |
| POST   | `/api/projects/{pid}/search-keys/{id}/retest` | Re-runs test call. |
| POST   | `/api/projects/{pid}/search-keys/{id}/activate` | Mark as the active provider (atomically deactivates others). |
| DELETE | `/api/projects/{pid}/search-keys/{id}` | Delete. |

### 22.10 Workspaces & Chat Rooms

| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/{pid}/workspaces` | List. |
| POST | `/api/projects/{pid}/workspaces` | Create (one default chat room auto-created). |
| DELETE | `/api/workspaces/{id}` | Soft-delete. |
| GET | `/api/workspaces/{id}/chatrooms` | List. |
| POST | `/api/workspaces/{id}/chatrooms` | Create. |
| PATCH | `/api/chatrooms/{id}` | Change name, access flags. |
| DELETE | `/api/chatrooms/{id}` | Soft-delete. |
| GET | `/api/chatrooms/{id}/messages?before=<id>&limit=` | Pagination (scroll-back). |
| GET | `/api/chatrooms/{id}/messages?since=<id>` | Delta (WS reconnect). |
| GET | `/api/messages/{id}` | Read a single message (permalink / quote). Scope enforced. |
| POST | `/api/chatrooms/{id}/messages` | Send user message `{content_md, attachment_ids}`. |
| PATCH | `/api/messages/{id}` | Edit (per §13.8 rules). Requires `If-Match: <version>`. |
| DELETE | `/api/messages/{id}` | Delete (self or owner). |
| GET | `/api/chatrooms/{id}/search?q=` | FTS search. |
| POST | `/api/chatrooms/{id}/export` | Queue export; returns job id. |
| GET | `/api/exports/{job_id}` | Download link (MinIO signed URL, 24 h). |
| POST | `/api/chatrooms/{id}/attachments` (multipart) | Upload attachment ≤ 32 MB (single-shot). For larger files use the tus endpoint (§22.15). |
| GET | `/api/attachments/{id}` | Read metadata + short-lived signed download URL. |
| GET | `/api/chatrooms/{id}/agents` | List agents bound to this room. |
| POST | `/api/chatrooms/{id}/agents` | `{agent_id}` — bind. |
| DELETE | `/api/chatrooms/{id}/agents/{agent_id}` | Unbind. |
| GET | `/api/chatrooms/{id}/guest-link` | Get permanent guest URL. |

### 22.11 Workflows

| Method | Path | Description |
|---|---|---|
| GET | `/api/workspaces/{id}/workflows` | List. |
| POST | `/api/workspaces/{id}/workflows/validate` | Body: `{definition}`. Runs JSON-Schema + all 14 semantic linter rules (docs/workflow.schema.md §5). Returns `{valid, errors:[], warnings:[]}` without persisting. |
| POST | `/api/workspaces/{id}/workflows` | Create with definition JSON. Runs the same validation server-side. |
| PATCH | `/api/workflows/{id}` | Edit. Requires `If-Match: <version>`. |
| DELETE | `/api/workflows/{id}` | Soft-delete. |
| POST | `/api/workflows/{id}/runs` | Trigger a run. |
| GET | `/api/workflows/{id}/runs` | List runs. |
| GET | `/api/workflow-runs/{id}` | Read with step trace. |
| POST | `/api/workflow-runs/{id}/cancel` | Cancel. |

### 22.12 Notifications

| Method | Path | Description |
|---|---|---|
| GET | `/api/notifications` | List (paginated). |
| POST | `/api/notifications/read` | `{ids:[…]}` → mark read. |

### 22.13 Admin

All under `/api/admin/*`. Role = Admin required.

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users?q=&status=` | Search/list. |
| GET | `/api/admin/users/{id}` | Full detail including orgs, projects, keys (masked), recent activity. |
| POST | `/api/admin/users/{id}/ban` | `{reason}`. |
| POST | `/api/admin/users/{id}/unban` | Lift ban. |
| POST | `/api/admin/users/{id}/delete` | Soft-delete. |
| POST | `/api/admin/users/{id}/hard-delete` | After 60-day grace. Irreversible. |
| POST | `/api/admin/users/{id}/impersonate` | Starts read-only view-as session. |
| POST | `/api/admin/users/{id}/end-impersonate` | Ends view-as. |
| GET | `/api/admin/ip-bans` | List IP bans. |
| POST | `/api/admin/ip-bans` | Add `{cidr, reason}`. |
| DELETE | `/api/admin/ip-bans/{id}` | Remove. |
| GET | `/api/admin/admins` | List platform admins. |
| POST | `/api/admin/admins` | Promote `{user_id}` to Admin. Audit-logged; requires confirmation. |
| DELETE | `/api/admin/admins/{user_id}` | Demote. The last Admin cannot be demoted (404 + `{error:"last_admin"}`). |
| GET | `/api/admin/orgs` | List all Orgs. |
| POST | `/api/admin/orgs/{id}/force-delete` | Bypass Original-Creator requirement. |
| GET | `/api/admin/projects` | List all Projects. |
| GET | `/api/admin/audit?filters…` | Search audit logs. |
| POST | `/api/admin/restore/{type}/{id}` | Restore soft-deleted within 60 d. |
| GET | `/api/admin/metrics` | System health + usage aggregate. |
| PATCH | `/api/admin/rate-limits/{key}` | Tune rate limits at runtime. |
| POST | `/api/admin/graphrag/{id}/reset` | Force `last_build_state = 'idle'` (§11a.02). |

### 22.14 WebSocket endpoints

| Path | Events pushed |
|---|---|
| `/ws/user/{user_id}` | notifications, ban-kick, `rag.document.status_changed`, `rag.document.failed` (scoped to caller's projects) |
| `/ws/chatroom/{id}` | see §13.7 [R13.19] |
| `/ws/workflow-runs/{id}` | step transitions, approval prompts |
| `/ws/rag-configs/{id}` | `document.ingesting / ready / failed`, per-document progress (percent) for configs the caller may read |
| `/ws/admin/tail` | Admin-only live audit tail |

All WS connections are authenticated via the access token passed in the `Sec-WebSocket-Protocol` subprotocol (format: `bearer.<access_token>`; clients send `Sec-WebSocket-Protocol: bearer.<token>` and the server echoes the protocol in the handshake response). Reauth upon token expiry via a `refresh` message on the same socket (JSON `{"type":"refresh","access_token":"..."}`).

### 22.15 Resumable large-file upload (tus, §D10)

All file uploads larger than the 32 MB single-shot cap (R22.10 attachments, R10.02 RAG ingest) MUST go through the **tus v1.0.0** resumable-upload protocol mounted at `/api/tus`. tus is an IETF-track open standard implemented by a vetted Python library (`tuspy-server` or similar in a pinned version).

- **[R22.15.01]** Endpoint: `/api/tus` (POST for creation; HEAD/PATCH for resume).
- **[R22.15.02]** Required headers per tus spec: `Tus-Resumable: 1.0.0`, `Upload-Length`, `Upload-Offset`, `Upload-Metadata`.
- **[R22.15.03]** `Upload-Metadata` MUST include:
  - `purpose` one of `chat_attachment`, `rag_source`.
  - `project_id` (UUID of target project).
  - `chatroom_id` (UUID) when `purpose = chat_attachment`.
  - `rag_config_id` (UUID) when `purpose = rag_source`.
  - `filename`, `mime`.
- **[R22.15.04]** Server-side enforcement:
  - Authentication: Bearer JWT in `Authorization` header on the Creation POST.
  - Authorization: caller must have upload permission for the declared project/chatroom/rag-config.
  - Max total size: 1 GB. Larger Creation requests return `413`.
  - Chunk size cap: 16 MB per PATCH.
  - Expiration: an incomplete upload is abandoned after 24 h; the temporary storage is deleted.
- **[R22.15.05]** On completion the server returns:
  - `Location: /api/tus/{upload_id}` (the tus resource URL).
  - A custom header `X-SMAP-Resource: /api/{chat|rag}/.../{new_id}` pointing to the materialized resource (attachment or rag document) the client can then reference in subsequent calls.
- **[R22.15.06]** The Creation POST itself is rate-limited (§19) at 20 / min / user, separate from the per-chunk PATCH calls which are 300 / min / user.
- **[R22.15.07]** Virus scanning hook: after completion, the backend dispatches a `file.scan_requested` worker task. Operators may configure ClamAV or equivalent; if not configured, a no-op passes the file. Files that fail the scan are quarantined, the parent resource is flagged `status = 'quarantined'`, and an audit event `attachment.quarantined` / `rag.document.quarantined` is written.

---

## 23. Bounded Contexts (DDD layout)

Each bounded context is a Python package with the same internal structure:

```
backend/
  app/
    api/            # FastAPI routers (thin)
    config/
    main.py
  contexts/
    identity/
      domain/       # entities, value objects, domain events
      application/  # use-cases, services, DTOs
      infrastructure/ # SQLAlchemy repos, vault client, etc.
      interfaces/   # REST routers, WS handlers
    tenancy/         …same layout…
    keys/
    agents/
    knowledge/       # RAG + GraphRAG
    conversation/    # workspaces, chat rooms, messages
    workflow/
    audit/
    notification/
  shared_kernel/
    auth/           # JWT, password hashing, decorators
    db/             # base classes, unit-of-work
    events/         # event-bus abstractions
    errors/
    i18n/
tests/
  unit/
  integration/
  e2e/
```

- **[R23.01]** No cross-context SQL joins. Cross-context reads go through the target context's application service.
- **[R23.02]** Domain events are published via the shared event bus; handlers live inside the consuming context.
- **[R23.03]** Every context exposes an `application` facade; `api/` routers call only these facades, never repositories directly.

---

## 24. Frontend Architecture

The frontend is an opinionated, strictly layered Vue 3 + TypeScript SPA. **Separation of Concerns is a first-class acceptance criterion**, enforced by folder structure, dependency-direction lint rules, and CI.

### 24.1 Layering model

Five layers, strictly stacked. Higher layers may depend on lower; never the reverse.

```
┌──────────────────────────────────────────────────────────────┐
│ L5  Views          routed pages, one file per route          │
├──────────────────────────────────────────────────────────────┤
│ L4  Composables    reactive business logic (useXxx functions)│
├──────────────────────────────────────────────────────────────┤
│ L3  Stores         Pinia (UI / session state) +              │
│                    TanStack Query cache (server state)       │
├──────────────────────────────────────────────────────────────┤
│ L2  API / WS       typed endpoint functions + ws channels    │
├──────────────────────────────────────────────────────────────┤
│ L1  Types / Schemas pure TS types + Zod runtime validators   │
└──────────────────────────────────────────────────────────────┘
                     ▲                                   ▲
                     │                                   │
          ┌──────────┴───────────┐          ┌────────────┴───────────┐
          │ Presentational       │          │ Transport              │
          │ components (L0)      │          │ (axios instance,       │
          │ props-in/events-out  │          │  ws client, interceptors)│
          └──────────────────────┘          └────────────────────────┘
```

- **[R24.01]** **L0 presentational components** (aka "dumb components") take props, emit events, have zero imports from L3/L4. They are portable to Storybook without mocks.
- **[R24.02]** **L2 API/WS** is the ONLY layer that may import `axios`, `fetch`, or the native `WebSocket` constructor. Components, stores, and composables never touch transport directly.
- **[R24.03]** **Transport** (the axios instance and WS client singletons) lives in `shared/transport/` and is imported only by L2.
- **[R24.04]** Views are thin: parse route params, call composables, pass props to components. No inline business logic.

### 24.2 Feature-slice layout (mirrors backend bounded contexts)

```
frontend/src/
├── app/                     # Root: bootstrap, router, global providers
│   ├── main.ts
│   ├── router.ts            # composes per-slice routes (24.6)
│   ├── plugins/             # i18n, pinia, query-client, error-handler
│   └── layouts/             # AppShell, AuthShell, AdminShell
│
├── slices/
│   ├── identity/            # auth, profile, sessions
│   ├── tenancy/             # orgs, projects, members, invites, OC transfers
│   ├── keys/                # api_keys, key_groups, search_keys
│   ├── agents/              # agents, RAG, GraphRAG, MCP bindings
│   ├── conversation/        # workspaces, chatrooms, messages, attachments
│   ├── workflow/            # workflow editor + runs viewer
│   └── admin/               # admin console
│
└── shared/
    ├── types/               # cross-slice domain types
    ├── transport/           # axios instance, WsManager, interceptors
    ├── api-client/          # OpenAPI-generated client (low level)
    ├── ui/                  # design-system components (Button, Modal, Drawer)
    ├── styles/              # tokens.css, reset.css, theme.css
    ├── i18n/                # loader, Intl helpers, pluralization
    ├── errors/              # typed error classes
    ├── composables/         # cross-slice (useBreakpoint, useClipboard)
    └── directives/          # v-focus, v-resize-observer
```

Every **slice** has the same internal shape:

```
slices/<name>/
├── api/          # L2: slice-specific endpoint wrappers + WS channel handlers
├── types/        # L1: TS types + Zod schemas for this slice
├── stores/       # L3: Pinia store(s) for slice-local UI state
├── queries/      # L3: TanStack Query keys + fetchers + mutations
├── composables/  # L4: useXxx functions built on queries + stores
├── components/   # L0: presentational, export as named
├── views/        # L5: routed pages; one per route
├── routes.ts     # route table contributed to the root router
├── locales/      # en.json (+ later jp.json, zh-TW.json, …)
├── __tests__/    # co-located tests
└── index.ts      # PUBLIC BARREL — the only entry for cross-slice imports
```

- **[R24.05]** **Barrel rule.** A slice's `index.ts` exports only what other slices may consume: route table, public composables, public types. Everything else (components, stores, queries, internal types) is internal. ESLint's `no-restricted-imports` enforces this.
- **[R24.06]** **Cross-slice dependency direction.** Allowed: `conversation → agents → keys → tenancy → identity → shared`. Disallowed: any reverse direction. Codified in `.eslintrc` with `boundaries` plugin; violations fail CI.
- **[R24.07]** `shared/` has **no** imports from any slice. One-way dependency by construction.

### 24.3 State management

Two complementary stores, each for distinct data classes:

| Concern | Tool | Examples |
|---|---|---|
| **Server state** (data owned by backend; cachable; invalidatable) | **TanStack Query for Vue** (`@tanstack/vue-query`) | Agent list, chat history, key usage |
| **Client state** (UI, session, transient) | **Pinia** | Sidebar collapsed?, selected tab, draft message, JWT access token |

- **[R24.08]** TanStack Query owns every remote resource's cache. Query keys follow the pattern `[slice, resource, ...params]`, e.g. `['agents','list', projectId]`.
- **[R24.09]** Mutations use `useMutation`; on success they `queryClient.invalidateQueries` the relevant keys. WS events call the same `invalidateQueries` (or apply optimistic patches via `setQueryData`) — see §24.7.
- **[R24.10]** Pinia stores NEVER cache server data. They hold session tokens, CSRF nonce (future), UI preferences, the view-as impersonation flag, and in-progress multi-step form drafts.
- **[R24.11]** The JWT access token lives in a Pinia store kept **only in memory**. On page reload, the refresh token (kept as an `httpOnly`-like path via a secure refresh endpoint protected by SameSite=strict if cookies are ever added; in v1 refresh token lives in memory + a single `sessionStorage` key cleared on logout) is exchanged for a new access token.

### 24.4 API / WS transport layer

```
shared/transport/
├── axios.ts         # configured instance + interceptors
├── ws-manager.ts    # single connection pool, reconnect, auth refresh
├── problem-json.ts  # RFC 7807 parser → typed errors
└── idempotency.ts   # UUID v4 header for POST creates
```

- **[R24.12]** **Axios interceptors** (in order):
  1. Inject `Authorization: Bearer <access_token>` from identity store.
  2. Inject `Idempotency-Key` on POST when caller opts in.
  3. Inject `Accept-Language` from i18n locale.
  4. On 401 with `type=auth/token-expired`: pause the request, silently refresh, replay once. If refresh fails, flush queue with `AuthError` and navigate to login.
  5. On 429: parse `Retry-After`, surface as `RateLimitError` with the value.
  6. Any non-2xx with `application/problem+json`: parse to a typed subclass of `ApiError` (see §24.11).
- **[R24.13]** The OpenAPI-generated client (`shared/api-client/`) is the low-level layer: one function per endpoint, fully typed from the backend schema. **Slice `api/` folders wrap these** into use-case-shaped calls (e.g., `fetchAgentList(projectId)`) that add domain semantics.
- **[R24.14]** **WsManager** is a singleton. Slice composables acquire channels through `wsManager.channel('/ws/chatroom/abc')` which returns a typed `Channel` object exposing `subscribe(eventName, handler)` and `send(payload)`. Reconnect, backoff, auth refresh, and message buffering during reconnect are centralized. Components never import `WebSocket`.

### 24.5 Component taxonomy

Three tiers, clearly named:

| Tier | Folder | Rules |
|---|---|---|
| **Atoms / design-system** | `shared/ui/` | Zero domain knowledge. Only props/events. `<BaseButton>`, `<BaseInput>`, `<BaseModal>`. |
| **Presentational (slice-local)** | `slices/<n>/components/` | Slice types in props allowed; no stores, no router, no API calls. Tested in isolation. |
| **Container** | `slices/<n>/views/` (and occasionally `components/*Container.vue`) | May import composables, router, stores. Keep template thin. |

- **[R24.15]** A presentational component with a `*Container.vue` sibling is preferred over a single smart component, wherever the view is used more than once.
- **[R24.16]** Naming convention: `BaseFoo.vue` (atom), `Foo.vue` (presentational), `FooView.vue` or `FooContainer.vue` (container). Storybook stories live next to presentational components in `Foo.stories.ts`.

### 24.6 Routing, guards, and authorization

- **[R24.17]** Each slice exports `routes.ts` with an array of `RouteRecordRaw`. `app/router.ts` composes them and adds a single not-found catch-all.
- **[R24.18]** Route `meta` declares auth requirements:
  ```ts
  meta: { requiresAuth: true, requiresVerifiedEmail: true, requiredRoles: ['ProjectOwner'] }
  ```
- **[R24.19]** Guards are **pure functions** tested independently, composed in `app/plugins/guards.ts`:
  1. `authGuard` — redirects unauthenticated users to `/login?next=`.
  2. `verifiedEmailGuard` — blocks unverified accounts from protected routes.
  3. `roleGuard` — reads `requiredRoles` and the resource-scoped role from the caller's cached membership; denies with a typed error (no 404 spoof).
  4. `banKickGuard` — listens for `ban-kick` WS events and immediately redirects.
- **[R24.20]** Authorization on the frontend is **advisory** (per backend R5.05): every render-time permission check is paired with a server-side check. The frontend uses a `<PermissionGate :cap="'create_agent'" :scope="projectId">` component that short-circuits to a disabled or hidden state when the cached role cannot satisfy.

### 24.7 Real-time integration

- **[R24.21]** A slice subscribing to a WS channel exposes a composable, e.g. `useChatroomSocket(chatroomId)`, returning reactive refs updated from server events. Internally, the composable:
  1. Acquires the channel from `WsManager`.
  2. Maps each event type to `queryClient.setQueryData` (patch) or `invalidateQueries` (refetch).
  3. Emits presence events into a slice Pinia store.
  4. Cleans up on `onScopeDispose`.
- **[R24.22]** **No component subscribes to WS directly.** Components read from query cache / Pinia; the composable is the sole WS-aware layer in each slice.
- **[R24.23]** On reconnect, composables replay a delta fetch (e.g. `GET /api/chatrooms/{id}/messages?since=`) before resuming event application, to avoid gaps.

### 24.8 Forms and validation

- **[R24.24]** Every form uses **vee-validate** with **Zod** as the schema resolver. Schemas live in `slices/<n>/types/` and are reused for API request validation (client-side pre-flight).
- **[R24.25]** Backend RFC 7807 errors with `detail.field_errors: [{path, message}]` are piped to vee-validate's `setErrors()` so server-side validation appears as inline form errors without ad-hoc plumbing.
- **[R24.26]** A `<FormField>` wrapper component handles label, error message, help text, and ARIA wiring — components never hand-roll this markup.

### 24.9 Styling and design tokens

- **[R24.27]** Base library: **Element Plus** (chosen over Naive UI for more mature mobile support and larger ecosystem). Imported on-demand via `unplugin-auto-import` to keep bundle size small.
- **[R24.28]** Design tokens live in `shared/styles/tokens.css` as CSS custom properties (`--color-bg-surface`, `--color-accent`, `--radius-md`, …) and are themed light/dark by toggling a `data-theme` attribute on `<html>`.
- **[R24.29]** No Tailwind in v1 — to avoid collision with Element Plus utility classes. Layouts use CSS Grid / Flex via scoped `<style>` blocks.
- **[R24.30]** **Scoped styles only.** Global CSS is restricted to `tokens.css`, `reset.css`, and Element Plus overrides. A lint rule bans `<style>` (non-scoped) in any `.vue` file outside `shared/styles/`.

### 24.10 Responsive and mobile

- **[R24.31]** Breakpoints: 480, 768, 1024, 1280 px. Exposed as CSS variables and a `useBreakpoint()` composable so layout-sensitive components can react in JS too.
- **[R24.32]** Chat: single-pane at < 768 px; side panels (agent list, attachments) become a drawer.
- **[R24.33]** Workflow editor: < 1024 px shows a read-only canvas with a message to "switch to desktop to edit". Editing on mobile is out of scope for v1.
- **[R24.34]** Touch targets are ≥ 44×44 px; tested via the dev's responsive toolbar + a Playwright mobile-viewport smoke suite.

### 24.11 Errors and telemetry

- **[R24.35]** Typed error classes in `shared/errors/`:
  - `ApiError` (base) with `type`, `title`, `status`, `detail`, `instance`.
  - `AuthError`, `PermissionError`, `ValidationError`, `RateLimitError`, `NetworkError` — each a subclass.
- **[R24.36]** A global Vue `errorCaptured` handler routes:
  - `AuthError` → navigate to `/login`.
  - `PermissionError` → show a toast + stay on page.
  - `ValidationError` → already mapped by vee-validate; leave to form UI.
  - `NetworkError` / 5xx / unknown → banner "Reconnecting… retry in Ns" with exponential backoff.
- **[R24.37]** `console.error` is redirected (in production) to a lightweight error beacon: POST to `/api/csp-report` for CSP violations and `/api/frontend-errors` (future) for uncaught JS. The latter endpoint is out of v1 scope; v1 just `console.error`s.
- **[R24.38]** A toast service (`useToast()`) is the one approved channel for user-visible transient messages. Components never use `alert()` or write ad-hoc banners.

### 24.12 Testing strategy

| Layer | Tool | Target coverage |
|---|---|---|
| Pure units (composables, utils, schemas) | **Vitest** | ≥ 90 % |
| Components (presentational) | **Vitest + @vue/test-utils + Storybook play functions** | ≥ 80 % |
| Integration (views + mocked API) | **Vitest + MSW** (mock service worker) | critical paths |
| E2E | **Playwright** | golden-path flows per slice |
| Visual regression | **Playwright screenshots** on Storybook stories | design-system only in v1 |

- **[R24.39]** **No container component ships without at least one MSW-mocked integration test** covering its success + one failure path.
- **[R24.40]** E2E runs against `docker compose -f compose.test.yml` which stands up the full stack with a seeded fixture project.

### 24.13 Frontend security

- **[R24.41]** Markdown → HTML pipeline in one file: `slices/conversation/lib/renderMarkdown.ts`. Uses `markdown-it` → **DOMPurify** (complementing backend bleach per R13.14). Outputs a `v-html`-compatible string. No other file in the repo calls `v-html`.
- **[R24.42]** KaTeX, Mermaid, and syntax-highlighting run **after** DOMPurify, using DOM-mutation APIs that cannot re-introduce script nodes.
- **[R24.43]** Guest-link URLs contain the token in the path. The frontend strips tokens from Router history URLs after consumption (replace-state to `/c/<chatroom_id>`) so browsers don't leak tokens via `Referer` or history.
- **[R24.44]** CSRF: N/A in v1 (token in `Authorization` header, not cookies). If cookies are added later, double-submit CSRF is mandatory; a placeholder composable `useCsrfToken()` is reserved.

### 24.14 Build and bundling

- **[R24.45]** **Vite 5** with rollup output. One bundle per slice-entry-view lazy-loaded via dynamic `import()`; `@vue-flow/core` and `mermaid` and `katex` are isolated into their own chunks.
- **[R24.46]** Locale bundles lazy-loaded on demand; `en.json` is the only bundle-at-startup locale.
- **[R24.47]** Source maps emitted in production builds but served only to authenticated Admins via a private `/admin/sourcemaps/` path (or not served; the map file is kept for post-mortem).
- **[R24.48]** Bundle budget: initial page (login + root shell) ≤ 250 KB gzipped; per-view lazy chunk ≤ 200 KB. Violations fail CI (`bundlesize`).

### 24.15 SoC enforcement (CI gates)

The rules above are not aspirational. Each is enforced by an automated gate that fails PRs:

| # | Gate | Tool |
|---|---|---|
| 1 | Layer direction (views→composables→stores→api→transport, no reverse) | ESLint `eslint-plugin-boundaries` |
| 2 | Slice isolation (import only via `index.ts`) | ESLint `no-restricted-imports` rule auto-generated per slice |
| 3 | Transport isolation (only `shared/transport/*` and `slices/*/api/*` may import axios) | ESLint custom rule |
| 4 | `v-html` is allowed only in `renderMarkdown.ts` | ESLint `vue/no-v-html` + allowlist |
| 5 | No `alert` / `confirm` / `prompt` | ESLint `no-alert` |
| 6 | No global CSS outside `shared/styles/` | Custom checker script |
| 7 | Store must not import from `api/` of another slice | ESLint boundaries |
| 8 | Every view has at least one integration test | Vitest file-glob check |
| 9 | Bundle budget | `bundlesize` |
| 10 | Type coverage ≥ 95 % (no `any`) | `type-coverage` |
| 11 | Accessibility: no `role="button"` on non-buttons, labels on inputs | `eslint-plugin-vuejs-accessibility` |
| 12 | i18n coverage: no bare string literals in `.vue` template | Custom ESLint rule that ignores `shared/ui/` atoms |

A contributor who needs to bypass any gate files a documented exception in `docs/frontend-exceptions.md` with rationale — gates themselves cannot be disabled silently.

### 24.16 Vendor choices summary

| Concern | Pick | Alternative considered |
|---|---|---|
| Framework | Vue 3 + TypeScript | — (stakeholder) |
| Build | Vite 5 | — |
| State (server) | TanStack Query (`@tanstack/vue-query`) | Pinia alone (rejected: manual invalidation burden) |
| State (client) | Pinia | Vuex (legacy) |
| Routing | vue-router 4 | — |
| UI library | Element Plus | Naive UI (viable second choice) |
| Forms | vee-validate + Zod | Formkit (heavier) |
| Markdown | markdown-it + DOMPurify | marked (less plugin surface) |
| Diagrams | Mermaid | — |
| Math | KaTeX | MathJax (larger bundle) |
| Canvas (workflow editor) | `@vue-flow/core` | react-flow (wrong framework) |
| i18n | vue-i18n 9 | — |
| Testing | Vitest + Playwright + MSW + Storybook | Jest (rejected: Vite alignment) |
| API codegen | openapi-typescript-codegen | orval (heavier) |

---

## 25. Deployment (Docker Compose topology)

Services (all in one `docker-compose.yml`):

- `nginx` (TLS terminator, reverse proxy, static)
- `backend-web` ×N (FastAPI ASGI, Uvicorn, gunicorn supervisor)
- `backend-worker` ×M (Arq workers for RAG ingest, GraphRAG build, exports, housekeeping)
- `backend-ws` (can share with `backend-web` if running single-process; otherwise sticky-session WS node)
- `frontend` (served static by Nginx)
- `postgres` (with `pgvector` extension even if unused, for future flexibility; `pg_cron` extension for scheduled jobs)
- `redis`
- `qdrant`
- `neo4j`
- `minio`
- `vault`
- `egress-proxy` (custom FastAPI service on its own network; see §12.3)
- `mcp-sandbox-supervisor` (runs inside Docker-in-Docker or uses the host's Docker socket through a `docker-sock-proxy` with a restricted set of endpoints)

Networks:
- `smap_frontend_net`: nginx ↔ backend-web
- `smap_backend_net`: backend ↔ postgres/redis/qdrant/neo4j/minio/vault
- `smap_egress_net`: sandbox containers ↔ egress-proxy only

---

## 26. Open items deliberately left to implementation

These are points the current spec intentionally leaves to the implementation team, flagged here so they can be caught in design review:

1. ~~Exact JSON schema for the workflow `definition_json`.~~ **Done** — see `docs/workflow.schema.json` + `docs/workflow.schema.md`.
2. ~~Concrete Vault policy files.~~ **Done** — see `deploy/vault/policies/smap-backend.hcl`, `deploy/vault/policies/smap-rotation.hcl`, and `deploy/vault/README.md`.
3. gVisor installation playbook and fallback to Kata on hosts without gVisor support.
4. i18n copy deck (English baseline; content-design pass).
5. Which of Brave / Serper / Tavily / Google CSE to implement first as the `web_search` reference adapter (all four ship behind the same protocol; only the rollout order is open). **Stakeholder selection: Tavily (recommended); confirmation pending.**
6. OpenAPI contract generation and publishing pipeline (`uvicorn` + `fastapi --generate-openapi`).

---

## 27. Traceability

Every requirement `[Rxx.yy]` corresponds to a Q&A decision or a design recommendation. The mapping is maintained in `docs/traceability.csv` (to be generated from this document by an author pass). In particular:

- Stakeholder Q&A items Q1–Q66 are each addressed; decisions marked "SKIP" (Q33) are deliberately absent.
- Items marked **Recommendation applied** in this document are: Q9 (Qdrant), Q22 (Vault + envelope), Q23 (permission matrix), Q36 (prompt strategies), Q47 (WS+SSE), Q38 (sandbox), Q53 (loop detection), Q56 (audit scope).

---

*End of document.*
