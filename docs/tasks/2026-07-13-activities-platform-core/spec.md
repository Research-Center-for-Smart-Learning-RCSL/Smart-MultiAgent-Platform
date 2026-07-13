---
type: feature
status: approved
created: 2026-07-13
requirements: [R23.01, R23.03, R8.12, R13.25]
---

# Structured Activities — Platform Core (`activities` bounded context)

## 1. Summary

Add a new generic backend bounded context, `activities`, that lets a room host **typed,
schema-validated, server-scored interaction events** alongside free-text chat. A
participant's structured submission (an "attempt") is validated against a per-type JSON
schema, scored by a pluggable server-side validator, persisted as the authoritative
record, and echoed into the chat transcript for readability. This is the platform
foundation on which education/research use cases — including the two NSTC creativity
projects in `_projects_documents/` (see `docs/assessments/nstc-multiagent-fit-assessment.md`
Part B) — are built as pure config + plugins + data, with zero domain logic in the
platform core.

This dossier is **self-complete for the backend core**: it defines the context, all three
validator adapters, the cross-context seams they need (two thin `AgentsFacade` methods),
the submission/session lifecycle, the SYSTEM echo, the aggregation read model, and a
stalled-validation watchdog — nothing in this dossier's own scope is deferred. Four
adjacent capabilities that build **on top of** this core are each a separate, self-complete
dossier and are enumerated in §16 (Program Roadmap): workflow reactive rules, observer
context provider, frontend plugin SDK, and the sampling-reproducibility fix.

## 2. Goals and Non-goals

**Goals**
- A new `activities` context (domain/application/infrastructure/interfaces) following the
  DDD layout and import rules in `backend/CLAUDE.md`.
- `ActivityType` (registered type: JSON schema + validator config), `ActivitySubmission`
  (the authoritative typed event), `ActivitySession` (a subject's run of a task, with
  server-assigned monotonic `attempt_no`).
- Server-side validation + scoring authority: a submission's correctness/score is
  computed server-side; client-supplied scores are never trusted.
- Three validator adapter kinds behind one `Validator` port: **in-process** (synchronous,
  `jsonschema` + a registered pure scoring function), **MCP** and **webhook**
  (asynchronous via a worker job that composes the `agents` facade, results written back).
- Two thin, reusable `AgentsFacade` methods (`invoke_mcp_tool`, `egress_request`) so the
  validator worker composes MCP/webhook capability **via facades only**, never by reaching
  into `contexts/agents` internals.
- SYSTEM-message echo into the room transcript on submission (authoritative record stays
  the `ActivitySubmission`).
- Generic aggregation/read model (per subject/session/room) usable later by dashboards,
  the observer, and reactive rules.
- A `pending→error` watchdog sweep for async validations that never complete.
- Tenant isolation on every endpoint via the existing room-access chain; full audit.

**Non-goals** (each is a separate, self-complete dossier — see §16 — or project-side config)
- Workflow reactive rules (`activity_event` trigger + SEL + rolling-aggregate signal). This
  core does **not** emit `workflow_signal("activity", …)` yet — wiring a signal with no
  consumer would be dead code; the reactive-rules dossier adds the emit and the consumer
  together.
- Observer-over-events (`ActivityContextProvider` in `agents`).
- Frontend plugin SDK / activity UI (`slices/activities`).
- Sampling-reproducibility fix (temperature/seed on the Agent config).
- Any Chinese-character / creativity logic: activity-type schemas, the component-scoring
  validator function, the manipulation-canvas plugin, the AA agent prompt/rubric, and the
  creativity dashboard are **project config/plugins/data**, out of platform scope.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Scope of this spec across backend context / workflow rules / observer / plugin SDK / reproducibility fix | Foundation-first: this dossier = backend `activities` platform core; the four adjacent capabilities are separate, self-complete linked dossiers (§16) | Keeps each dossier buildable/verifiable by `/build`; none leaves a dangling half-feature |
| Q-2 | Where does the sampling-reproducibility fix live | Separate small dossier (§16) | Touches `agents` + provider adapters, not `activities`; orthogonal and independently useful |
| Q-3 | Which validator adapters in v1 | All three: in-process + MCP + webhook | Covers every generic need without deferral |
| Q-4 | Name collision: `[R14.01]` (`REQUIREMENTS.md:708`) already uses "activities" as jargon for workflow DAG nodes | Keep `activities` as the context name; disambiguate in the SRS Delta | The workflow term is internal DAG-node jargon, not a bounded context; separate namespaces. Surfaced for the approval gate. |
| Q-5 | Which validator kind does a **deterministic first-party** scorer (e.g. the project's Chinese-component scorer, >95% deterministic over a data table) use | **in-process**, not MCP | In-process is synchronous, reproducible, and container-free. MCP spawns a gVisor container and adds stdio/JSON-RPC nondeterminism + seconds of latency — correct for *untrusted/external* validator servers (same trust story as the deferred plugin sandbox), wrong for trusted deterministic scoring. This corrects an earlier assumption that the component scorer is an MCP tool. |
| Q-6 | How do MCP/webhook validators reach the agents-owned sandbox + egress without an `agents↔activities` cycle or a worker reaching into agents infrastructure | Add two thin `AgentsFacade` methods; the validator worker (a composition root) calls them via the facade | `activities` never imports `agents`; the worker calls **facades only** (honours `backend/CLAUDE.md` "call the context facade — never reach into application/infrastructure"). Corrects the earlier draft, which had the worker use `contexts/agents/infrastructure/egress_client.py` directly — a SoC violation. |

## 4. Current State

There is **no** structured-submission / task / activity capability today. Grep for
`activit|submission|assessment` across `REQUIREMENTS.md` finds only incidental jargon:
`[R14.01]` (`REQUIREMENTS.md:708`) calls workflow DAG nodes "activities", and `[R15.18]`
(`REQUIREMENTS.md:789`) uses "task" as a free-text sub-agent job description. Rooms carry
only free-text `messages` (`contexts/conversation/domain/models.py:88-99`) with
`sender_type ∈ {user, agent, system}` (`:12-15`).

Relevant existing surfaces this feature builds on (all verified):
- **Cross-context SYSTEM insert** already exists: `ConversationFacade.create_message`
  (`contexts/conversation/interfaces/facade.py:150-166`) accepts `SenderType.SYSTEM`,
  `sender_id=None`, and service-stamped `metadata`; `SenderType` is re-exported at
  `facade.py:274`. The observation-release flow is the choreography to mirror:
  service builds a service-stamped SYSTEM row (`contexts/conversation/application/observation_service.py:145-181`)
  → route commits (`app/api/v1/observations.py:157`) → post-commit `message.created`
  emit on `room_channel` (`observations.py:197-205`). Clients cannot forge `metadata`
  (`observation_service.py:173-174`; `app/api/v1/messages.py:80-81`).
- **Tenant isolation**: `resolve_room_access` (`contexts/conversation/application/access.py:52-93`)
  + `ensure_can_read`/`ensure_can_send` (`access.py:125-176`) ride the FK chain
  `message → chatroom → workspace → project` (`contexts/conversation/infrastructure/tables.py:131-136, 35-40, 14-16`).
- **In-process JSON-schema validation**: `jsonschema==4.23.0` is already a dependency
  (`backend/requirements.lock:109`); the workflow context validates against a JSON schema
  via `jsonschema.Draft202012Validator(...).iter_errors(...)`
  (`contexts/workflow/application/workflow_service.py:430-445`).
- **MCP invocation seam — agents-owned, NOT on the facade today.** `SandboxRunner.invoke_mcp_tool`
  (`contexts/agents/application/mcp_ports.py:42-54`, impl
  `contexts/agents/infrastructure/sandbox/docker_runsc.py:522-586`) is turn-independent (also
  called headless by the probe endpoint, `contexts/agents/application/agent_service.py:883`)
  but is coupled to the agents domain: it needs `agent_id` + `binding_id` → an `AgentTool` row,
  and always spawns a gVisor container. **`AgentsFacade` exposes no MCP-invocation method**
  (facade methods are agent CRUD only: `get_agent`, `patch_agent`, `restore_agent`,
  `list_agent_tools`, … — verified by grep of `contexts/agents/interfaces/facade.py`). There
  is **no** in-process JSON-RPC MCP client. → this dossier adds a facade method (§6).
- **Webhook egress (SSRF-safe) — agents-owned, NOT on the facade today.** Outbound HTTP must
  route through the egress proxy via `HttpxEgressProxyClient.request`
  (`contexts/agents/infrastructure/egress_client.py:52-122`; factory
  `egress_proxy_client_from_settings`), which gives allowlist + resolved-IP screen +
  DNS-rebinding pinning + HMAC + auth stripping. The reference wrapper (project allowlist via
  `function_egress_allowed` + 60/min rate limit + fail-closed auth) is `_build_function_tool._invoke`
  (`contexts/agents/application/runtime/builtin_tools.py:451-527`). The allowlist repo
  (`EgressAllowlistRepository`, `contexts/agents/infrastructure/mcp_repositories.py:28`) and table
  (`mcp_egress_allowlist`, `contexts/agents/infrastructure/mcp_tables.py:15`) are agents-owned.
  `is_blocked_ip` (`services/egress_proxy/ip_policy.py:65-108`) is proxy-only; calling it
  directly from the backend would skip DNS pinning + allowlist and is **not** the safe pattern.
  → this dossier factors `_invoke`'s wrapper into a reusable application function and exposes
  it via a facade method (§6), so both the agent-tool path and the validator path share it.
- **Queue**: `shared_kernel.queue.enqueue(job_name, *args, **kwargs)` (`shared_kernel/queue.py:21-33`);
  raises on Redis failure (wrap best-effort). Post-commit dispatch idiom at
  `app/api/v1/messages.py:373-388`.
- **Context anatomy blueprint**: `agent_groups` (domain `contexts/agent_groups/domain/models.py`,
  tables `contexts/agent_groups/infrastructure/tables.py`, repo `.../infrastructure/group_repository.py`,
  service `.../application/group_service.py`, facade `.../interfaces/facade.py`, error map
  `.../interfaces/error_mapping.py`); table registration in `backend/app/db_registry.py:15-38`;
  router registration in `backend/app/api/v1/__init__.py` `_build_registry()`; auth via
  `current_principal`/`current_context`/`require`/`require_membership`/`scope_from_path`
  (`shared_kernel/auth/dependencies.py`) + in-body `deps.py` assertions
  (`backend/app/api/v1/deps.py:22-66`).
- **Cross-context facade-call precedent** (confirms the worker/observer pattern): `agents`
  already calls foreign facades — `KeysFacade`/`KnowledgeFacade` as service fields
  (`agent_service.py:69-70, 212-213`), `ConversationFacade`/`IdentityFacade`/`KnowledgeFacade`
  in `turn_engine.py` (e.g. `:281, 1352, 1355, 1698-1702`).

**End-to-end data flow (target).**
`POST /activity-submissions` → route auth + `resolve_room_access`/`ensure_can_send` →
`SubmissionService.submit`: resolve/open the `ActivitySession` + assign `attempt_no` →
validate payload vs the `ActivityType` JSON schema → **in-process**: run the registered
validator synchronously, set `validation_status=validated` + `is_valid`/`sub_scores`;
**mcp/webhook**: set `validation_status=pending` → persist `ActivitySubmission` → echo SYSTEM
message via `ConversationFacade` → `audit.emit` → **commit (atomic)** → **post-commit
best-effort**: WS `activity.created` + (for mcp/webhook) `enqueue(validate_activity_submission)`.
Async path: worker
`app/workers/tasks/activities.py` calls `AgentsFacade.invoke_mcp_tool` / `AgentsFacade.egress_request`,
maps the result → `SubmissionService.record_validation` (`validated`+`is_valid` or `error`),
emits WS `activity.validated`, audits. Watchdog cron sweeps `pending` rows older than the
job TTL → `error`.

## 5. Design

### 5.1 Validator execution model

**Option A — all validators synchronous in `SubmissionService`.** Simple submit→score in
one request. Trade-off: MCP spawns a gVisor container (seconds) and webhook is a network
round-trip — both block the request and, worse, would require `activities` to reach the
agents MCP seam / egress client, creating an `agents ↔ activities` cycle once the observer
dossier adds `agents → activities`.

**Option B — split by adapter kind: in-process sync, MCP/webhook async via a worker.** (chosen)
In-process validators run synchronously in `SubmissionService`. MCP/webhook validators
persist `validation_status=pending`, enqueue `validate_activity_submission` whose handler
lives in `app/workers/tasks/activities.py` (a composition root, which may import multiple
context facades), calls the slow validator **through `AgentsFacade`**, and writes the result
back through `ActivitiesFacade`. Trade-off: MCP/webhook scoring is eventually-consistent (a
second WS event `activity.validated`) with two write paths — acceptable, since those
validators are inherently slow and the UI needs a "validating…" state anyway.

**Decision: Option B.** It is the only option preserving an acyclic context graph. The
`activities` context imports only the `conversation` facade (echo) and `shared_kernel`; it
never imports `agents`. MCP/webhook composition happens in the **worker layer**, which calls
`AgentsFacade` + `ActivitiesFacade` — facades only, no reach into any context's
application/infrastructure.

**Dependency graph (this dossier + planned follow-ups):**
```
activities ──facade──▶ conversation                    (SYSTEM-message echo)
app/workers/tasks/activities.py ──▶ activities facade + agents facade   (composition root)
agents(observer, later) ──facade──▶ activities         (read events)    [separate dossier]
```
No cycle: `activities` never imports `agents`; the worker depends on both facades but is not
a context.

### 5.2 The `AgentsFacade` seam (SoC fix)

The validator worker must not import `contexts/agents/infrastructure`. This dossier adds two
thin facade methods, each a pass-through to existing, verified internals:

- `AgentsFacade.invoke_mcp_tool(*, project_id, agent_id, binding_id, tool_name, arguments,
  timeout_s) -> McpToolResult` — wraps the headless sandbox invocation already used by the
  probe path (`agent_service.py:883`), returning a neutral result value object (not an
  agents-internal type).
- `AgentsFacade.egress_request(*, project_id, method, url, headers, body, upstream_auth,
  timeout_s) -> EgressResponse` — wraps a **new reusable application function**
  `perform_egress_request(...)` extracted from the middle of `_build_function_tool._invoke`
  (`builtin_tools.py:455-527`): project allowlist (`function_egress_allowed`, `:455`) →
  fixed-window per-project rate limit (`:462-482`) → `deps.proxy.request(...)` (`:498-516`) →
  status classification. **Auth resolution stays caller-side**: the agent-tool path resolves
  credentials from the `AgentTool` row via `resolve_tool_auth(tool)` (`:485-493`) and passes the
  resulting `upstream_auth` in; the validator path passes `upstream_auth` derived from
  `validator_config` (there is no tool row). Auditing likewise stays caller-side
  (`_audit_tool_invoke` is agent-tool-specific; the validator worker audits via its own
  `activity.validated` event). `_build_function_tool._invoke` is refactored to call the shared
  function, so both paths share one copy of the allowlist + rate-limit + proxy-call policy.

Both are behaviour-preserving for existing agent flows (the refactor is covered by existing
agent-tool tests) and expose no agents-domain types across the boundary.

### 5.3 Validator port + registry

`activities/application/validators/` defines a `Validator` protocol (port) and a **registry**:
- `registry.register_in_process_validator(validator_id: str, fn)` — the platform ships **zero**
  domain validators. First-party project validators register at app startup from a module
  **outside** `contexts/activities` (e.g. a project package under `app/plugins/`), keeping the
  context domain-free. Registering an unknown `validator_id` on an `ActivityType` is rejected
  at type registration (`ValidatorConfigInvalid`).
- `InProcessValidator` — loads the `ActivityType` payload schema, runs
  `jsonschema.Draft202012Validator(schema).iter_errors(payload)` (mirroring
  `workflow_service.py:439-445`), then calls the registered scoring function with signature
  `fn(payload: dict, activity_type: ActivityType, *, db) -> ValidationResult`. Passing `db`
  lets a first-party function query project-owned data tables (e.g. a component lexicon the
  project migrates in as data) while the port stays generic. **Trust note:** an in-process
  validator is first-party backend code running in the app process with a live DB session —
  the same trust tier as any backend module, and the reason MCP (sandboxed) exists for
  *untrusted* validators (Q-5). Only register in-process validators you ship. Returns
  `ValidationResult{is_valid, error_class, sub_scores, detail}`.
- `McpValidator` / `WebhookValidator` — worker-side; call `AgentsFacade.invoke_mcp_tool` /
  `AgentsFacade.egress_request` and map the result → `ValidationResult`.

### 5.4 Submission / session lifecycle and status semantics

**Two orthogonal status fields — do not conflate:**
- `validation_status: ValidationStatus = pending | validated | error` — the *execution* state
  of the validator. `pending` = async validator not yet run; `validated` = the validator ran
  to completion; `error` = the validator could not produce a verdict (MCP egress denied,
  webhook 5xx/timeout, worker exception, or watchdog-swept stale `pending`).
- `is_valid: bool | None` — the *answer correctness*, meaningful only when
  `validation_status=validated`; `None` while `pending` or on `error`.

(The earlier draft used a single `failed` state that conflated "validator didn't run" with
"answer is wrong". Split here so a dashboard can distinguish an infrastructure fault from a
wrong answer.)

**Session lifecycle.** `ActivitySession{id, activity_type_id, chatroom_id, subject_user_id,
status: open|closed, created_at, closed_at}`. A submission body may carry an explicit
`session_id`; otherwise `SubmissionService` reuses the caller's single `open` session for
`(activity_type, chatroom, subject)` or lazily opens one.

*Lazy-open concurrency (two failure modes, both handled):* (1) two concurrent first
submissions must not open two sessions — a **partial unique index**
`(activity_type_id, chatroom_id, subject_user_id) WHERE status='open'` makes the second
`INSERT` conflict; the service does `INSERT … ON CONFLICT DO NOTHING` then re-selects the
winning open session. (2) two concurrent submits to the same session must not collide on
`attempt_no` — the service takes `SELECT … FOR UPDATE` on the resolved session row before
computing `attempt_no = (count of prior submissions in the session) + 1`, so numbering is
serialized. (`FOR UPDATE` alone is insufficient for case 1 because there is no row to lock
yet; the partial unique index is what closes that race.)

A project starts a fresh task instance via `POST /activity-sessions` (returns an `open`
session id) and closes via `PATCH …/close`; closing lets the next submission lazily open a
new session under the same partial-unique constraint. The `pending→error` watchdog does not
touch sessions.

**Echo timing.** The SYSTEM echo is inserted once, at submit. For `in_process` (result known
synchronously) the echo may render the outcome. For `mcp`/`webhook` the echo is neutral
("submitted an attempt"). The async result is **not** re-echoed as a second SYSTEM message
(keeps the transcript readable); it lands on the `ActivitySubmission` + WS `activity.validated`.
The authoritative record is always the `ActivitySubmission`.

### 5.5 Why a new bounded context (alternatives steelmanned)

This is the most expensive, least-reversible decision in the program, so it is recorded with
the alternatives that were rejected and why:

- **Fold into `conversation` as a message subtype** (messages already carry `sender_type` +
  `metadata` JSONB). Rejected: the message write path has **no** validation/scoring today
  (verified — `conversation/application` has no jsonschema/scoring; the only `validate` hits are
  TUS upload allowlists). Adding server-side scoring would drag a `Validator` port, jsonschema,
  sessions, an aggregation read model, and — fatally — the MCP/egress composition into
  `conversation`, creating the `conversation ↔ agents` import cycle this design exists to avoid.
- **Fold into `workflow`.** Rejected: workflow is an orchestration engine (DAG/FSM runs); a
  participant submission is data collection, not an orchestration run, and workflow validates
  workflow *definitions*, not arbitrary participant payloads. The correct integration is the
  loose queue **signal** (reactive-rules dossier), not a merge.
- **Put it in `shared_kernel`.** Rejected: `shared_kernel` holds cross-cutting utilities and
  never owns domain/business logic; a scored-submission lifecycle is squarely domain.
- **Fold into `agents`.** Rejected: it would make every non-agent consumer (dashboard, workflow,
  conversation echo) reach into `agents`, and couples participant-task data to the agent
  lifecycle — the opposite of the agent-agnostic goal.

**The sharpest counter-precedent, and the rebuttal.** `AgentObservation`
(`conversation/domain/models.py:103-116`) is a room artifact that was folded **into**
`conversation`, not given its own context — so why not `ActivitySubmission` too? Because
`AgentObservation` is a lightweight text note with a release flag (`content_md`, `trigger`,
`released_at`, `release_target`) and **zero** external dependencies. `ActivitySubmission`
carries jsonschema validation, a three-adapter validator port, MCP/egress composition,
session/attempt semantics, and a generic aggregation model. The principled line the codebase
already draws is **dependency weight + the validator coupling**: lightweight room-text-artifact
→ `conversation`; heavyweight scored-task subsystem with external validators → its own context.
`activities` is on the far side of that line. (Bonus: `AgentObservation`'s "release into the
transcript" flow is the exact precedent for the SYSTEM echo — the echo is not novel machinery.)

**Disambiguation for future readers:** three room nouns now coexist — `Message` (free-text),
`AgentObservation` (observer *output* about the room), and `ActivitySubmission` (participant
*input*, scored). They are distinct and non-overlapping.

## 6. Detailed Changes

**Backend — new context `activities`** (mirrors `agent_groups` anatomy):
- `domain/models.py` — `@dataclass(frozen=True, slots=True)`, framework-free:
  - `ActivityType{id, project_id, key, name, payload_schema: dict, validator_kind:
    ValidatorKind, validator_config: dict, created_at, deleted_at, version}`
  - `ValidatorKind(str, enum.Enum)` = `in_process | mcp | webhook`
  - `ActivitySession{id, activity_type_id, chatroom_id, subject_user_id, status:
    SessionStatus, created_at, closed_at}`; `SessionStatus` = `open | closed`
  - `ActivitySubmission{id, session_id, activity_type_id, chatroom_id, producer_user_id,
    payload: dict, attempt_no, validation_status: ValidationStatus, is_valid: bool|None,
    error_class: str|None, sub_scores: dict, latency_ms: int|None, created_at,
    validated_at, deleted_at}`
  - `ValidationStatus(str, enum.Enum)` = `pending | validated | error`
  - `ValidationResult` value object.
- `domain/errors.py` — base `ActivitiesError` with `code` (pattern
  `agent_groups/domain/errors.py:6-27`): `ActivityTypeNotFound`, `SubmissionNotFound`,
  `SessionNotFound`, `PayloadSchemaInvalid` (422), `ActivityTypeKeyConflict` (409),
  `ValidatorConfigInvalid` (422, incl. unknown `validator_id`).
- `application/submission_service.py` — `submit(...)` (resolve/open session + assign
  `attempt_no` under `FOR UPDATE` → validate schema → sync in-process (result in the same
  commit) OR persist `pending` then enqueue the async job **post-commit** → echo via
  `ConversationFacade` → audit); `record_validation(...)` (worker write-back — **idempotent:
  transitions only from `pending`, so an Arq retry / redelivery on an already-terminal row is a
  no-op**); `sweep_stalled(...)` (watchdog, same `pending`-only guard).
- `application/session_service.py` — start/close/list sessions.
- `application/type_service.py` — register/list/soft-delete `ActivityType`; validates the
  supplied `payload_schema` is a well-formed JSON Schema and that a referenced in-process
  `validator_id` is registered, at registration time.
- `application/aggregation_service.py` — read model: list submissions (filtered by
  session/subject/room, paginated) + a generic aggregate (count, valid-count, error-class
  histogram, latency stats) per subject/session in a single grouped query.
- `application/validators/` — `Validator` port, `registry.py`, `InProcessValidator`;
  `McpValidator`/`WebhookValidator` constructed worker-side.
- `infrastructure/tables.py` — `activity_types`, `activity_sessions`, `activity_submissions`
  (PG ENUMs `validator_kind`, `validation_status`, `session_status` with `create_type=False`
  referencing the migration; JSONB for schema/payload/config/sub_scores; `deleted_at`; FK to
  `projects`/`chatrooms`). Register in `app/db_registry.py`.
- `infrastructure/repositories/` — `type_repo.py`, `session_repo.py`, `submission_repo.py`
  (pattern `group_repository.py`: Core queries, row→domain free functions, `deleted_at IS
  NULL` filters, stable order-by, `FOR UPDATE` on the session row for attempt numbering).
- `interfaces/facade.py` — `ActivitiesFacade(db)` thin pass-throughs (incl. a
  `list_recent_activity(chatroom_id, limit)` read for the future observer);
  `interfaces/error_mapping.py` — RFC-7807 map + `register(app)`; wire in `app/main.py`.

**Backend — `agents` context (the SoC seam, §5.2):**
- `application/egress.py` (new) — `perform_egress_request(...)` extracted from
  `_build_function_tool._invoke` (`builtin_tools.py:451-527`); refactor `_invoke` to call it.
- `interfaces/facade.py` — add `AgentsFacade.invoke_mcp_tool(...)` and
  `AgentsFacade.egress_request(...)` (§5.2), returning neutral result value objects.

**Backend — `conversation` context:** add a thin `insert_system_message(chatroom_id,
content_md, metadata)` wrapper (hardcodes `sender_id=None`, namespaces `metadata["type"]`)
over `create_message` (`facade.py:150-166`), and widen the `create_message` docstring that
currently reads "transcript compaction store" (`facade.py:159`) to reflect its general SYSTEM
use (folds former FU-2 into scope).

**Worker:** `app/workers/tasks/activities.py` — `validate_activity_submission(submission_id)`
(reads submission via `ActivitiesFacade`, runs MCP/webhook via `AgentsFacade`, calls
`record_validation`, emits WS `activity.validated`); `activities_watchdog()` cron sweeping
stale `pending` → `error` (mirror the workflow watchdog cron in `app/workers/main.py`).
Register both in `app/workers/main.py`.

**API contract** — new `app/api/v1/activities.py` (register in `_build_registry()`), Pydantic
models, facade-only calls:
- `POST /api/projects/{project_id}/activity-types` — register (owner/`RESOURCE_CREATE_EDIT`).
- `GET /api/projects/{project_id}/activity-types` — list (membership).
- `POST /api/chatrooms/{chatroom_id}/activity-sessions` — start a session (room `ensure_can_send`).
- `PATCH /api/chatrooms/{chatroom_id}/activity-sessions/{id}/close` — close (room `ensure_can_send`).
- `POST /api/chatrooms/{chatroom_id}/activity-submissions` — submit (room `ensure_can_send`).
- `GET /api/chatrooms/{chatroom_id}/activity-submissions` — list/aggregate (room `ensure_can_read`).
- `gen:api` rerun required: **yes**.

**Frontend** — none in this dossier (plugin SDK is a separate dossier).

**Deploy/config** — none. MCP validators reuse the gVisor sandbox + egress; webhook
validators reuse the egress proxy.

**Migration** — `backend/alembic/versions/0049_activities.py`, `down_revision = "0048_knowmap"`.
Mint ENUMs `validator_kind`, `validation_status`, `session_status`; tables with UUID PK
`gen_random_uuid()`, FK to `projects`/`chatrooms` `ON DELETE CASCADE`, JSONB `'{}'::jsonb`
defaults, `deleted_at`, `version`; partial unique index `activity_types (project_id, key)
WHERE deleted_at IS NULL`; **partial unique index `activity_sessions (activity_type_id,
chatroom_id, subject_user_id) WHERE status='open'`** (the lazy-open race guard, §5.4); indexes
`(session_id)`, `(chatroom_id, created_at)`, `(subject_user_id)`, and `(validation_status,
created_at)` for the watchdog sweep. Reversible `downgrade()` drops only what it creates.

## 7. NFR Checklist

- [x] i18n — no user-facing strings added in backend; API returns codes/data.
- [x] Audit log — `activity_type.created`, `activity.submitted`, `activity.validated` via
  `audit.emit` on the caller's transaction (pattern `group_service.py:44-55`).
- [x] Tenant isolation — every endpoint gates through `resolve_room_access` +
  `ensure_can_read`/`ensure_can_send` (rooms) or `require_membership`/owner (project types).
  No cross-context SQL joins (`[R23.01]`); role resolution via `TenancyFacade` (`access.py:75-83`).
- [x] Error handling UX — RFC-7807 via `error_mapping.register`; `PayloadSchemaInvalid`/
  `ValidatorConfigInvalid` 422, `ActivityTypeKeyConflict` 409, `*NotFound` 404. Submission
  returns `validation_status` + `is_valid` so the client renders `pending`/`validated`/`error`.
- [x] Performance — high-volume submissions: list endpoints paginate (`PaginationParams`,
  `deps.py:14-19`); indexes per §6; aggregation is one grouped query (no N+1); payload capped
  at the API model. `FOR UPDATE` scoped to a single session row.

## 8. Security Considerations

Touches user-input processing, tenant boundaries, and outbound network (webhook) — full lens:
- **Scoring authority is server-side and non-negotiable.** The adversary is a participant who
  controls their browser and can forge any client payload/score. The client submits only a raw
  `payload`; `is_valid`/`sub_scores`/`error_class` are computed server-side. No client-supplied
  score is ever persisted (holds even for trusted first-party plugins — they run in the
  participant's browser).
- **Payload validation before use.** Every `payload` is validated against
  `ActivityType.payload_schema` via `jsonschema` before persistence or dispatch; violations
  422. Schemas are validated as well-formed JSON Schema at registration.
- **Webhook SSRF.** Webhook validators call **only** `AgentsFacade.egress_request`, which
  routes through the egress proxy (allowlist + resolved-IP screen + DNS-rebinding pinning +
  HMAC + `Authorization`/`Cookie` stripping) plus per-project rate limit + fail-closed auth,
  via the shared `perform_egress_request` (§5.2). The backend never makes a raw outbound call
  and never re-implements `is_blocked_ip`. **Operational dependency:** the validator host must be
  on the project's egress allowlist (`mcp_egress_allowlist`, the same list agent tools use). A
  project that configures a `webhook` validator must therefore be able to add its host to that
  allowlist through the existing egress-allowlist management surface; a webhook to a
  non-allowlisted host fails closed. (If that management surface is today only reachable via
  agent-tool config flows, exposing it for validator hosts is a small config-UX task — not a
  core-spec blocker, since the project's actual validator is `in_process`, which needs no
  allowlist.)
- **MCP isolation.** MCP validators run in the existing gVisor sandbox with egress-denied
  networking; exit-code 42 → egress-denied surfaces as `validation_status=error`, not a crash.
- **Tenant isolation.** A submission is bound to a chatroom; the room-access chain prevents
  submitting to / reading another tenant's room. `ActivityType` is project-scoped; registration
  requires owner capability.
- **Audit + non-forgeable metadata.** Submission and validation are audited; SYSTEM-echo
  `metadata` is service-stamped (mirrors `observation_service.py:173-174`).

## 9. Quality Notes

- **Existing debt (record, decide explicitly):** `ConversationFacade.create_message` docstring
  says "transcript compaction store" (`facade.py:159`) though the method is general — widened
  here (§6), not left silent. The agents egress SSRF policy currently lives only inside a
  closure (`_build_function_tool._invoke`) — extracting `perform_egress_request` (§5.2) removes
  that as a reuse blocker rather than copying it.
- **Patterns to follow:** context anatomy — `agent_groups`; schema validation —
  `workflow_service.py:430-445`; SYSTEM insert + post-commit emit — `observation_service.py` +
  `observations.py:197-205`; outbound webhook — `_build_function_tool` (`builtin_tools.py:451-527`);
  cross-context facade call — `agent_service.py:69-70, 212-213`; table/migration — `0048_knowmap.py`;
  watchdog cron — the workflow watchdog in `app/workers/main.py`; router+auth — `knowmap.py`.
- **Reuse inventory (use, do not re-invent):** `jsonschema` (dep); `ConversationFacade` /
  new `insert_system_message`; `resolve_room_access`/`ensure_can_read`/`ensure_can_send`
  (`access.py`); `audit.emit` + `AuditEvent` (`shared_kernel/audit.py:103-145`); `Publisher` +
  `room_channel` (`shared_kernel/realtime/pubsub.py`, `conversation/infrastructure/channels.py:12-13`);
  `enqueue` (`shared_kernel/queue.py:21`); `SandboxRunner.invoke_mcp_tool` (`mcp_ports.py:42`);
  `HttpxEgressProxyClient` + `function_egress_allowed` (`egress_client.py:52`, `builtin_tools.py`);
  `PaginationParams` + `require`/`require_membership`/`scope_from_path` (`deps.py`,
  `shared_kernel/auth/dependencies.py`).

## 10. Risks and Rollback

- **Transaction boundary (made explicit to avoid ambiguity).** The `ActivitySubmission` row,
  the echoed SYSTEM message row, and the audit row are written in **one transaction** and
  committed atomically (the `observation_service` pattern — service builds rows, route commits
  once). There is no "submission without echo" or "echo without submission" state. The
  `ActivitySubmission` is the authoritative source of truth; the echo is a derived readability
  copy in the same commit. Only the **post-commit** side effects are best-effort: the WS
  `activity.created` emit and the `enqueue(validate_activity_submission)` (both after commit,
  wrapped so a Redis/pubsub failure cannot roll back a committed submission).
- **Eventually-consistent MCP/webhook scoring, and lost enqueues.** A submission can sit
  `pending` if the worker stalls **or if the post-commit enqueue itself failed** (Redis down at
  emit time). The `(validation_status, created_at)` index + `activities_watchdog` cron is the
  single safety net for **both**: it sweeps any `pending` older than the job TTL to `error`
  (in scope, §6) — so a dropped enqueue degrades to a visible `error`, never a permanent
  silent `pending`.
- **Worker idempotency (Arq at-least-once).** `record_validation` guards on
  `validation_status = pending` and transitions only from there; a re-delivered or retried
  `validate_activity_submission` job for an already-`validated`/`error`/watchdog-swept row is a
  no-op. This prevents a double score-write or clobbering a watchdog verdict with a late
  worker result.
- **Agents egress refactor touches a security-critical path.** `perform_egress_request` is a
  pure extraction; existing agent-tool tests must pass unchanged before and after (guards the
  behaviour-preservation claim).
- **Naming overlap with workflow "activities" jargon** (Q-4) — documentation risk only.
- **Migration reversibility:** `0049_activities.py` `downgrade()` drops the three tables and the
  three new ENUMs it minted, in reverse order; touches nothing pre-existing.

## 11. Acceptance Criteria

- [ ] AC-1: A project owner can register an `ActivityType` with a JSON-Schema `payload_schema`
  and a `validator_kind`; a malformed schema → 422 (`PayloadSchemaInvalid`); a duplicate
  `(project_id, key)` → 409; an `in_process` type naming an **unregistered** `validator_id` →
  422 (`ValidatorConfigInvalid`).
- [ ] AC-2: A submission whose payload violates the type's schema → 422, nothing persisted.
- [ ] AC-3: A submission to an `in_process`-validator type returns synchronously with
  `validation_status=validated` and server-computed `is_valid`/`sub_scores`; a client-supplied
  score field in the body is ignored/not persisted.
- [ ] AC-4: A submission to an `mcp` or `webhook` type persists immediately with
  `validation_status=pending`, `is_valid=None`, and enqueues the job; after the worker runs, the
  submission is `validated` with `is_valid` set, **or** `error` with `is_valid` still `None` when
  the validator could not run.
- [ ] AC-5: Sessions — the first submission for `(type, room, subject)` lazily opens an `open`
  session; two concurrent first submissions open **exactly one** session (partial-unique
  conflict + re-select), not two; `attempt_no` is server-assigned and strictly increases per
  session even under two concurrent submits to that session (no duplicate numbers, via
  `FOR UPDATE`); `POST /activity-sessions` opens a fresh session; a client-sent `attempt_no`
  is ignored.
- [ ] AC-6: On submission the `ActivitySubmission`, the SYSTEM echo (service-stamped
  `metadata.type`), and the audit row commit **atomically in one transaction** (an injected
  echo-insert failure rolls back the submission — no orphan either way); the WS
  `activity.created` and the async enqueue happen post-commit; an async validation does **not**
  insert a second SYSTEM message.
- [ ] AC-6b: `record_validation` is idempotent — a second delivery of the validation job for an
  already-`validated`/`error` submission does not change the row or emit a duplicate result;
  a worker result arriving after the watchdog already marked the row `error` does not overwrite
  it.
- [ ] AC-7: Every endpoint enforces room/project access — no-access caller → 403; a caller
  cannot read or submit into another tenant's room.
- [ ] AC-8: A webhook validator call goes only through `AgentsFacade.egress_request` /
  the egress proxy (allowlisted host succeeds; a non-allowlisted or private/metadata IP target
  is refused), never as a direct backend outbound request.
- [ ] AC-9: `activities` imports no symbol from `contexts/agents` (wiring tripwire test); the
  validator worker composes MCP/webhook capability only through `AgentsFacade` (not
  `contexts/agents/infrastructure`).
- [ ] AC-10: `activities_watchdog` moves a `pending` submission older than the TTL to `error`;
  a `validated` or already-`error` row is untouched.
- [ ] AC-11: The aggregation endpoint returns per-session counts (total, valid), an error-class
  histogram, and latency stats in a single query, paginated.
- [ ] AC-12: The agents egress extraction is behaviour-preserving — the existing agent-tool
  egress tests pass unchanged, and `_build_function_tool._invoke` and the validator path both
  call `perform_egress_request`.

## 12. Test Plan

- Unit (`tests/unit/test_activities_*.py`): schema accept/reject (AC-1,2); in-process scoring
  + client-score-ignored (AC-3); unknown `validator_id` rejected (AC-1); session lazy-open +
  concurrent `attempt_no` monotonicity via `FOR UPDATE` (AC-5); status/`is_valid` semantics
  (AC-4); RFC-7807 mapping; repo `deleted_at` filtering; aggregation query shape (AC-11).
- Unit/authz (`test_activities_authz.py`): room/project gate matrix (AC-7).
- Worker (`test_activities_validation_worker.py`): async MCP/webhook write-back with
  `AgentsFacade` mocked → `validated` and `error` paths (AC-4); webhook-through-facade-only
  (AC-8); watchdog sweep (AC-10); **idempotency — re-running the job on a terminal row is a
  no-op, and a worker result after a watchdog `error` does not overwrite it (AC-6b)**.
- Transaction atomicity (`test_activities_echo_atomicity.py`): an injected echo-insert failure
  rolls back the submission (no orphan row); WS emit + enqueue are post-commit (AC-6).
- Agents (`test_agents_egress_extraction.py`): `perform_egress_request` extraction —
  existing egress-tool behaviour unchanged; both callers share it (AC-12).
- Wiring (`test_activities_no_agents_import.py`): tripwire `activities` ⊄ `agents` (AC-9);
  SYSTEM-echo + WS emit choreography + no-second-echo (AC-6).
- Integration: endpoint permission-matrix coverage (AC-7); audit rows (AC-8 → `activity.*`).
- Manual (`verify`): submit in-process → transcript echo + WS; submit MCP/webhook → observe
  `pending→validated`; leave a `pending` past TTL → watchdog → `error`.

## 13. SRS Delta

To append as a new chapter **§30** in `REQUIREMENTS.md` (before `*End of document.*` at
`:2045`), following the §28/§29 house convention:

```
## 30. Structured Activities

Added by the 2026-07-13 design session (task dossier: `docs/tasks/2026-07-13-activities-platform-core/`). A generic platform capability for typed, schema-validated, server-scored participant submissions within a room, on which education/research use cases are built as config + plugins + data. Note: "activity" here is a bounded context (structured participant tasks) and is distinct from the internal workflow DAG-node jargon in [R14.01]. This chapter also extends the bounded-context enumeration in [R3.04] with the `activities` context.

- **[R30.01]** The `activities` bounded context stores typed interaction events alongside free-text chat. An `ActivityType` registers a payload JSON Schema and a validator configuration; an `ActivitySubmission` is the authoritative record of one participant submission; an `ActivitySession` groups a subject's submissions to a type and carries a server-assigned monotonic attempt number.
- **[R30.02]** `ActivityType` is project-scoped; registration requires Project Owner capability. `(project_id, key)` is unique among non-deleted types. A registered payload schema must be well-formed JSON Schema; an in-process validator reference must name a registered validator.
- **[R30.03]** Scoring is server-side and authoritative. A submission's `is_valid`, `error_class`, and `sub_scores` are computed by the configured validator on the server; client-supplied score or attempt-number fields are never trusted or persisted.
- **[R30.04]** Every submission payload is validated against its `ActivityType` payload JSON Schema before persistence or validator dispatch; violations are rejected (422).
- **[R30.05]** A validator has one of three kinds: `in_process` (synchronous, a registered pure scoring function; the platform ships no domain validators), `mcp`, or `webhook` (both asynchronous via a worker job that writes the result back). The `activities` context never imports the `agents` context; MCP/webhook composition is performed in the worker layer through the agents facade only.
- **[R30.06]** A submission carries two orthogonal states: `validation_status` (`pending`, `validated`, `error`) describing whether the validator ran, and `is_valid` describing answer correctness (defined only when `validated`). Async validations that never complete are swept to `error` by a watchdog.
- **[R30.07]** Webhook validators make outbound calls only through the egress proxy (host allowlist, resolved-IP screening, DNS-rebinding pinning, credential stripping) with a per-project rate limit and fail-closed authentication.
- **[R30.08]** On submission the context echoes a service-stamped SYSTEM message into the room transcript for readability; asynchronous validation results are not re-echoed. The `ActivitySubmission` remains the authoritative record. Clients cannot supply message metadata.
- **[R30.09]** Every activities endpoint is tenant-isolated: room-scoped endpoints gate through the room-access chain; project-scoped registration gates through project membership/ownership. No cross-context SQL joins.
- **[R30.10]** The context exposes a generic aggregation read model (per subject/session/room: counts, error-class distribution, latency statistics) for downstream dashboards, observers, and reactive rules.
- **[R30.11]** Type registration, submission, and validation emit audit events.
```

## 14. Open Questions

- **OQ-1 (research-data retention — needs a human/IRB decision, not a silent default).**
  `activity_submissions` FK `chatroom_id ON DELETE CASCADE` (the codebase pattern —
  `conversation/infrastructure/tables.py:65,88,116,134`). Chatrooms are **soft-deleted**
  (`tables.py:49`), and `[R8.12]` physically purges rows 60 days after soft-delete, cascading
  children away. Chat **messages** additionally get a **5-year** retention (`[R13.25]`). This
  means the **authoritative research record would be hard-deleted 60 days after its room is
  deleted** — potentially *shorter* than the 5-year chat retention and than IRB longitudinal-
  study requirements (the meeting doc's data-retention item, `nstc-meeting-learning-activities.md`
  §G2). Decision needed: does `activity_submissions` (a) follow the room's 60-day-post-delete
  cascade, (b) inherit the 5-year message retention, or (c) get a configurable research-
  retention that can exceed both and is honoured by a dedicated purge (and listed in `[R8.12]`'s
  cascade)? Not blocking the core build — the schema carries `deleted_at` and the FK is correct
  either way — but the retention/purge policy must be set before real study data is collected.
  Surfaced for the approval gate.
- Q-4 naming resolved with disambiguation; surfaced for the approval gate.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Program Roadmap (linked dossiers)

This core carries no in-scope deferrals. The following build **on top of** it; each is its own
self-complete dossier under `docs/tasks/`. Order reflects dependency, not priority.

1. **`activities-reactive-rules`** (depends on this) — add a `workflow` `activity_event`
   trigger + SEL conditions + a `wait_for_event` activity kind; `SubmissionService` emits
   `workflow_signal("activity", payload)` post-commit carrying a lightweight rolling aggregate
   (same-error count in last N s, latency) so SEL rules stay stateless. Impasse detection = an
   SEL rule (project config). MVP = rules off (teacher-in-the-loop); professor auto-loop = rules on.
2. **`activities-observer-context`** (depends on this) — add an `ActivityContextProvider` in
   `agents/application` gated coverage-based on `is_observer` + room-has-activities (no `Agent`
   schema change; mirror `_observer_memory_block`, inject at `turn_engine.py` system-parts),
   calling `ActivitiesFacade.list_recent_activity`. The observer turn gets full chat history +
   full activity events; the AA rubric lives in its system prompt + a scoring tool (config).
3. **`activities-plugin-sdk`** (depends on this API) — new frontend `slices/activities`: plugin
   host + SDK (`defineActivityPlugin({manifest, schema, render, emit})`) + generic JSON-schema
   form renderer; host-mediated API calls; `activity.created`/`activity.validated` WS handling.
   v1 runs first-party bundled plugins only; the untrusted-plugin iframe sandbox is
   designed-for (postMessage contract fixed day one) but its enforcement is that dossier's
   explicit deferral, not this program's.
4. **`agent-sampling-reproducibility`** (standalone) — expose `temperature`/`seed` on the Agent
   config and thread through `turn_engine._stream_with_tools` → provider adapter bodies, so the
   AA's LLM-judged scoring is reproducible for Cohen's Kappa (adapters already forward
   temperature, e.g. `gemini.py:111`; the engine never sets it and the `Agent` dataclass has no
   field today).

*Optional future hardening (not required for correctness, not scheduled):* extract the egress
capability (client + allowlist) from `contexts/agents` into a neutral shared location so
webhook validators no longer depend on the agents facade. The facade-method seam (§5.2) is
clean today; this is only worthwhile if a third consumer appears.
