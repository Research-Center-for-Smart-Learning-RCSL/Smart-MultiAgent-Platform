# Phase K — Agent Runtime & Critical Gap Remediation

**Goal.** Close the five release-blocking gaps confirmed by the 2026-06-11 completeness audit: (1) no agent runtime / LLM inference loop exists anywhere in the backend; (2) `ProviderRouter` and the entire BYO-key rotation / quota / usage pipeline is orphaned (zero adapters, zero production callers); (3) the workflow engine cannot execute past its trigger node (executor registry regression, `approval_gate` constructor bug, cron-trigger FK violation, missing resume paths); (4) the MCP sandbox container images and their in-image driver do not exist in the repository; (5) no email delivery exists (logging-only sender) and the frontend ships no CAPTCHA widget, so production registration, verification, password reset, and invites are non-functional.

This phase makes the product *function*: a user message in an agent-bound chatroom must produce a streamed agent reply, routed through the user's own key group, with usage recorded — the loop every other subsystem was built to serve.

**Size.** XL
**Depends on.** A–J (all phases; this phase wires their deliverables together).
**Unblocks.** J.∞ (staging release gate). Do **not** attempt the release checklist before K closes — staging will fail at first contact.
**Refs.** `REQUIREMENTS.md` §6.1, §7.4–7.5, §9.2–9.4, §10.2, §12, §13.7, §14.3, §15, §19a.6. Audit evidence: completeness audit 2026-06-11 (file:line citations below are from that audit, verified against `main` @ `86d87d5`).

## K.0 Scope summary

By phase close:

- An agent bound to a chatroom replies to user messages: trigger → context assembly → provider call (streaming) → `agent.thinking / agent.token / agent.finished` over `/ws/chatroom/{id}` (R13.19) → persisted `sender_type=AGENT` message.
- Every outbound LLM/embedding/rerank call flows through `ProviderRouter` with concrete adapters; `key_usage_events` rows are written (R7.12); hourly caps, 80% thresholds, rotation, and exhaustion queueing bind for the first time.
- All 11 workflow executors are registered and reachable; approval, wait-for-event, and instruct nodes can park **and resume**; cron-triggered runs insert valid rows.
- `smap/mcp-runtime` and `smap/code-exec` images build from in-repo Dockerfiles with a working in-image driver; `code_exec`, `file`, `web_search`, and MCP tool invocation work end-to-end through the gVisor sandbox and egress proxy.
- Verification, password-reset, and invite emails are delivered via operator-configured SMTP; registration renders a real CAPTCHA widget.

**Out of scope for K** (tracked for a later phase; see audit report): frontend workflow-authoring canvas gaps, RAG/MCP configuration UI, notifications UI, message edit/delete UI, attachment display, guest-route auth bug, admin rate-limit Redis mirror, restore coverage, `graphrag_reconciler` deployment, rotate-transit checkpoint bug. None of these block the core loop; fixing them before the loop exists is polishing a car with no engine.

### Construction order

```
K.1 (adapters/router) ──► K.2 (turn engine) ──► K.3 (trigger wiring)
K.4 (workflow fixes)  — independent, may run in parallel with K.1–K.3
K.5 (sandbox images)  — independent; K.2's tool loop consumes it
K.6 (email + captcha) — independent
K.7 (wiring tripwire tests) — last, pins everything above
```

---

## K.1 Provider adapters & router wiring — **CODE** — L

Closes gap 2. `ProviderRouter` (`backend/contexts/keys/application/provider_router.py`) has correct retry-budget / rotation / quota-queue logic but **zero production callers and zero `ProviderAdapter` implementations** (its own docstring defers them to "Phase E", which never delivered). Meanwhile the only real LLM client, `HttpChatCompleter` (`backend/contexts/knowledge/infrastructure/chat_completer.py`), guesses the provider from the key prefix, hardcodes model IDs, and leaks the API key into exception messages via the request URL.

**Deliverables.**

- New package `backend/contexts/keys/infrastructure/adapters/`:
  - `anthropic.py` — Messages API, native streaming (SSE), system-prompt + tool-use support.
  - `openai.py` — Chat Completions, streaming, tool calls.
  - `gemini.py` — `streamGenerateContent`; key sent via `x-goog-api-key` header, **never** in the query string.
  - `voyage.py`, `openai_embed.py`, `gemini_embed.py` — embedding capability.
  - `cohere.py` — rerank capability.
  - Each implements the existing `ProviderAdapter` protocol; capability map already exists (`provider_router.py:380-386`).
- Adapter contract: model ID comes from the **agent's configured model** (`agents.model` / `model_hint`), never hardcoded. All errors pass through the probes' `summarise_http_failure` scrubber (`probes/base.py:76-94`) — status + provider error code only, no URLs, no headers, no key material.
- Streaming surface: adapters yield token deltas; `ProviderRouter.call` gains a streaming variant that preserves rotation semantics (a member that fails **before first token** rotates; after first token, the turn fails — partial responses are not retried across keys).
- Re-route existing bypasses through the router:
  - GraphRAG triple extraction (`workers/tasks/graphrag.py:148`) — replace `HttpChatCompleter` + `KeysFacade.unwrap_api_key_plaintext` with a router call against the builder key group.
  - RAG embedders (`knowledge/infrastructure/embedders.py`) — same.
  - Delete `HttpChatCompleter` once both callers are migrated; the key-prefix provider sniffing must not survive this phase.
- `record_usage_event` (`usage_events.py`) now fires on every call (R7.12), including `parent_agent_id` for sub-agent turns (R15.22); `redis_buckets.record` feeds the hourly window so the 30 s threshold worker (already scheduled, `app/workers/main.py:204`) finally observes non-zero data.

**Key IDs.** `[R7.07]`, `[R7.12]`–`[R7.14]`, `[R10.08]`, `[R15.22]`.

**Exit criteria.** Unit: each adapter against respx fixtures incl. streaming chunk reassembly and error scrubbing (assert no key material in any raised message). Integration: router + fake adapters exercises rotate-on-429, quota-queue 60 s, exhaustion; `key_usage_events` row written per call. GraphRAG build and RAG ingest still pass with the router in the path.

## K.2 Agent turn engine — **CODE** — XL

Closes gap 1 (core). There is no module in the backend that performs an agent turn: no prompt assembly, no provider call on behalf of an agent, no reply persistence. `SenderType.AGENT` is never constructed (`message_service.py:184` is its only mention, an immutability guard). The orphaned-but-tested components this engine must consume already exist: context compaction (`contexts/agents/application/context.py:94-223`), lazy prompt loading (`prompt_loader.py`), RAG retrieval (`knowledge/application/retrieve.py:54-156`), built-in tools (`web_search`, `file`, `code_exec`), and the A2A transport.

**Deliverables.**

- New package `backend/contexts/agents/application/runtime/`:
  - `turn_engine.py` — `TurnEngine.run_turn(agent_id, chatroom_id, trigger)` orchestrating the steps below. Runs **in the arq worker only** (never in the web process).
  - `transcript.py` — production `TranscriptStore` over the messages repository (the protocol in `context.py` has no implementation today).
  - `summariser.py` — production `Summariser` issuing the R9.10 compact call through the agent's own key group via K.1.
  - `tool_registry.py` — per-turn tool table assembled from: `update_wakeup` (R15.06, exists in `wakeup_service.py`), `web_search` (R12.07, exists), `file` (exists), `code_exec` (R12.05, exists; needs K.5), bound MCP server tools (R12.01; needs K.5), `load_prompt_section` (R9.06, exists in `prompt_loader.py`). Every entry currently has zero callers — this registry is their first.
- Turn algorithm:
  1. Acquire per-`(agent_id, chatroom_id)` Redis lock — one concurrent turn per agent per room; a trigger landing during a turn coalesces into at most one queued follow-up.
  2. Load agent row; resolve key group; resolve prompt per `prompt_strategy` (R9.04–R9.08; `lazy` falls back to `full` with a UI warning when the provider lacks tool use, R9.08).
  3. Assemble history per `context_mode` (R9.09 `general` / R9.10 `compact` via `should_compact` → `run_compact`; compaction failure keeps original context and audits, R9.11).
  4. If `rag_config_id` set: run `RetrieveService` (embed → Qdrant → optional rerank → hydrate) and inject as a context block; GraphRAG configs likewise via `GraphRagRetrieveService` (both currently caller-less).
  5. Stream the provider call through K.1; on first token emit `agent.thinking` → `agent.token` deltas on the chatroom channel (R13.19); execute tool-call rounds through the registry (each MCP/tool call audited, R12.02/R12.15).
  6. Persist the reply via a new `MessageService.send_agent(...)` surface (constructs `SenderType.AGENT`, publishes `message.created`, bypasses the user-only validation paths but keeps sanitization-relevant invariants); emit `agent.finished`.
  7. On error: emit `agent.finished` with error payload, audit `agent.turn_failed`, never leave the room in "thinking" state.
- Sub-agent turns: `subagent_service` spawn path (logic exists) invokes `run_turn` with depth/concurrency caps already implemented; `parent_agent_id` flows into the K.1 `ProviderRequest`.
- Wire `POST /api/chatrooms/{id}/compact` (`chatrooms.py:405-423`, currently a documented no-op returning 202) to enqueue a real compaction job against the room's active agent.

**Key IDs.** `[R9.04]`–`[R9.11]`, `[R10.07]`–`[R10.09]`, `[R12.01]`–`[R12.16]`, `[R13.19]`, `[R15.06]`–`[R15.08]`, `[R15.22]`.

**Exit criteria.** Integration (FakeAdapter, real Postgres/Redis via compose.test.yml — **not** mocked at the service boundary): user message → agent reply persisted with `sender_type=AGENT` and streamed events observed on a live WS client. Compaction triggers at cap and replaces range. Lazy-prompt section load round-trip. RAG block present when configured. Lock prevents concurrent double-turns. Tool round executes `update_wakeup` with clamp audit.

## K.3 Trigger → turn → reply wiring — **CODE** — M

Closes gap 1 (wiring). Three severed links: (a) `MessageService.send` never notifies orchestration — `OrchestrationFacade.on_message_created` (`facade.py:119-126`) has zero callers, so `every_n_messages` (R15.01) can never fire; (b) the `wakeup_agent` arq task (`workers/tasks/orchestration.py:23-59`) is an audit-only no-op whose docstring defers to a runtime that now exists (K.2); (c) `a2a_handler.py:36-46` answers every A2A `call` with a hardcoded "agent runtime unavailable" error, which also makes the workflow `agent_invocation` executor fail unconditionally.

**Deliverables.**

- `MessageService.send` (post-commit) invokes `OrchestrationFacade.on_message_created`; presence transitions invoke `on_presence_changed`; agent replies invoke `on_agent_message_sent` (autostop round counting, R15.03/R15.04). The wake-up evaluator (`wakeup_service.py:60-155`) is already correct — it only needs callers.
- `wakeup_agent` task body: evaluate guards (room still exists, agent not deleted, autostop not tripped) → `TurnEngine.run_turn(trigger="wakeup")`. Keep the existing audit event, now emitted *after* a real turn.
- `a2a_handler.py`: `call` → run a turn with the envelope payload as input, reply with `correlation_id` (R9.15); `instruct` → run turn and drive `InstructService.mark_delivered / mark_completed / timeout` (all currently caller-less); `notify` → inject as context for the agent's next turn (no immediate turn).
- Approval participation: when an approval gate opens, approver agents receive A2A `notify`; their next turn exposes a `cast_approval_vote` tool wired to `ApprovalService.cast_vote` (currently caller-less); `handle_timeout` scheduled as an arq deferred job at gate creation. Resolution publishes the existing WS events **and** resumes the workflow (K.4).
- Frontend requires no changes for the happy path: `useChatroomSocket` already handles `agent.thinking/token/finished` (`useChatroomSocket.ts:76-79`) — these events simply exist now.

**Key IDs.** `[R9.12]`–`[R9.17]`, `[R15.01]`–`[R15.05b]`, `[R15.11]`, `[R15.23]`.

**Exit criteria.** Compose-backed integration: (1) `every_n_messages n=2` — second user message produces an agent reply; (2) silence trigger fires a real turn; (3) A2A `call` between two agents returns a reply envelope within timeout; (4) instruct chain depth-2 completes with `mark_completed`; (5) three-agent majority approval resolves and the run resumes. The 30 s `evaluate_silence` cron no longer produces phantom `wakeup.fired` audit rows with no effect.

## K.4 Workflow engine remediation — **CODE** — M

Closes gap 3. Four independent defects, all confirmed at file level:

1. **Executor registry**: commit `ff19610` (automated lint cleanup) deleted 10 of 11 side-effect imports from `executors/registry.py:32-34`; only `trigger` registers. Every workflow fails with `ValueError("No executor registered…")` at node 2. No test calls `get_executor` for non-trigger types.
2. **`approval_gate` constructor**: `executors/approval_gate.py:37-42` passes `approver_agent_ids=` but the dataclass field is `approvers` (`orchestration/domain/models.py:241-248`) — guaranteed `TypeError`, hidden by `# type: ignore[call-arg]` and swallowed into a FAILED/timeout exit.
3. **Cron trigger FK violation**: `workflow_cron_scheduler` (`workers/tasks/workflow.py:265-268`) calls `trigger_run` without `project_id`; the `UUID(int=0)` default (`workflow_service.py:229`) violates the `workflow_runs.project_id` FK — every cron-triggered run fails at insert.
4. **No resume paths**: approval resolution never calls `resume_at_port("approved"/"rejected")`; no dispatcher consumes `wf:wait:by_event:*` (the `wait_for_event` executor documents a protocol nobody implements); instruct nodes with `wait_for_completion=True` (the default, `executors/instruct.py:47-54`) park forever. Only timeouts resume today. `run_max_seconds` / `idle_max_seconds` (`models.py:190-196`) are declared and never enforced.

**Deliverables.**

- Restore all 11 executor imports in `registry.py` with a `# noqa: F401  -- side-effect registration` guard comment, plus a completeness unit test: `for node_type in NodeType: assert get_executor(node_type)` — this test is the reason the regression can never recur.
- Fix `approval_gate.py` field name; **delete the `type: ignore`**. Sweep the repo for other `# type: ignore[call-arg]` occurrences and resolve each (this pattern hid a guaranteed-crash bug from mypy).
- Cron scheduler resolves `project_id` via the workflow's workspace before `trigger_run`; backfill test.
- Resume wiring: approval resolution → `resume_at_port` (with K.3's vote path); event dispatcher task subscribing the internal event bus and matching `wf:wait:by_event:*` keys; instruct completion (K.3 `mark_completed`) → resume. Watchdog cron enforcing `run_max_seconds` / `idle_max_seconds` → fail run with explicit state reason.
- Wire the three dormant trigger kinds (`message_received`, `a2a_event`, `wakeup_signal` — enum-only today): dispatch checks at `MessageService.send` (post-commit, alongside K.3), the A2A consumer, and the wakeup path.
- Documentation correction in the same PR: `docs/implement/00-overview.md` §0.8 rows G/H currently claim REST mutations (`/wake-up`, `/refresh`, `/approve`) that were never built and an executor list that does not match the schema. Rewrite the rows to match reality (mutations flow through the engine + A2A; actual node-type list).

**Key IDs.** `[R14.06]` (linter/editor contract untouched), §14.3 execution, `[R15.11]`.

**Exit criteria.** Compose-backed: a workflow `trigger → condition → agent_invocation → approval_gate → end` runs to completion with a FakeAdapter agent and a majority vote; cron workflow fires within one minute and inserts a valid run; `wait_for_event` resumes on a published event; parked run killed by `run_max_seconds` watchdog. Executor completeness test green.

## K.5 Sandbox images & in-image driver — **CODE** — M

Closes gap 4. `docker_runsc.py:44-45` references `smap/mcp-runtime:pinned` and `smap/code-exec:pinned`; neither image has a Dockerfile in the repo, and the JSON-over-stdout driver protocol the host side expects (probe / invoke / file ops / code_exec) has no source anywhere. Every sandbox spawn today raises `ImageNotFound`. The host-side machinery (gVisor flags, runtime assertion, internal-only network, resource caps, egress proxy with HMAC + IP screening) is real and audited — only the guest side is missing.

**Deliverables.**

- `deploy/sandbox/mcp-runtime/Dockerfile` — base `node:22-slim` + `python:3.12` tooling (`npx`, `uvx`) for stdio MCP servers, non-root user, no package managers in final layer beyond what MCP servers need.
- `deploy/sandbox/code-exec/Dockerfile` — `python:3.12-slim` + curated scientific set (R12.05), non-root, read-only rootfs compatible.
- `deploy/sandbox/driver/` — the in-image entrypoint implementing the wire protocol `docker_runsc.py` already speaks: read one JSON command on argv/stdin (`probe` / `invoke` / `file_op` / `exec`), set `HTTP_PROXY`/`HTTPS_PROXY` to the egress proxy, run the MCP server or code, print one JSON result on stdout. Keep the protocol exactly as the host side expects — the host is the contract, the driver conforms.
- Build + pin: CI job builds both images, smoke-runs the driver (`probe` against a known stdio MCP server fixture), and records digests; `docker_runsc.py` image references move to digest pins supplied via settings (`SANDBOX_MCP_IMAGE`, `SANDBOX_CODE_EXEC_IMAGE`), defaulting to the tags for dev.
- Compose: build entries for both images in `docker-compose.yml` (profile `sandbox-build`) so a self-hosting operator can `docker compose build` them; `deploy/README.md` gains the build step.
- Wire the orphaned factories into K.2's tool registry: `egress_proxy_client_from_settings` (`egress_client.py:109`) and `build_registry()` (`search_adapters/__init__.py:22`) get their first production callers.

**Key IDs.** `[R12.03]`–`[R12.06]`, `[R12.16]`.

**Exit criteria.** On a Linux host with gVisor: `POST /api/agents/{id}/mcp/{mcp_id}/test` succeeds against a real stdio MCP server; `code_exec` runs a Python snippet and returns stdout; egress from inside the sandbox to a non-allowlisted host is blocked (curl metadata IP fails), allowlisted host succeeds via proxy. CI builds both images green.

## K.6 Email delivery & registration completion — **CODE** — M

Closes gap 5. The only `EmailSender` is `LoggingEmailSender` (`shared_kernel/email/email.py:34-43`); `factory.py:23` admits "Phase I replaces with real SMTP" — it never did. Consequences: verification (R6.02), password reset (R6.05), and invite mails (R6.09–R6.11) are undeliverable; the invite plaintext token is generated, hashed, and then **discarded by both routers** (`orgs.py:353-361`, `projects.py:351-354`) — a write-only column. Separately, the backend CAPTCHA verifier (`shared_kernel/auth/captcha.py`, real hCaptcha/Turnstile siteverify) has **no frontend counterpart**: `RegisterView.vue:61-64` is a bare text input, so with CAPTCHA enabled nobody can register through the UI.

**Deliverables.**

- `SmtpEmailSender` (aiosmtplib): STARTTLS/implicit-TLS, settings `smtp_host / smtp_port / smtp_from / smtp_tls_mode`; credentials from Vault KV (`secret/smap/config/smtp`), **not** env. Factory selects SMTP when `smtp_host` is set; in `env=prod` without SMTP configured, log a **startup warning** (registration will be dead) — fail-open to logging sender stays, because self-hosted operators may genuinely run mail-less in a closed lab.
- Templates (plain text + minimal HTML, i18n-ready keys but en-only per v1): verify-email, password-reset, email-change reverify, org/project invite.
- Wire the senders: `auth_service` verify/reset/change-email paths; `InviteService` composes the invite mail carrying the plaintext token link (R6.09 flow: link lands on sign-up for unregistered invitees); `accept` keeps the current logged-in-email match **and** honors the token link path. The token stops being dead code.
- Frontend CAPTCHA: real hCaptcha / Turnstile widget component in `RegisterView` (provider + sitekey served by a new `GET /api/auth/captcha-config`; renders nothing when mode=off). Remove the token paste box. Remove the phantom `captcha_token` from the login payload (`session.ts:25-27`) — backend login takes none (R19a.12 is register-only).
- Ops: `.env.example`, `docs/operations.md` SMTP runbook section, `docs/release-checklist.md` gains an SMTP smoke item (send + receive verification mail on staging).

**Key IDs.** `[R6.01]`, `[R6.02]`, `[R6.05]`, `[R6.06]`, `[R6.09]`–`[R6.11]`, `[R19a.12]`.

**Exit criteria.** Compose-backed with MailHog (test overlay): register → mail arrives → link verifies → login; reset round-trip; invite mail to unregistered address → sign-up → auto-enroll. UI registration with Turnstile test keys passes; with mode=off no widget renders. Prod-mode boot without SMTP logs the warning exactly once.

## K.7 Wiring tripwires — **TEST** — M

The audit's root-cause finding: 451 good unit tests, all mocked at service boundaries, so **no test ever asked the cross-context question** ("does a message produce a reply?"), and a lint commit could sever the executor registry without a single failure. This step adds the minimum permanent guard set — not a test-strategy overhaul (that remains J.∞).

**Deliverables.**

- A `wiring` test tier (real Postgres + Redis from `compose.test.yml`, FakeAdapter for LLM, no Vault/Qdrant/Neo4j requirement) run as a CI job:
  1. message → agent reply (K.2/K.3 exit test, kept forever);
  2. executor completeness (K.4);
  3. workflow golden run (K.4 exit test);
  4. A2A call round-trip (K.3);
  5. usage event written per provider call (K.1);
  6. email round-trip via MailHog (K.6).
- Fix the marker no-op: the 8 `tests/integration/` files get real `@pytest.mark.integration` markers so `-m "not integration"` actually filters something; the new tier uses `@pytest.mark.wiring`.
- Registry/orphan tripwire: an import-linter contract (or pytest collection check) asserting that every module under `contexts/*/application/executors/` is imported by its registry — the generic form of the `ff19610` regression.

**Key IDs.** Cross-cutting; cites the IDs of each pinned behavior in test docstrings per §0.3.

**Exit criteria.** New CI job green and **required** (not `continue-on-error`); deleting any executor import or the `on_message_created` call fails CI.

---

## K.∞ Phase gate

- [ ] Live compose stack: user message in an agent-bound room → streamed `agent.thinking/token/finished` → persisted agent reply (real provider key, one manual run).
- [ ] `key_usage_events` rows present after the above; threshold notification fires at 80% on a test cap.
- [ ] `HttpChatCompleter` deleted; no `unwrap_api_key_plaintext` caller bypasses the router for LLM/embed/rerank traffic.
- [ ] Workflow with all node types passing a compose-backed golden run; cron trigger inserts valid runs; parked nodes resume on approval / event / instruct completion.
- [ ] `docker compose build` produces both sandbox images; MCP test endpoint and `code_exec` succeed under gVisor on a Linux host; sandbox egress to non-allowlisted hosts blocked.
- [ ] Register / verify / reset / invite mails delivered via SMTP on the test overlay (MailHog); CAPTCHA widget functional with Turnstile test keys.
- [ ] `wiring` CI tier green and required.
- [ ] §0.8 rows E/G/H corrected to reflect actual endpoint paths and node-type list; row K added with close date.

## Cross-cutting checklist

1. **AuthZ tap.** Turn engine re-validates agent↔room binding and key-group project scope at turn start (defends against unbind-during-turn). A2A keeps the R9.17 scope check.
2. **Audit tap.** New events: `agent.turn_started/finished/failed`, `agent.compact_run`, `email.sent` (template + recipient hash, no address plaintext), `workflow.resumed`. MCP/tool calls keep R12.02/R12.15.
3. **Rate-limit buckets.** New: `agent-turn` (per agent per room), `email-send` (per recipient hash, mailbomb guard already exists for verification — extend to invites).
4. **Observability.** Metrics: `agent_turns_total{result}`, `agent_turn_duration_seconds`, `provider_calls_total{provider,status}`, `agent_stream_tokens_total`, `workflow_resumes_total`, `emails_sent_total{template,result}`. Grafana dashboard gains an "Agent runtime" row.
5. **RFC 7807.** New problem types: `agent-turn-failed`, `provider-exhausted` (surfaces D.6 exhaustion to chat UI), `sandbox-image-unavailable`, `smtp-unconfigured`.
6. **Migration policy.** No new tables expected; if turn bookkeeping needs one, follow N-1 compatibility. `0030+` numbering.
7. **Secrets.** SMTP credentials in Vault KV; sandbox images carry no secrets; adapter error scrubbing verified by test (K.1).

## Risks

- **Streaming through the router weakens rotation guarantees.** Mid-stream provider failures cannot rotate keys without replaying partial output. Decision: rotate only before first token; after first token the turn fails visibly (R9.09 surfaces provider errors to UI). Documented in adapter contract.
- **Turn storms.** `every_n_messages n=1` plus multi-agent rooms can fan out turns. Mitigations already exist (wake-up clamps R15.07, sub-agent caps, D.6 exhaustion, per-agent turn lock); add the `agent-turn` rate bucket before enabling K.3 wiring.
- **gVisor requires Linux.** K.5's exit criteria cannot run on the Windows dev host; CI (Linux runners) and the staging box are the verification environments. Do not fake this with `runtime: runc` — the runtime assertion (SEC fix) will correctly refuse.
- **Scope creep toward the deferred gap list.** K is done when the five gaps are closed, not when the audit list is empty. The deferred items get their own phase after J.∞ scopes are re-validated on a working product.
- **Doc-trust erosion.** This phase exists because §0.8 asserted completeness that file-level reading disproved. Rule for K: a sub-step may be marked complete **only** with its compose-backed exit test green in CI — no mock-only closes.
