---
type: audit
status: draft
created: 2026-07-22
requirements: [R9.09, R11.23, R12.16, R13.19, R15.01, R15.16, R15.22]
---

# Audit: AI Agent configuration to runtime

## 1. Scope

- **Area** — the full "what the configurator sets" to "what the runtime does" chain, in four
  blocks: (a) agent-level settings (persona, model, sampling, context budget, wake-up);
  (b) capabilities (built-in tools, MCP bindings, local functions, skills, egress);
  (c) knowledge binding (RAG and Knowledge Map attachment, allowlists, retrieval); and
  (d) group/orchestration inheritance (multi-agent rooms, workflow nodes, A2A, approvals).
  Backend `contexts/{agents,knowledge,skills,agent_groups,orchestration,keys,workflow}`,
  `app/api/v1/`, `app/api/ws/`, `app/workers/`, `services/{egress_proxy,mcp_supervisor}`,
  and the frontend `slices/agents` configuration surfaces.

- **Intent sources** — the user set the bar at **blocking functional defects**, not
  documentation conformance. Findings are therefore judged primarily against internal
  consistency (call site vs. implementation, write path vs. read path, comment vs. code)
  and against observable user-facing breakage. `REQUIREMENTS.md` `[Rxx.yy]` entries,
  approved dossiers under `docs/tasks/`, and `docs/implement/` notes were used as
  corroborating intent where a finding turned on "which behavior is correct" — the
  `requirements` list above names only those actually relied on. Several candidates were
  refuted precisely because a spec or dossier documented the behavior as deliberate
  (see §4).

- **Depth** — thorough. Nine investigation lenses run read-only in parallel (configuration
  fidelity; capability enablement; knowledge binding; group/orchestration inheritance; turn
  state and lifecycle; frontend round-trip; tenant isolation; concurrency and event flow;
  plus one follow-up lens to close a coverage gap the isolation lens self-reported). These
  produced 46 candidates. Every candidate then went through an independent adversarial
  verification pass whose explicit instruction was to **refute**, defaulting to refuted when
  uncertain: 12 verification agents, grouped by code area. 32 candidates survived, 14 were
  refuted. Verification also corrected severity or scope on 9 of the survivors, in both
  directions.

## 2. Coverage

**Read closely.** `contexts/agents/` in full (`runtime/turn_engine.py`,
`runtime/tool_registry.py`, `runtime/builtin_tools.py`, `runtime/transcript.py`,
`runtime/summariser.py`, `application/tools/`, `application/agent_service.py`,
`infrastructure/`); `contexts/orchestration/` (wakeup, instruct, subagent, approval, A2A);
`contexts/keys/` provider router, group repository, and the three chat adapters;
`contexts/knowledge/` binding and retrieval paths; `contexts/workflow/` executors that
invoke agents; `services/egress_proxy/`; the `slices/agents` frontend views, composables and
shared form controls; and the alembic migration history where a constraint or backfill
decided reachability.

**Verified clean, with evidence.** Provider key resolution at turn time (the carry join in
`group_repository.py:147-187` re-reads per call, so revocation and carry-withdrawal take
effect immediately). Worker-side key-group scoping on the knowledge build/ingest paths
(`graphrag_builder.py:240-253` runs the same preflight the turn engine runs, and Knowledge
Map inherits it via the shared builder; no code anywhere mutates `key_groups.project_id`, so
a stale cross-project binding is unreachable). Per-turn tool registry construction and
closure lifetime — no cross-agent or cross-turn tool leakage. Agent PATCH null-vs-omitted
semantics (`exclude_unset` plus explicit `clear_*` sentinels; `temperature: 0`,
`a2a_enabled: false` and an emptied system prompt all persist). The sampling chain
end-to-end. The observer-agent leak class from
`docs/audits/2026-07-03-observer-agents-audit/` — re-verified as still holding, including
the F-2 role-blind presence gate fix.

**Sampled, not exhaustive.** `app/workers/` beyond the knowledge and orchestration tasks.
`services/mcp_supervisor/` (the sandbox lifecycle was read only where it decided a finding).
`contexts/{activities,audit,identity,notification,tenancy,prompt_studio}` were entered only
where the agent-runtime chain passed through them; they were not audited as areas.

**Not covered.** Structural quality (route to `check-quality`). Vulnerabilities as such
(route to `check-security`) — one candidate, the egress proxy's block-on-any-resolved-IP
posture, was deliberately reclassified there rather than reported here, since
`ip_policy.py:66-69` documents it as an anti-rebinding decision. Diff-level review
(`/code-review`). Provider API behavior could only be reasoned about from the adapters and
from external knowledge, not executed — this bounds F-16 and was decisive in refuting one
candidate (§4, L2). Nothing in this audit was verified by running the system; every finding
is a static trace.

## 3. Findings

Ordered by severity. Never renumber — F-n identifiers are cited from spec dossiers.

## F-1: `web_search` result cache is keyed globally, serving one project's results to another

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/tools/web_search.py:49-52`
  (`_cache_key` = provider, query, top_k, locale, freshness — no `project_id`, no `key.id`,
  no `key.config`), read at `:125-130`, written at `:160`, per-key config forwarded to the
  adapter at `:153`; `backend/contexts/agents/infrastructure/search_cache.py:63,76` (bare
  global Redis keys); `backend/shared_kernel/auth/clients.py:35-50` (`get_redis()` — one
  process-wide client, no key prefix, no per-tenant pool or DB index);
  `backend/contexts/agents/infrastructure/search_adapters/google_cse.py:59-67` (`cx` selects
  the entire corpus); contrast the neighbouring
  `backend/contexts/agents/infrastructure/search_rate_limiter.py:24`
  (`f"search:rl:{project_id}:{window}"` — the same author scoped this one).
- **Failure scenario**: projects A and B each hold a `google_cse` key. A's `cx` is restricted
  to `internal-wiki.acme.com`; B's searches the open web. An agent in A searches
  `"Q4 roadmap"` — results are cached under `search:sha256("google_cse|q4 roadmap|5|en-US|any")`.
  Within `_CACHE_TTL_S = 600` an agent in B issues the same query, hits the cache at `:126`,
  and receives A's internal-wiki results verbatim. B's `cx` is never consulted and B's key is
  never unwrapped. The audit record is written as `source: "cache"` (`:129`), so nothing
  marks the result as foreign.
- **Blast radius**: every project pair sharing a search provider — the common case. Three
  distinct breakages: wrong corpus queried; quota and cost misattribution (the cache is
  checked *before* the rate limiter by design at `:122-124`, so B's search is served off
  egress A paid for and consumes none of B's quota); and non-determinism, since identical
  configuration yields different results depending on which tenant warmed the cache first.
- **Intent source**: internal inconsistency with `search_rate_limiter.py:24`. No `[Rxx.yy]`
  governs the cache key.
- **Note**: independently surfaced by two lenses (capability enablement, tenant isolation)
  and confirmed by a verification pass that found no namespace at any layer. This is also a
  cross-tenant data exposure; it is reported here as a correctness defect, but it warrants a
  `check-security` referral in parallel.

## F-2: `model_hint` and `model_id` do not constrain routing — the agent silently runs on another provider and another model

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/keys/application/provider_router.py:736-754`
  (`_load_eligible` filters only on carry and `capability not in _CAPS[key.provider]`),
  `:76-87` (`ProviderRequest` carries no provider preference), `:348-377` (`call`) and
  `:416-432` (`call_stream` — first serviceable member in priority order wins);
  `backend/contexts/agents/application/runtime/turn_engine.py:235-240` (`_resolve_models`
  overrides only `models[agent.model_hint.value]`, leaving the other two at
  `_DEFAULT_CHAT_MODELS`), `:2655`, `:2732` (the payload ships the `models` map, not a single
  `model`); `backend/contexts/keys/infrastructure/adapters/base.py:96-110` (`resolve_model`
  reads `models[provider.value]` for whichever provider won);
  `backend/contexts/keys/application/group_service.py:151-152` and
  `backend/app/api/v1/key_groups.py:56-59` (mixed-provider groups are first-class);
  `backend/contexts/agents/application/agent_service.py:227-232` (create/patch assert only
  that the group holds *at least one* carried key of the hinted provider, and never re-check
  at turn time). Context-budget half: `turn_engine.py:196-203` (`_context_limit_for` keys
  strictly off `model_hint`), `:220`.
- **Failure scenario**: key group G holds an OpenAI key at priority 1 and a Claude key at
  priority 2. Agent A is configured `model_hint=claude`, `model_id=claude-opus-4-8`,
  `key_group_id=G`. Create-time validation passes. Every turn thereafter, `_load_eligible`
  returns [OpenAI, Claude], `call_stream` picks OpenAI, and `resolve_model` returns the
  hardcoded default `models["openai"]`. The agent runs on GPT-5.4, not Opus. Nothing in the
  UI shows a discrepancy. Compounding: the turn budgeted its context to
  `CONTEXT_LIMITS["claude"]` (200 000) before routing happened, so a long conversation hands
  a 200k-budgeted request to a 128k window; `classify_http` maps the resulting 400 to
  `RotationReason.ABORT` (`router_policy.py:26,55-56`), so `call_stream` raises
  `KeyGroupExhausted(reason="request_rejected")` **without** trying the Claude sibling.
  Third variant, no misconfiguration needed: a 429 on the hinted provider's key rotates
  mid-conversation and the agent silently changes model between turns.
- **Blast radius**: every agent whose key group carries more than one chat provider, plus
  any agent whose hinted provider's key is later withdrawn from the group — the create-time
  invariant rots with no runtime re-check. Affects model selection, `effort` semantics,
  context budgeting and compaction correctness.
- **Intent source**: `docs/implement/K-agent-runtime.md:49` — "model ID comes from the
  agent's configured model, never hardcoded".

## F-3: `subagent_spawn` workflow nodes can never complete — every run parks for the full timeout, then fails

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/workflow/application/executors/subagent_spawn.py:79`
  (`config.get("wait_for_all", True)` — default True), `:82` (writes
  `wf:subagent_callback:{instance_id}`), `:79-107` (parks with
  `timeout_task="workflow_subagent_timeout"`, default 3600s). The only reader of that key is
  `backend/contexts/orchestration/application/subagent_service.py:217-251`
  (`_fire_workflow_callback`), called only from `destroy` at `:215`, whose only entry point
  is `backend/contexts/orchestration/interfaces/facade.py:319-325`. **A repo-wide search for
  `destroy_subagent` across `backend/`, `frontend/`, `deploy/`, `docs/` and `openapi.json`
  returns exactly one hit: that facade definition.** No API route, no WS handler, no arq
  registration, no frontend client method, no test. Nothing runs a turn for a spawned
  instance either — searching `spawn|subagent|instance_id` across
  `contexts/agents/application/runtime/` returns zero matches, and there is no
  `spawn_subagent` built-in tool. Timeout path:
  `backend/app/workers/tasks/workflow_steps.py:78-99` calls
  `engine.force_fail(run_id, reason="subagent_timeout")`.
- **Failure scenario**: author a workflow containing a `subagent_spawn` node with default
  config. The node creates an `agent_instances` row, arms the callback key, and parks. No
  code path ever destroys the instance, so `workflow_subagent_complete` is never enqueued.
  One hour later the timeout fires and force-fails the whole run. The node's `success` port
  is unreachable; no subagent work is ever performed.
- **Blast radius**: every workflow using `subagent_spawn` — the entire G.8 feature. Secondary
  effect: `count_alive_children` (`repositories.py:537-546`) counts `destroyed_at IS NULL`,
  which never transitions, so `max_alive_simultaneously` (default 3) behaves as a *lifetime*
  spawn cap per workflow run rather than a concurrency cap, and the 4th spawn raises
  `SubagentConcurrencyExceeded`.
- **Intent source**: `[R15.18]`–`[R15.23]`; `docs/implement/G-orchestration.md:183` describes
  the node as creating the row "**and hydrates a short-lived runtime**" — the hydration half
  is unimplemented.
- **Note**: the `[R15.22]` inheritance matrix
  (`contexts/orchestration/domain/models.py:356-370`) is unenforceable for the same root
  cause — no runtime consumes the inherited context built by `_build_inherited_context`.
  State this narrowly: the *synthetic root's* `run_context` keys are read (`repositories.py:465,494`,
  `retention.py:508-511`); it is the inherited agent configuration that has no reader.
  A cleanup sweep does exist (`backend/app/workers/tasks/retention.py:488-538`) but deletes
  rows with raw SQL without firing the callback, and its own docstring concedes that
  "neither the synthetic root nor its workflow-spawned children are ever destroyed".

## F-4: the workflow `instruct` node is rejected by the A2A scope check for every ordinary agent pair

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/orchestration/application/instruct_service.py:140-156`
  (builds the envelope with `from_agent=issuer_agent_id` and `workflow_run_id=None`, then
  calls `self._a2a.send(envelope=envelope)` with **no keyword arguments** — no
  `caller_invocation_context_id`, no `callee_attached_context_ids`);
  `backend/contexts/orchestration/application/a2a_service.py:88-97` (non-None `from_agent`
  takes the agent-to-agent branch; `_enforce_scope` receives
  `callee_attached_context_ids or frozenset()`);
  `backend/contexts/agents/application/a2a_scope.py:72-111` — read end to end, `evaluate` has
  exactly four early returns before the shared-context test (cross-project, callee project
  soft-deleted, self-invocation at `:90`, and the two `a2a_enabled` checks). There is no
  workflow-origin, system-actor or trusted-caller branch. For two ordinary distinct agents
  the flow reaches `:98`, `shared_context` is False, and the verdict falls through to
  `is_call_only_enabled(callee)` → `allowed=False`. Executor:
  `backend/contexts/workflow/application/executors/instruct.py:39-43`, failure port
  `:94-100`.
- **Failure scenario**: a workflow `instruct` node from agent A to agent B, both
  `a2a_enabled`, both bound to the same chatroom — i.e. they genuinely share a context.
  `send` raises `A2AForbidden`; the executor's broad `except Exception` routes the node to
  the `failure` port with "a2a denied". The instruct succeeds only if B has
  `wakeup_config.triggers.call_only.enabled = true`.
- **Blast radius**: the entire G.7 instruct-via-workflow feature.
- **Intent source**: internal inconsistency — the sibling executor
  `backend/contexts/workflow/application/executors/agent_invocation.py:41-47` passes
  `from_agent_id=None` and therefore takes the *trusted workflow* branch
  (`a2a_service.py:98-100`). Two workflow-originated agent invocations are authorized by two
  incompatible rules.
- **Why this survived**: every test touching instruct stubs the scope check out —
  `backend/tests/unit/test_orchestration_services.py:148` replaces `svc._a2a` with a mock and
  `:447` asserts only that `send` was awaited;
  `backend/tests/unit/test_workflow_executors.py:420-501` mocks `issue_instruct` entirely.
  No integration or wiring test exercises a real workflow instruct between two ordinary
  agents.
- **Sibling instance, same class, different fix**:
  `backend/contexts/orchestration/application/a2a_service.py:316-321` passes
  `callee_attached_context_ids=frozenset()` unconditionally inside the per-recipient loop, so
  `shared_context` is structurally always False and A2A broadcast reaches only `call_only`
  agents. This contradicts `contexts/orchestration/domain/models.py:33-36`, which documents
  broadcast as writing to "one inbox stream per a2a-enabled agent in the project". Here the
  caller id *is* threaded through, so the defect is the hardcoded empty frozenset, not a
  missing argument.

## F-5: one agent's `context_mode=compact` permanently truncates every other agent's history in the same room

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:2506-2523` (the
  compaction decision), `:2514-2520` (read from `agent.context_mode` /
  `agent.context_token_cap`), `:2552` (the write target is
  `MessagesTranscriptStore(self._db, chatroom_id=chatroom_id)` — a room-level resource),
  `:2546-2551` (the summary is produced by the acting agent's summariser against the acting
  agent's `key_group_id`); `backend/contexts/agents/application/runtime/transcript.py:138-143`
  (`load_model_history(db, *, chatroom_id, window)` — **no agent parameter, no mode, no
  scoping hook**), `:150-157` (unions `compacted_ids` from every summary row in the window),
  `:164` (applies the set to all readers), `:182-191` (the row is written
  `sender_type=SYSTEM, sender_id=None` — the producing agent is not recorded, so no reader
  could filter by it even if it wanted to);
  `backend/contexts/agents/infrastructure/tables.py:49-52` and
  `backend/app/api/v1/agents.py:82,116` (`context_mode` is a per-agent column, independently
  settable).
- **Failure scenario**: room R holds agent A (`context_mode=compact`, cap 20k, cheap model)
  and agent B (`context_mode=general`, large-context model). A's turn crosses its cap,
  acquires `compact:lock:{room}`, folds the oldest range into a `compact_summary` row naming
  ids `[m1..mN]`, and commits. B's next turn calls `load_model_history(db, chatroom_id=R)`,
  collects those ids, and drops `m1..mN` from its own history. B now receives a summary
  produced by A's model and billed to A's key group, instead of the raw history it is
  configured for. Permanent: the summary row is never removed, so every subsequent B turn
  elides them too.
- **Blast radius**: every multi-agent room with heterogeneous `context_mode` or
  `context_token_cap` — the normal configuration (a cheap summarizer beside a large-context
  analyst). Irreversible, since the fold is persisted.
- **Intent source**: `[R9.09]` (`docs/traceability.csv:62`) — "`context_mode = general`: the
  system sends the entire chat history (subject to the provider's hard context limit)". B is
  configured `general` and does not receive the entire history.
- **Corroborating**: `_request_ceiling` (`turn_engine.py:206-222`) *does* keep `general`
  agents on the provider limit for the knowledge budget, which makes the history path's
  missing per-mode gate look like an oversight rather than a design choice. The FIX-11
  comment at `:2524-2526` shows the authors knew two agents contend over one transcript —
  they solved the duplicate-summary race but never addressed whose configuration authorises
  the fold.

## F-6: a tool's DB failure poisons the turn session, and the completed reply is destroyed after the user has already seen it

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/tool_registry.py:176-179`
  (unconditional `except Exception`, comment "a tool failure must not abort the turn");
  tools are built over the turn's own session —
  `backend/contexts/agents/application/runtime/turn_engine.py:1837-1843`, `:952`; `registry.call`
  is invoked bare at `:2690` with **no savepoint**; the file's only three `begin_nested()`
  uses (`:2256`, `:2283`, `:2845`) are read-only best-effort lookups, each carrying a
  docstring stating that a plain rollback "would discard the whole transaction"; tokens
  stream to the room at `:2677`; persist and commit at `:2181-2189`; outer handler
  `:2220-2246`. The turn's prior commit at `:2104` happens before `_stream_with_tools`, so
  tool writes sit in a fresh transaction that only the reply commit closes.
- **Failure scenario**: the model calls an MCP tool.
  `_audit_tool_invoke` (`backend/contexts/agents/application/runtime/builtin_tools.py:527-548`,
  called at `:567`, `:584`, `:586`) inserts an `mcp.tool_invoked` audit row on the turn
  session via `audit.emit`, which uses the caller's session with no savepoint
  (`backend/shared_kernel/audit.py:115-136`) — and swallows its own exception at `:547`. An
  infra-level fault there leaves the session rollback-required while reporting *success* to
  the model. The model continues and produces a full reply, streamed token-by-token to the
  room. `MessageService.send_agent` at `:2181` then raises on the aborted transaction, the
  outer handler rolls back, and the user watches a complete answer appear and vanish,
  replaced by a failed turn. Provider spend is paid, nothing persists.
- **Blast radius**: any turn using a DB-writing tool; widest for MCP-bound agents, since the
  audit insert fires on *every* MCP invocation and bypasses even the registry's catch-all.
- **Intent source**: internal inconsistency with the engine's own savepoint discipline at
  `:2256`.
- **Refuted sub-claim**: the originally-cited trigger (an `AgentVersionMismatch` race in
  `update_wakeup`) does **not** poison the session —
  `backend/contexts/agents/infrastructure/repositories.py:274-279` raises it from a
  `row is None` control-flow check after a successful `execute`, so it propagates cleanly and
  the turn completes normally.

## F-7: a summarisation returning HTTP 200 with empty text permanently deletes the folded history

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/summariser.py:57-59`
  (`RouterSummariser.summarise` checks only `http_status != 200`, then returns
  `str(result.body.get("text", ""))` with no emptiness check);
  `backend/contexts/agents/application/context.py:257-266` (`run_compact` passes it straight
  to `replace_range_with_summary`);
  `backend/contexts/agents/application/runtime/transcript.py:182-192` (writes it verbatim),
  `:159-166` (the range is elided from the model-facing view for good);
  `backend/contexts/agents/application/runtime/turn_engine.py:1883-1887` (renders
  `"[Earlier conversation summary]\n"` with nothing after it), `:2562` (`CompactFailed`, the
  designed failure path, is never reached because nothing raised). Reachability:
  `backend/contexts/keys/infrastructure/adapters/anthropic.py:194,297` both build
  `"text": "".join(text_parts)`, so a response with no text block — truncation at the
  summariser's `max_tokens` with only thinking blocks, a refusal, or an empty content array —
  normalises to `""` at status 200, and `adapters/base.py:48` confirms non-2xx never raises,
  so the status check is the only gate.
- **Failure scenario**: a compact-mode room crosses its cap. The summarisation call returns
  200 with empty content. A `compact_summary` row is written with `content_md=""` and
  `compacted_ids` naming the folded range. From then on the oldest part of the conversation
  is irrecoverably invisible to every agent in the room, replaced by a bare header. The audit
  records it as a successful `agent.compact_run`; nothing logs or flags it.
- **Blast radius**: any `compact`-mode room. Silent and cumulative — each subsequent
  compaction can fold another range away the same way.
- **Intent source**: internal inconsistency — the reply path guards exactly this case at
  `turn_engine.py:2117` (`if not final_text.strip()`, comment "never persist an empty agent
  message"). The summariser path has no equivalent.
- **Tests**: `backend/tests/unit/test_context_compaction.py:97-147` covers the success and
  summariser-raises cases only; `_FakeSummariser` never returns `""`.

## F-8: a turn killed by the arq job timeout skips every cleanup path

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/app/workers/main.py:310` (`job_timeout = 600` worker-wide;
  `wakeup_agent` at `:258` gets no override, unlike `graphrag_build`/`knowmap_build`/
  `agent_fs_gc` at `:286,299,303`); arq applies it via `asyncio.wait_for`, which cancels the
  inner task, so `run_turn` receives `asyncio.CancelledError`;
  `backend/contexts/agents/application/runtime/turn_engine.py:2220` is `except Exception` and
  `CancelledError` inherits `BaseException`. **There is no `finally`** — the `try` opened at
  `:1778` ends at the `except Exception` block, whose last statement is the `return` at
  `:2246`, immediately followed by `def _observer_memory_block` at `:2248`. No outer
  `BaseException` handler exists in `wakeup_agent`
  (`backend/app/workers/tasks/orchestration.py:72-181`). No reaper: the cron list
  (`main.py:313-332`) holds `workflow_watchdog` and `activities_watchdog`, nothing that
  resolves a stranded agent turn. Budget: `MAX_TOOL_ROUNDS = 8` (`turn_engine.py:96`) plus
  the final no-tools call at `:2749` is 9 provider calls, and
  `STREAM_TIMEOUT = httpx.Timeout(300.0, ...)` (`adapters/base.py:68`) is a per-read timeout,
  not a wall-clock cap.
- **Failure scenario**: a tool-heavy turn crosses 600s — two or three slow rounds suffice.
  The task is cancelled and none of the cleanup runs: no `agent.finished` emit (the room saw
  `agent.thinking` at `:1783` and never hears back), no `agent.turn_failed` audit, no
  `_requeue_notifications` (the queue was drained at `:1796`, so any queued approval request
  is lost), no `_restore_compact_flag`, and no post-release `_pop_queued_trigger` drain
  (`:628`) — so a user message that arrived mid-turn sits in `turn:queued:*` for its 3600s
  TTL and is never answered. The DB session is abandoned mid-transaction.
- **Blast radius**: every long turn. Silent loss of approval notifications and coalesced
  triggers; the only mitigation is the frontend's 120s spinner watchdog
  (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:24`), which hides the
  symptom without resolving the turn.
- **Intent source**: internal — the cleanup block exists and is simply unreachable on this
  path.
- **Correction to the original candidate**: the job does *not* dangle at the queue level.
  `asyncio.wait_for` raises `TimeoutError`, which on Python 3.12 is a builtin `Exception`
  subclass, so arq marks the job failed and finished on the first attempt. This also refutes
  the companion claim that `max_tries=5` replays the turn (see §4, L4) — the two were filed
  as compounding and do not compound. A timed-out turn is lost once, silently, rather than
  replayed five times.

## F-9: the egress allowlist is never seeded, so `web_search` fails on first use in every new project

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `REQUIREMENTS.md:628` `[R12.16]` verbatim: "The Proxy's allowlist is
  **seeded** with the four providers' documented hostnames (`api.search.brave.com`,
  `google.serper.dev`, `api.tavily.com`, `www.googleapis.com`)." Nothing seeds it: the only
  writers of `mcp_egress_allowlist` are
  `backend/contexts/agents/infrastructure/mcp_repositories.py` (upsert/delete/replace, called
  only from `EgressAllowlistService`) and `backend/app/api/v1/mcp.py:135,149,169,194`;
  `backend/alembic/versions/0014_mcp.py` does `create_table` + `create_index` only, with no
  `op.bulk_insert`, and is the sole migration touching the table across 0000–0056. No seeding
  in `backend/smap/` (CLI bootstrap), `deploy/`, or the tenancy project-creation path. No
  built-in default in the proxy either —
  `backend/services/egress_proxy/main.py:21-39` is a bare SELECT, and an empty table means
  denied. Meanwhile `hosted_web_search` is provisioned **enabled** on every agent create
  (`backend/contexts/agents/application/agent_service.py:449`, `:1044-1057`;
  `backend/contexts/agents/infrastructure/repositories.py:625`), and the search adapters do
  traverse the proxy (`search_adapters/tavily.py:68-74`, and siblings), which checks the
  allowlist at `backend/services/egress_proxy/app.py:287-302`.
- **Failure scenario**: new project → owner uploads a Tavily key → creates an agent →
  `web_search` is enabled out of the box → the model calls it → the proxy returns 403
  "host api.tavily.com not on project allowlist" → `tavily.py:75-78` raises → the model reads
  `"web_search failed: ..."`. The documented out-of-box experience does not work.
- **Blast radius**: every project that has not manually discovered the MCP egress-allowlist
  screen — i.e. every new project.
- **Intent source**: `[R12.16]`. This is the one finding where documented behavior and
  shipped behavior diverge outright.
- **Mitigating**: the error text names the host and the allowlist, so it is self-diagnosing;
  and a BYO search key must be configured first anyway, so allowlist setup can plausibly ride
  along with that flow. Note that `_function_warnings`
  (`backend/app/api/v1/agents.py:509-528`) proves the codebase already knows to warn about
  allowlist gaps — it just covers only `local_function`, never the search hosts or
  `hosted_mcp` bindings.

## F-10: a 3xx from a function tool is delivered to the model as a successful empty result

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/egress.py:38-40`
  (`ok` is `200 <= status < 400`, so 301/302/307/308 all read as success);
  `backend/services/egress_proxy/app.py:204` (`follow_redirects=False` — the only occurrence
  in the repo, with no env override or settings knob, so it is genuinely the deployed
  configuration), `:465-473` (the proxy *does* relay `Location`, which is not in the
  hop-by-hop strip list at `:383-396`); but the caller discards it —
  `backend/contexts/agents/application/egress.py:107-125` destructures
  `status, _headers, body` and `:126` builds an `EgressOutcome` that has no headers field;
  `backend/contexts/agents/application/runtime/builtin_tools.py:668-671` returns
  `ToolResult(content="HTTP 301\n", is_error=False)`. The configurator's Test button uses the
  same predicate (`backend/contexts/agents/application/agent_service.py:1005`).
- **Failure scenario**: a `local_function` `lookup_order` points at
  `https://api.partner.com/orders` (no trailing slash). The partner 301s to `/orders/`. The
  tool returns a *successful* result containing no data, and the model confabulates an order
  status from nothing. The configurator's Test button agrees the tool is healthy. Trailing-slash
  normalization, http→https upgrades and API version redirects all hit this.
- **Blast radius**: every `local_function` whose upstream redirects. Silent wrong answers
  rather than visible failures.
- **Intent source**: internal inconsistency — `follow_redirects=False` is correct (the target
  could leave the allowlist), which makes 3xx a *guaranteed* data-loss case that the `ok`
  predicate then misclassifies.
- **Mitigating**: the model does see the literal string `HTTP 301`, so a strong model may
  infer a redirect — but with `is_error=False` and `Location` dropped it has neither the flag
  nor the data to act on it.
- **Tests**: `backend/tests/unit/test_agents_egress_extraction.py:141` exercises 201 only.

## F-11: re-uploading or re-ingesting a document silently discards the submitted per-agent allowlist

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/knowledge/application/ingest_service.py:138-167` (the
  READY-dedup branch returns the existing document and the re-index branch calls
  `_index_document`; neither references `ipt.agent_ids`) versus `:180-189` (the fresh-insert
  branch, the only one that passes it); identical shape in
  `backend/contexts/knowledge/application/knowmap_ingest_service.py:102-117` vs `:131` and
  `backend/contexts/knowledge/application/rag_tus_finalizer.py:85-116` vs `:133`.
  `find_by_sha` (`backend/contexts/knowledge/infrastructure/repositories.py:189-200`) filters
  on `rag_config_id` + `sha256` only — no status, no `deleted_at` — so it matches FAILED and
  INGESTING rows. The API accepts and validates the list first
  (`backend/app/api/v1/rag.py:485-490,517-529`) and returns 201. The allowlist read path is
  `repositories.py:400-410`.
- **Failure scenario**: upload `policy.pdf` to RAG config X with the allowlist accidentally
  narrowed to agent A. Parsing fails on a transient embedder error, so the document lands
  FAILED. The designer re-uploads the same file, this time ticking both A and B.
  `find_by_sha` matches the FAILED row, the service takes the re-index branch, reuses the
  stored `agent_ids=[A]`, and returns 201. Agent B is bound to config X and sees the document
  listed in the UI, but `allowed_document_ids` never returns it — permanently, until someone
  uses the separate `PATCH /rag-documents/{id}/agents` endpoint. The same applies to a
  duplicate upload of an already-READY document with a corrected allowlist, and to the case
  where the original row was created with `agent_ids=[]` — the document then stays invisible
  to every agent while a success toast is shown.
- **Blast radius**: all four ingestion entry points (RAG multipart, RAG tus, Knowledge Map
  multipart, Knowledge Map tus). Fails in the restrictive direction, so no leak — but it makes
  the retry path, the one users hit *after* an ingestion failure, unable to fix a wrong
  binding.
- **Intent source**: internal inconsistency — the tus path deliberately carries the allowlist
  in upload metadata specifically so "the finaliser applies it atomically on the new document
  (no racy post-upload PATCH)"
  (`frontend/src/slices/agents/views/RagConfigDetailView.vue:312-318`). The re-index branch
  breaks exactly that guarantee.
- **Visibility**: the response body does carry the stale `agent_ids`
  (`backend/app/api/v1/rag.py:162`), but the frontend discards it — `RagConfigDetailView.vue:302-328`
  shows a success toast and invalidates the query without comparing.
- **Tests**: `backend/tests/wiring/test_rag_ingestion.py` seeds `agent_ids` only via direct
  repository calls for the retrieval filter; nothing covers ingest-path propagation.

## F-12: an unvalidated MCP tool name can brick every turn for an agent

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/agent_service.py:174-193`
  (`_validate_mcp_config` checks only: is a list, ≤200 entries, each a non-empty `str` — no
  length cap, no charset check), against the sibling `_FUNCTION_NAME_RE = ^[a-z0-9_]{1,64}$`
  at `:76` applied at `:100` for `local_function`, which proves the constraint is understood
  and simply not applied here. The API boundary does not compensate:
  `backend/app/api/v1/agents.py:451,473` type `config` as `BoundedConfig`
  (`backend/shared_kernel/validation.py:90` — a size and shape bound only). Composition at
  `backend/contexts/agents/application/runtime/builtin_tools.py:551-552`:
  `f"mcp__{str(tool.id)[:8]}__{mcp_tool}"` — a 14-character prefix, so any upstream name over
  50 characters overflows the 64-character function-name limit that both OpenAI and Anthropic
  enforce. No sanitisation downstream:
  `backend/contexts/agents/application/runtime/tool_registry.py:139-145` passes the name
  through; `backend/contexts/keys/infrastructure/adapters/openai.py:138` and
  `anthropic.py:161` emit it verbatim. Blast radius mechanism:
  `turn_engine.py:2649` computes `tool_specs` once and `:2660-2661` attaches it to **every**
  round of **every** turn; `provider_router.py:380` treats a deterministic 400 as
  non-retryable across sibling keys.
- **Failure scenario**: an MCP server exposes
  `search_customer_orders_by_region_and_date_range` (46 characters, entirely legal per the
  MCP spec). Composed, it is 60 characters — under the limit. Five more characters upstream,
  or any name containing `.` or `:` (both common in MCP servers, both outside the providers'
  legal charset), and every turn for that agent 400s on the tool name. Keys are not burned,
  because the 400 is classified non-retryable, but the turn dies with an opaque provider
  error and nothing points at the tool name.
- **Blast radius**: one bad `allowed_tools` entry disables the agent entirely, not just that
  tool.
- **Intent source**: internal inconsistency with `_FUNCTION_NAME_RE`.
- **Trigger likelihood**: `allowed_tools` is free text from the client — the discovery
  intersection at `backend/contexts/agents/infrastructure/sandbox/docker_runsc.py:850-853`
  applies to the *test* endpoint, not to the stored config. Real MCP servers rarely exceed 50
  characters, so this is rated major on blast radius rather than on likelihood.
- **Tests**: `backend/tests/unit/test_agent_service.py:825-843` covers source, reference,
  empty-list and non-empty-string cases only — nothing on length or charset.

## F-13: a Prompt Studio reply that misses the WebSocket is unrecoverable, and one path wedges the composer

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: **there is no session read endpoint at all** —
  `backend/app/api/v1/prompt_studio.py:741-770` is the complete session surface (create
  session, post message), and `frontend/src/slices/prompt-studio/api/index.ts:166-173` has
  only `createSession` / `postMessage`. The worker does persist the reply
  (`backend/app/workers/tasks/prompt_assistant.py:146-147` appends to the Redis
  `SessionStore` before publishing), but nothing can read it back. Pub/sub does not replay
  (`backend/shared_kernel/realtime/pubsub.py:3-6`).
  `frontend/src/slices/prompt-studio/composables/usePromptAssistantSocket.ts:36,41` clears
  `streaming` only on `prompt.finished` or `prompt.error` — no watchdog, unlike
  `useChatroomSocket.ts:24` which has one for exactly this failure mode, and unlike
  `useBuildStateSocket.ts:80-87` which has a backstop poll.
  `PromptAssistantPanel.vue:198` binds `:loading="sending || streaming"` and
  `SButton.vue:29` hard-disables on `loading`.
- **Failure scenario (b), the wedge**: any socket reconnect mid-turn after at least one
  `prompt.token` has arrived leaves `streaming === true` with no terminal event ever
  redelivered. The Send button stays permanently disabled; the panel is unusable until a page
  reload, which mints a fresh session and drops the history. The idempotency job id
  (`backend/contexts/prompt_studio/application/session_service.py:69`) guarantees a re-POST
  will not re-run the turn either.
- **Failure scenario (a), silent loss**: on the first message of a session, the socket is
  still completing ticket fetch + WS upgrade + owner check
  (`backend/app/api/ws/prompt_assistant.py:30-43`) while `postMessage` has already enqueued
  the job. Frames published in that window go nowhere. This most plausibly bites
  `prompt.error`, which is emitted within milliseconds of the job starting
  (`prompt_assistant.py:52,65-68`) — a lost `prompt.error` means the panel shows nothing at
  all: no alert, `streaming` never set, silent no-op.
- **Blast radius**: every Prompt Studio assistant panel (personal, org, admin, project
  scope). The user loses the reply text while still paying for it — the turn consumes the
  message cap (`session_service.py:50`) and the daily quota (`:58`) regardless.
- **Intent source**: internal inconsistency — the two comparable channels in the codebase
  both have a recovery mechanism and this one has neither.
- **Scope correction**: path (a) silently swallows rather than wedges; only path (b) wedges.

## F-14: every MCP tool is advertised to the model with no parameter schema

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the schema is discarded at the source —
  `deploy/sandbox/driver/driver.py:181` keeps
  `names = [str(t.get("name", "")) for t in result.get("tools", [])]` and the stdio branch at
  `:184-186` does the same, so `inputSchema` never leaves the sandbox;
  `backend/contexts/agents/infrastructure/sandbox/docker_runsc.py:917` keeps names only;
  `backend/contexts/agents/application/agent_service.py:174-193` has nowhere in the stored
  config for a per-tool schema; at turn time
  `backend/contexts/agents/application/runtime/builtin_tools.py:590-595` hardcodes
  `input_schema={"type": "object", "additionalProperties": True}` and the description at
  `:592` is only `"MCP tool '{name}' from bound server {ref}."` — no parameter hints. There is
  **no turn-time schema fetch**: the only `tools/list` call site in the repo is the probe.
  Adapters emit it verbatim (`openai.py:140`, `anthropic.py:160`, `gemini.py:102`).
- **Failure scenario**: a configurator binds a filesystem MCP server and enables `read_file`,
  whose real schema requires `path`. The model sees a free-form object schema and a
  description naming no parameters, so it guesses `{"file_path": "/data/x.csv"}`. The server
  rejects the call. The model burns tool rounds guessing argument names; for a tool with
  non-obvious required parameters it may never succeed.
- **Blast radius**: every `hosted_mcp` tool on the platform — the one capability class that
  is enabled in configuration and substantially degraded at runtime.
- **Intent source**: internal — discovery already speaks `tools/list` and throws away the
  half it needs.
- **Severity note**: downgraded from the original "effectively non-functional". The model is
  not without feedback — `builtin_tools.py:588` returns `is_error` carrying the server's
  stderr, and `MAX_TOOL_ROUNDS = 8` allows in-turn retry — so this is a cost and reliability
  defect rather than a hard break, except against servers whose errors are unhelpful.

## F-15: the compaction lock is released before the summary row is committed, so two agents can double-fold the same range

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:2504-2569` — read
  line by line, there is no commit inside the `distributed_lock("compact:lock:{chatroom_id}")`
  block, which exits at `:2569`; `replace_range_with_summary`
  (`backend/contexts/agents/application/runtime/transcript.py:182-192`) only stages the row —
  `ConversationFacade.create_message`'s docstring at
  `backend/contexts/conversation/interfaces/facade.py:161-166` states outright "The caller
  owns commit"; the first commit is `turn_engine.py:2104`, after the whole knowledge and RAG
  assembly block at `:1868-2104`, which is provider-latency-bound. The interleave is reachable
  because the turn lock is per *(agent, room)*, not per room
  (`backend/contexts/agents/infrastructure/turn_lock.py:26-27`, applied at `:590`).
- **Failure scenario**: agent A's turn compacts, releases the compact lock, and spends
  seconds in retrieval before committing. Agent B's turn in the same room acquires the lock,
  re-reads history in its own session under READ COMMITTED, cannot see A's uncommitted
  summary, and summarises an overlapping range. Two `compact_summary` rows land with
  overlapping `compacted_ids`; `load_model_history` emits both and elides the union —
  duplicated summary text in the system prompt, plus a second summarisation call billed to
  the user's key group.
- **Blast radius**: multi-agent rooms in `compact` mode.
- **Intent source**: internal — the FIX-11 comment at `:2524-2525` claims the lock "prevents
  duplicate summaries when two agents' turns in the same room both cross the cap
  concurrently". It serialises the summarisation *call*, not the row's *visibility*, so the
  invariant it asserts does not hold.

## F-16: truncated streamed tool-call JSON silently becomes empty arguments

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/keys/infrastructure/adapters/anthropic.py:307-314` and
  `openai.py:321-328` (`_safe_json` returns `{}` on `JSONDecodeError` **and** on a
  valid-but-non-dict parse), applied unconditionally at stream close
  (`anthropic.py:289`, `openai.py:304`); `finish_reason` is carried into the body
  (`anthropic.py:299`, `openai.py:313`) and **never read** — `_stream_with_tools`
  (`backend/contexts/agents/application/runtime/turn_engine.py:2680-2690`) consumes only
  `body["text"]` and `body["tool_calls"]`, and no other consumer exists in the runtime
  package. **There is no schema validation anywhere in the dispatch path**:
  `backend/contexts/agents/application/runtime/tool_registry.py:172-179` looks the tool up by
  name and calls `tool.invoke(args)` directly; `input_schema` is only ever serialized into
  the provider spec, and no `jsonschema` import exists under
  `contexts/agents/application/runtime/`.
- **Failure scenario**: the model hits its `max_tokens` part-way through a large `code_exec`
  or `file` argument. The partial JSON becomes `{}`. Tools degrade rather than reject —
  `_build_web_search_tool._invoke` (`builtin_tools.py:137-155`) does
  `str(args.get("query", ""))`, so the model receives a plausible-looking empty result set,
  flagged as success, for a search it never made. Tools that index arguments directly raise
  into the registry catch-all and at least return `is_error=True`, but the message reads
  "Tool 'x' failed: KeyError", never "your arguments were truncated" — so the model cannot
  correct by shortening the call.
- **Blast radius**: any tool call whose arguments approach the token ceiling.
- **Intent source**: internal — `finish_reason` is already carried to the exact place that
  would need it.

## F-17: a failed final synthesis call is persisted as the agent's answer, reported as success

- **Severity**: major
- **Verdict**: confirmed (narrowed)
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:2747-2761` — the
  entire final no-tools `call_stream` is wrapped in `try/except Exception`; `:2759` logs a
  warning and returns `last_text`, which the code's own comment at `:2700-2703` describes as
  "typically 'let me check…' or empty". Both `KeyGroupExhausted`
  (`backend/contexts/keys/application/provider_router.py:418,447,453,455`) and
  `ProviderStreamError` are plain `Exception` subclasses and land there. `_stream_with_tools`
  returns `tuple[str, int]`, so the caller at `:2106` cannot distinguish "the model
  synthesized this" from "every key in the group failed". Persisted at `:2181-2189` and
  audited `agent.turn_finished {"tool_rounds": 8}`.
- **Failure scenario**: an agent burns all 8 tool rounds; the final synthesis call fails
  because the key group is exhausted after those rounds of spend. The user receives
  "Let me check that for you." as the agent's final, committed answer, with no error
  indication anywhere in the UI, and it becomes permanent history that the next turn is
  conditioned on. The provider outage is recorded only as a log warning.
- **Blast radius**: tool-heavy turns. Converts a provider failure into a wrong answer.
- **Narrowing**: the *empty* `last_text` case — the worse half of the original candidate — is
  caught. `turn_engine.py:2117-2136` short-circuits on `if not final_text.strip()` to an
  `empty_reply` audit and a `status="skipped"` result, persisting no message row. Only
  non-empty round-8 filler survives to be misreported.

## F-18: `approval.requested` and approver notifications are dispatched before the transaction commits

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/orchestration/application/approval_service.py:97-118`
  (publishes on the room and workflow channels) and `:162-185` (`_notify_and_arm` enqueues
  the timeout and pushes `pending_notify`) — all before any commit; `:176-178` acknowledges
  the pre-commit position in a comment and works around it only by *deferring the approver
  job*, not the publish. The same file's resolution path does it correctly: `cast_vote`
  commits at `:226` and only then calls `_emit_resolution_effects` (`:228`), whose docstring
  at `:350` reads "Post-commit side effects". The commit for the create path happens later,
  in `backend/app/workers/tasks/workflow_steps.py:33-39`. On timeout, a missing approval
  returns `"noop:gone"` and emits nothing
  (`backend/app/workers/tasks/orchestration.py:200-204`). The frontend never reconciles:
  `frontend/src/shared/stores/orchestration.ts:20,27` is pure in-memory Pinia fed only by WS
  events — no query, no refetch, no TTL, no expiry from the stored `timeout_seconds` — and
  `useChatroomSocket.ts:268-291` clears the card only on `approval.resolved`.
- **Failure scenario**: the approval row is inserted and the WS event plus Redis notifies
  fire. A raise in `run_engine._execute_node`'s tail — `update_step` (`:613`),
  `emit_step_event` (`:619`), `update_variables` (`:627`) or `update_state(WAITING)`
  (`:648`) — propagates through `run_step` (`:220-229`), so `workflow_steps.py:39`'s commit
  never runs and the session rolls back. The approval row does not exist, but the room UI
  holds a `pending` approval card that nothing will ever clear, and the armed timeout job
  finds nothing and publishes nothing. Arq's retry sees the run FAILED and does not recreate
  the gate.
- **Blast radius**: any workflow run containing an `approval_gate` node bound to a chatroom.
  Scope correction: "permanently" means until page reload, not persisted.
- **Intent source**: internal inconsistency with the resolution path in the same file, and
  with the observer-release path in `contexts/conversation`, which was already fixed to defer
  dispatch post-commit.
- **Reachability note**: the executor's own broad `except Exception`
  (`backend/contexts/workflow/application/executors/approval_gate.py:34,112`) means
  executor-internal failures do *not* roll back. The reachable trigger is a DB-level raise
  after the executor returns. The existence of `_mark_run_failed_isolated`
  (`run_engine.py:464-482`, "must be written on a separate session so it is not lost in the
  caller's rollback") is direct evidence the codebase treats this path as live.
- **Milder than originally claimed**: `drive_approver_turn`
  (`backend/app/workers/tasks/approvals.py:60-76`) gives up after 5 invisible-row attempts,
  so no provider spend is wasted. The residual is an orphaned `pending_notify` note that the
  approver's next natural turn drains and builds a vote tool for a dead gate — noise, not
  corruption.

## F-19: Tavily `search_depth` is configured, displayed, and never applied

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/infrastructure/search_adapters/tavily.py:45` accepts
  `config` and the identifier appears nowhere in the method body; `:47-52` hardcodes
  `"search_depth": "basic"`. The value does arrive —
  `backend/contexts/agents/application/tools/web_search.py:153` passes `config=key.config`
  directly, with no intermediate layer. The frontend writes it
  (`frontend/src/slices/keys/components/SearchKeyUploadForm.vue:49`) and renders it back as a
  badge (`frontend/src/slices/keys/views/SearchKeyView.vue:184-191`). Contrast
  `search_adapters/google_cse.py:59-61`, which reads `config["cx"]` and hard-fails when
  absent — the `config` seam works; Tavily skipped it.
- **Failure scenario**: a project owner uploads a Tavily key with Search Depth = Advanced.
  The UI lists the key showing "advanced". Every `web_search` call sends
  `search_depth: "basic"`. Shallower results, cheaper tier, no error, no log line.
- **Blast radius**: every project on Tavily, every `web_search` call.
- **Intent source**: `REQUIREMENTS.md:1151` and `docs/implement/D-keys.md:260` both name
  `tavily.search_depth` as a `config` field for the real path.
- **Aggravating**: `backend/contexts/keys/infrastructure/search_probes.py:61,67`
  (`_probe_tavily`) *does* read and send `search_depth`, and its module docstring at `:3-6`
  says the probe deliberately mirrors the shape the agent tool will use. So validation honours
  the setting and the production path ignores it — a user who configures and probes
  `advanced` gets a green check and basic-depth results.

## F-20: `file` op=write with `content` omitted truncates the target file and reports success

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/builtin_tools.py:113-126`
  (`_FILE_SCHEMA` declares `"required": ["op", "path"]` — `content` is optional for all three
  ops, including `write`), executor at `:380`
  (`str(args.get("content", "")).encode("utf-8")` → `b""`). No validation intercepts it:
  there is no schema enforcement in the registry (see F-16), `file_tool.py:79-92` checks type
  and the 10 MB ceiling but never emptiness,
  `backend/contexts/agents/infrastructure/sandbox/docker_runsc.py:1010-1012` raises only on
  `data is None`, and the guest driver at `deploy/sandbox/driver/driver.py:256-272` does an
  unconditional `os.replace` onto the target. `builtin_tools.py:385` returns
  `is_error=not res.ok` → False. Contrast `_CODE_EXEC_SCHEMA` at `:103-111`, which requires
  `source` *and* re-checks it at runtime (`:188-190`).
- **Failure scenario**: the model emits `{"op":"write","path":"notes.md"}` — schema-valid, so
  neither the provider nor the registry rejects it. `/workspace/notes.md` is truncated to
  empty and the model is told the write succeeded. Data loss on the agent's persistent volume.
  This compounds with F-16: a truncated `content` argument arrives as `{}` and takes exactly
  this path.
- **Blast radius**: any agent with `hosted_file_workspace` enabled — provisioned enabled by
  default (`backend/contexts/agents/infrastructure/repositories.py:627`).
- **Severity note**: downgraded from the original rating. The guest emits
  `{"written": 0, "path": ...}` on stdout, which becomes the tool result content, so the model
  does receive a `written: 0` signal even though the call is not flagged as an error —
  recoverable-if-noticed rather than fully silent.

## F-21: `max_alive_subagents` is a live-looking control that no code reads, and clearing it persists an out-of-range value

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: a repo-wide search for `max_alive|alive_subagent` across `backend/`
  (including workers, services, alembic and tests) returns exactly two hits, both
  `backend/contexts/workflow/application/executors/subagent_spawn.py:45,72`, and both read
  `max_alive_simultaneously` from a **workflow node's** config — a different field on a
  different entity. `workflow_capabilities` is only ever stored, passed through or echoed
  (`backend/contexts/agents/infrastructure/repositories.py:85`,
  `backend/contexts/agents/application/agent_service.py:447,616-617`,
  `backend/app/api/v1/agents.py:90,124,148,174,236,334`); there is no `.get(...)` against it
  anywhere, so a variable-key lookup is ruled out. The 0-persist path:
  `frontend/src/slices/agents/views/AgentDetailView.vue:1198-1207` binds a plain `ref`
  outside the vee-validate/zod schema with no `min`, through the `SInput` number coercion (see
  F-22), `:428` sends it whenever `canCreateSubagent` is true, `:394` reloads with `?? 5`
  which keeps a stored `0`, and `backend/app/api/v1/agents.py:90` types
  `workflow_capabilities` as `BoundedConfig` — a size and depth bound with no key-level
  validation.
- **Failure scenario**: the designer enables "can create subagent" and clears the number box
  to retype. The input emits `0`; nothing validates it on either side; `0` is persisted and
  redisplayed. The setting is presented as a live subagent concurrency limit and is inert
  either way.
- **Blast radius**: every agent whose orchestration tab is saved.
- **Intent source**: `docs/UI/06-agents.md:429` specifies "int, 1-20, required when
  `can_create_subagent`".
- **Note**: compounds with F-3 — there is no subagent runtime to honour the cap even if it
  were read.

## F-22: clearing a cleared-able number input sends `0` and 422s, because `SInput` coerces empty string to zero

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/shared/ui/SInput.vue:81-85` is the component's only input
  handler and has no empty-string guard:
  `props.type === 'number' ? Number(target.value) : target.value`, so clearing emits `0`.
  Concrete instance: `frontend/src/slices/agents/components/ConceptMapPanel.vue:118`
  (`saveRecency` special-cases only `=== ''`, and the draft is now the number `0`),
  `:101-105` (`recencyDirty` compares `String(0)` vs the stored value → dirty, so Save is
  enabled); backend `backend/app/api/v1/graphrag.py:104`
  (`Field(default=None, gt=0, allow_inf_nan=False)`) → 422, surfaced only as a generic
  `recencySaveFailed` toast with no field-level error. The `min="1"` attribute at
  `ConceptMapPanel.vue:256` is a native browser hint the component never reads, and there is
  no `<form>` submit to trigger constraint validation.
- **Failure scenario**: a designer sets a Concept Map recency half-life, later clears the
  field intending to restore the default, and gets an opaque save failure with no indication
  which field is at fault or what to do about it.
- **Blast radius**: every `SInput type="number"` whose backend bound excludes zero. Confirmed
  instances: the Concept Map recency half-life and `max_alive_subagents` (F-21). The agent
  detail view's `temperature`/`top_p`/`seed` deliberately avoid the trap by using a text
  input with an explicit `Number.isFinite` guard
  (`frontend/src/slices/agents/views/AgentDetailView.vue:328-341`) — so the hazard is known
  and the mitigation was applied locally rather than in the shared control.
- **Intent source**: internal inconsistency between the shared control and its known
  workaround.
- **Explicitly not part of this finding**: the non-clearability of the recency half-life
  itself is documented as deliberate at
  `backend/contexts/knowledge/infrastructure/graphrag_repositories.py:895-902` ("a deliberate
  limitation (WS5)"). The reportable defect is the 422 on clear, not the limitation. A
  residual API-contract wart remains: `GraphRagConfigPatchIn.recency_half_life_days` is typed
  nullable, so the OpenAPI schema advertises a `null` that is silently inert.

## F-23: detaching an agent's knowledge source leaves `file_search` enabled and permanently failing

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the invariant is explicit —
  `backend/contexts/agents/application/agent_service.py:345-350` docstring: "preserving the
  invariant **file_search enabled ⇒ rag_config_id present**… an enabled File Search tool with
  no backing config is a worse state than the dangling FK", repeated in
  `backend/alembic/versions/0054_config_delete_agent_unbind.py:13-16`. Two of three writers
  uphold it: `create` at `:451`, `patch_tool` at `:809-816` (raises
  `FileSearchNeedsKnowledge`), `clear_config_bindings` at `:358-359`. `patch` does not —
  `:571-574` writes `rag_config_id=None` and `:619-642` audits and returns with no `self._tools`
  call anywhere in the method. No runtime filter compensates:
  `backend/contexts/agents/application/runtime/builtin_tools.py:694-713` gates only on
  `if not t.enabled`, and the `rag_config_id is None` check lives inside `_invoke` at
  `:416-420`, returning `is_error` only after the model has already called it. The path is
  reachable from the API (`backend/app/api/v1/agents.py:338` maps an explicit
  `"rag_config_id": null` to `clear_rag_config=True`).
- **Failure scenario**: a designer clears the knowledge source in the agent editor. The tool
  row stays enabled, so `file_search` is advertised in every turn's tool spec; the model calls
  it and gets "file_search unavailable: no knowledge source configured for this agent",
  burning a tool round repeatably since nothing in the prompt says the tool is dead. Deleting
  the config produces the correct state; the ordinary user-driven unbind does not.
- **Blast radius**: any agent whose knowledge source is detached through the agent editor.
  Also inflates the tool-token count against the knowledge budget
  (`turn_engine.py:762`, `:1893-1898`), shrinking the grant for remaining knowledge sources.
- **Tests**: `backend/tests/unit/test_agent_service.py:697-714` asserts only the column write;
  the mocked `_tools` is never asserted against, so adding the reconciliation would not break
  it.

## F-24: `autostop_rounds = 0` permanently disables the silence trigger while the worker reads the same value as 100

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/app/workers/tasks/orchestration.py:111-113` applies a zero-fallback
  (`effective_limit = autostop_limit if autostop_limit > 0 else sm.autostop_max_default` →
  100); `backend/contexts/orchestration/application/wakeup_service.py:224-227` uses the raw
  value (`if autostop_count >= autostop_limit: return False`), so `0 >= 0` returns False on
  every 30s sweep, forever — and `reset_autostop` only zeroes the count, so `0 >= 0` still
  holds. Reachable: `backend/contexts/orchestration/domain/models.py:159` is
  `min(int(...), AUTOSTOP_HARD_CAP)` — an upper clamp only — and `wakeup_config` is typed
  `BoundedConfig` at `backend/app/api/v1/agents.py:89,123`, so `0` and negatives pass. The
  frontend clamps to `>= 1` (`frontend/src/shared/ui/SWakeupEditor.vue:105-109`) but that is
  not a backend guard, and `models.py:95-96` explicitly claims the parse layer enforces caps
  "regardless of how the JSONB was written (designer UI, direct DB edit, migration)".
- **Failure scenario**: an agent is configured
  `"silence_minutes": {"enabled": true, "t_minutes": 5, "autostop_rounds": 0}` — a plausible
  reading of "0 = no autostop". The silence trigger never fires again, with no audit, log or
  error, while the same agent's `every_n_messages` wake-ups pass the worker gate with an
  effective limit of 100.
- **Blast radius**: any agent with `autostop_rounds` set to 0 or negative. Fail-safe direction
  (a trigger stops firing rather than storming), hence minor.
- **Corrections to the original candidate**: the shared helper *is* called by both sites
  (`wakeup_service.py:224`, `orchestration.py:101`), and its "single source of truth"
  docstring refers to *which cap field* is selected — that part does not diverge. The
  divergence is the zero-fallback layered on at one call site only. Also
  `docs/implement/N-conversation-a2a-fixes.md:189-203,228` scopes the FIX-01 fallback
  explicitly to the `wakeup_agent` guard, so the evaluator is arguably spec-compliant as
  written. The fix belongs in a lower clamp at `models.py:159`, not another call-site patch.
- **Tests**: `backend/tests/unit/test_agent_trigger_wiring.py:304-431` and
  `test_wakeup_service.py` exercise 3/5/50 only; nothing passes `0`.

## F-25: PATCH accepts a whitespace-only agent name and persists the empty string

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/app/api/v1/agents.py:93-124` — read in full;
  `name: str | None = Field(default=None, min_length=1, max_length=200)` with no
  `field_validator` and no `StringConstraints(strip_whitespace=True)`. The class's only
  validator is `_strip_model_id` at `:105-111`, which covers `model_id` and demonstrably not
  `name`. `"   "` passes `min_length=1`. The route builds the draft with the raw value at
  `:316-318`; `backend/contexts/agents/application/agent_service.py:555-556` strips at write
  → `""`. Create rejects the same input at `:388-389`.
- **Failure scenario**: `PATCH /api/agents/{id}` with `{"name": "   "}` returns 200 and the
  agent's name is now `""`. It renders as a bare label in the UI and in `_participant_labels`
  (`turn_engine.py:2399-2414`), so history rows for that agent reach the model with an empty
  speaker name.
- **Blast radius**: API and CLI consumers only — the frontend Zod schema
  (`frontend/src/slices/agents/schemas.ts:19`, `.trim().min(1)`) blocks it in the UI.
- **Note**: the unique-constraint collision a second empty name would cause is handled, not a
  500 — `agent_service.py:689-691` maps `uq_agents_project_name_active` to a conflict error.
- **Tests**: no `strip` or `whitespace` coverage in
  `backend/tests/unit/test_agent_service.py`.

## F-26: an empty MCP allowlist renders as "all tools" while contributing zero tools

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/agents/views/AgentToolsView.vue:871-873` renders
  `agents.tools.mcp.allTools` for a zero-length `allowed_tools`, inverting the runtime meaning
  — `backend/contexts/agents/application/agent_service.py:184-189` states "An empty allowlist
  produces zero runtime tools (build_agent_tools iterates the allowlist), so the binding is
  silently inert". Reachable in a deployed database:
  `backend/alembic/versions/0011_agents.py:118-119` gives `agent_mcp_servers.allowed_tools` a
  `server_default '{}'::text[]`, and `backend/alembic/versions/0036_agent_tools.py:204` copies
  it forward unfiltered (`mcp_config["allowed_tools"] = list(mr.allowed_tools or [])`) — which
  is why `allow_empty_allowlist` exists on the patch path at all.
- **Failure scenario**: a legacy binding backfilled with `[]` displays "all tools" in the MCP
  table while contributing zero tools to the agent at runtime. The designer sees a
  healthy-looking row and never opens it to fix it.
- **Blast radius**: legacy `hosted_mcp` bindings only — the submit path at
  `AgentToolsView.vue:359-362` refuses an empty allowlist, so new ones cannot be created this
  way. Display-only.

## F-27: duplicating an agent silently drops `skill_index_token_cap`

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/agents/views/AgentListView.vue:202-221` enumerates 15
  fields explicitly and omits `skill_index_token_cap`, so the backend create default applies
  and the copy reverts to the platform default. The field is genuinely absent from all UI — a
  repo-wide search for `skill_index_token_cap|skillIndexTokenCap` returns zero frontend hits;
  it is missing from the slice's hand-rolled `Agent` interface
  (`frontend/src/slices/agents/api/index.ts:30-52`) as well as from every view. It exists only
  on the backend (`backend/app/api/v1/agents.py:84,144`), bounded by the DB CHECK in
  `backend/alembic/versions/0056_skills.py:65-66` and enforced at bind time in
  `backend/contexts/agents/application/agent_service.py:589-594`.
- **Failure scenario**: an agent with a tuned skill-index cap, set out-of-band via the API, is
  duplicated; the copy silently reverts to the 3000 default.
- **Blast radius**: duplicated agents that had the cap set out-of-band — a near-empty
  population, since there is no UI to set it.
- **Note**: fixing the duplicate requires first widening the slice's `Agent` interface, so it
  is a two-part change.

## F-28: semantic `chunk_params` loses `max_tokens_per_chunk` on a settings save

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/agents/composables/useChunkParamsForm.ts:23-27`
  (`assembleChunkParams('semantic')` returns only `{ similarity_threshold }`), resent by the
  full-form save at `frontend/src/slices/agents/views/RagConfigDetailView.vue:272` and
  `KnowledgeMapConfigDetailView.vue:288`;
  `backend/contexts/knowledge/application/config_service.py:320-335` copies `chunk_params`
  verbatim into `db_values` (same at `knowmap_config_service.py:256`), and
  `normalized_chunk_params` is called only inside the immutability comparison at `:309-318`,
  never on the value being written; `backend/contexts/knowledge/domain/models.py:107-128`
  (`DEFAULT_SEMANTIC_CHUNK_PARAMS` includes `max_tokens_per_chunk: 512`); the default is
  re-merged at `chunkers.py:258`. Settable out-of-band because `chunk_params` is
  `BoundedConfig` (`backend/app/api/v1/rag.py:72`, `knowmap.py:86`).
- **Failure scenario**: a config created via API/CLI with
  `chunk_params = {max_tokens_per_chunk: 1024, similarity_threshold: 0.3}` and no documents
  yet. The designer opens the detail view and renames it. The save stores
  `{similarity_threshold: 0.3}` and chunking silently reverts to 512.
- **Blast radius**: narrow. The F-20 immutability guard at `config_service.py:309-318`
  normalizes **both** sides, so once the config has any locking document the same save is
  rejected with `ChunkParamsImmutable` (409) rather than silently dropping the key — the
  silent drop is reachable only on an out-of-band-created, semantic-strategy, document-free
  config. UI-created configs never carry the key.
- **Note**: `docs/tasks/2026-07-14-chunk-params-immutable-with-docs/spec.md:110-114`
  anticipated this interaction and specified only the guard behavior, not preservation on
  write. Severity downgraded from the original rating accordingly.

## F-29: an approval note drained by a concurrent room turn is unrecoverable, and the driven turn reports success

- **Severity**: minor
- **Verdict**: **plausible** — the race and the silent-timeout path are traced, but the step
  that decides whether harm occurs (whether an LLM in a chat turn chooses to call the vote
  tool) is not statically traceable.
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:652-681`
  (`run_input_turn` takes no lock; only `run_turn` wraps `turn_lock` at `:590`);
  `backend/contexts/orchestration/infrastructure/pending_notify.py:28-29,43-64` (agent-keyed
  and destructively drained, so whichever turn starts first takes the note);
  `_APPROVER_TURN_DISPATCH_DELAY_S = 2`
  (`backend/contexts/orchestration/application/approval_service.py:49`, used at `:184`);
  `backend/app/workers/tasks/approvals.py:104-111` (the diagnostic fires only on
  non-`completed`, so a voteless "completed" logs at info as "approver turn driven").
  `_requeue_notifications` (`turn_engine.py:1641-1656`) fires only for misrouted observations
  and for failed/skipped turns.
- **Failure scenario**: a workflow opens an approval gate; the approver's note is pushed and
  `drive_approver_turn` is deferred 2s. Within that window a room message fires the same
  agent's `every_n_messages` trigger. The room turn drains the queue. Two seconds later the
  driven turn drains an empty queue, gets no vote tool, and returns `completed` with no vote.
  If the room turn — an ordinary chat turn with an approval line appended to its notify block
  — declines to vote, the note is consumed for good: no requeue, no re-arm, and
  `approval_timeout` is the only backstop, resolving the gate as `TIMEOUT_LEADER`.
- **Blast radius**: approval gates whose approvers are also room-bound. Narrow reachability —
  the room turn must *begin* inside roughly the 2s dispatch delay plus queue latency, since a
  turn already in flight drained before the push.
- **Refuted framing**: the original candidate claimed the note is stolen into a context where
  voting is impossible. It is not. The room filter in `_pending_context_and_tools`
  (`turn_engine.py:1598-1600`) applies **only** to `kind == "released_observation"`; an
  `approval_request` note always lands in `usable`, is rendered at `:1622-1625`, and
  `:1634-1638` builds `cast_approval_vote` scoped to that approval id — pinned by
  `backend/tests/unit/test_a2a_turn_dispatch.py:686-718`. The missing lock is also not the
  cause: `turn_lock` is keyed `(agent, room)` and the drain happens at turn start, so
  serialising the turns would still let the room turn drain first. The defect is drain
  semantics, not locking.

## F-30: the queued-trigger drain deletes the message-id key non-atomically, losing a fresh trigger's anchor

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:304-305` — two
  separate Redis round-trips; `_mark_trigger_queued` at `:274-285` writes the trigger key with
  `nx=True` (first-wins) and the message id with a plain `SET` (last-wins).
- **Failure scenario**: a drain reads the trigger key, then loses the interleave to a fresh
  `_mark_trigger_queued` writing both keys. The drain's second call deletes the newly written
  message-id key while leaving the new trigger key intact. The next holder's pop then serves
  that trigger with `trigger_message_id=None`, so `_resolve_trigger_attachments`
  (`:983-997`) degrades to its `latest_user_attachments(chatroom_id)` fallback.
- **Blast radius**: one turn's attachment anchor. No lost or duplicated turn.
- **Refuted companion claim**: the headline "post-release drain duplicates a whole agent
  turn" does **not** hold. `_pop_queued_trigger` uses `redis.getdel` (`:304`), so the parked
  mark is a token consumed by exactly one racer: if the retrying job pops first it runs the
  turn and the drain's pop returns `None`; if the drain pops first the retrying job hits the
  explicit `break` at `:604`, whose comment states precisely this reasoning. There is no third
  turn.
- **Tests**: no test references `_pop_queued_trigger`, `_mark_trigger_queued` or `turn_lock`.

## F-31: the egress proxy pins to the first resolved address with no failover

- **Severity**: minor
- **Verdict**: **plausible** — the mechanism is confirmed; the originally-claimed trigger was
  refuted, and the surviving trigger was not reproduced.
- **Evidence**: `backend/services/egress_proxy/app.py:352` (`connect_ip = ips[0]`), `:353-359`
  (the URL is pinned to that single literal, so httpx performs no DNS and no
  Happy-Eyeballs), `:442-443` (any `httpx.HTTPError` becomes a 502, with no retry loop
  anywhere).
- **Failure scenario**: a multi-homed allowlisted host whose first A record is down causes
  every request to 502 until DNS reorders — surfaced to the model as
  `"web_search failed: ..."` or a function-tool error that implicates the provider rather than
  the network.
- **Blast radius**: deployment- and DNS-dependent; when it bites it disables the affected
  egress-backed tooling for the project.
- **Refuted original framing**: the candidate claimed dual-stack hosts are unreachable because
  `getaddrinfo` returns AAAA first on an IPv4-only container. The deployment *is* IPv4-only
  (no compose network enables IPv6), but the image is
  `python:3.12-slim-bookworm` (`backend/services/egress_proxy/Dockerfile:5`) — glibc — and
  glibc's `getaddrinfo` applies RFC 6724 destination sorting unconditionally, demoting
  destinations with no usable source address. So `ips[0]` is an IPv4 address as deployed. The
  claim would hold on musl or with partially-configured IPv6; neither ships here.
- **Explicitly routed elsewhere**: the sibling claim that
  `if any(is_blocked_ip(ip) for ip in ips)` wrongly blocks a whole host is **not** an audit
  finding — `backend/services/egress_proxy/ip_policy.py:66-69` documents the `any()` contract
  and `:23-24` states the anti-rebinding rationale, and `app.py:265-270` spells out
  pinning-as-SSRF-closure. That is a deliberate posture; see FU-2.

## F-32: a comment documents a flicker-avoidance that the next event undoes

- **Severity**: minor
- **Verdict**: confirmed (documentation-level; the behavior is deliberate and test-locked)
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:2200-2209` emits
  `message.created` then `agent.finished` back to back, post-commit.
  `frontend/src/slices/conversation/composables/useChatroomSocket.ts:179` handles
  `message.created` with `void replayDelta()` — async, appending only after
  `await listMessages(...)` resolves at `:94-96` — while `:180-183` comments that "only
  `clearAgentStream` defers to `applyMessageCreated` (post-append) to avoid the streamed-draft
  flicker". One frame later `:243-252` calls `store.clearAgentStream` unconditionally, and
  `frontend/src/shared/stores/conversation.ts:105-120` confirms it is fully synchronous with
  no knowledge of the message list. Agent replies have no optimistic echo
  (`turn_engine.py:2194-2195`), so there is a genuine one-round-trip window with neither draft
  nor persisted row.
- **Failure scenario**: every agent reply flickers — the streamed text is wiped and the
  persisted message appears a REST round trip later.
- **Blast radius**: cosmetic, every agent reply in every room.
- **Why documentation-level**: the unconditional clear is a deliberate, test-locked decision
  (`useChatroomSocket.ts:247-251` "BUG-1 fix", asserted in
  `frontend/src/slices/conversation/composables/__tests__/useChatroomSocket.test.ts:138-148`
  and `:150-159`) guarding against a ghost bubble when `message.created` is lost on reconnect.
  Changing the behavior would break two tests. The actionable residue is the stale comment,
  which asserts a guarantee the code does not provide.

## 4. Refuted Candidates

Kept because each refutation is itself informative — most turned on a guard, spec or test
that is easy to miss, and re-reporting them next audit would waste the same effort.

- **Knowledge Map applies the per-agent allowlist post-retrieval** (claimed major). Real
  behavior, but it is the documented, chosen design:
  `docs/tasks/2026-07-07-graphrag-phase3-knowledge-map/spec.md:125-142` records option B
  ("edge filter by evidence provenance, all-source-docs-readable") as **chosen**, rejects
  option C on security grounds, and names the cost explicitly — "Consciously given up: recall
  from partially-readable edges (accepted for security)". Pinned by
  `backend/tests/unit/test_knowmap_edge_filter.py` and
  `test_knowmap_context_provider.py:88-112`. Two cited evidence points were also wrong:
  `querying_agent_id=None` is inert on this path (consumed only when an `evidence_fetcher` is
  wired, which knowmap deliberately does not), and a Qdrant-side pre-filter is architecturally
  impossible since the entity payload carries no document provenance. Residual: no recall
  floor for heavily-restricted agents — a tuning ticket, not a defect. See FU-1.
- **`wakeup_config = {}` collapse strands a self-modified cadence** (claimed major). The three
  collapse sites are real, but the harm is unreachable: the only system-actor writer
  (`WakeupService._build_new_dict`, `wakeup_service.py:329-337`) rewrites only `n` and
  `t_minutes`, leaving every `enabled` flag False, so the drifted row and the baseline read
  identically at every consumer. Decisively, `backend/alembic/versions/0019_wakeup_authored_snapshot.py:35-38`
  backfills `WHERE wakeup_config != '{}'::jsonb` — the established convention is that `{}`
  means *no authored baseline*, which is what the patch and read paths implement. The create
  path's comment claiming the opposite is the outlier. Net effect: a cosmetic JSONB difference
  and one missing audit row.
- **`_request_ceiling` uses `or` where `context.py` uses `is not None`** (self-declared hunch).
  Unreachable: `0011_agents.py:97` has carried
  `context_token_cap IS NULL OR context_token_cap > 0` since the table was created, `0057`
  replaces it with an equivalent-plus-upper-bound and its backfill only clamps from above, and
  both API verbs carry `Field(gt=0)`. No window in migration history accepts `0`. Latent
  inconsistency worth a comment; not a bug.
- **An agent's own reply consumes its own `every_n` trigger slot** (claimed minor). This is
  the pinned specification, not a deviation: `docs/implement/N-conversation-a2a-fixes.md:222-225`
  states the acceptance criterion verbatim — "A's and B's counters both incremented". The
  counter is per *(agent, room)* (`wakeup_state.py:26-27`), not shared, so the claimed
  permanent phase shift does not arise; and a 7-day TTL bounds any parity lock-in regardless.
- **Two consecutive replies by the same agent wedge the room with a provider 400** (claimed
  high). The mechanism is real — no adapter coalesces adjacent same-role messages, and the
  scenario is reachable — but the consequence is unsubstantiated. OpenAI Chat Completions has
  no alternation requirement, and the Anthropic Messages API combines consecutive same-role
  turns rather than rejecting them. The only evidence offered for the 400 was the code comment
  at `turn_engine.py:2452-2454` — a developer belief, not an API fact. Without the 400 the
  entire "wedged until compaction" chain never starts. Note the adapters *do* guard a verified
  deterministic-400 case (empty content, `anthropic.py:106-109`), which shows the authors
  guard what they confirmed.
- **arq retries a timed-out turn 5 times, multiplying BYO spend** (claimed high). `max_tries`
  does default to 5 and is unset for `wakeup_agent`, but a `job_timeout` expiry raises
  `TimeoutError` (a builtin `Exception` subclass on Python 3.12), not `CancelledError`, so
  arq's retry branch is skipped and the job is terminal on the first attempt. A genuine
  re-execution vector remains for worker shutdown/SIGTERM, which is a deploy concern of much
  narrower scope. Incidentally the comment at `backend/app/workers/tasks/rag.py:120` ("Arq's
  default max_tries is 1") is factually wrong.
- **The A2A cancel handler commits partial state and drops notifications** (claimed medium).
  Both halves are refuted by documented contracts. `_requeue_notifications`' docstring
  (`turn_engine.py:1641-1643`) scopes it to turns that failed "before the provider call could
  read them", and the `rounds_completed == 0` condition implements exactly that predicate —
  requeuing later would double-deliver. The rollback asymmetry is also principled: the failure
  handler rolls back because the exception may itself be a DB error, whereas `_TurnCancelled`
  is a clean control-flow exception, and discarding the `mcp.tool_invoked` audit rows for
  calls that genuinely executed would violate the audit invariant in the opposite direction.
- **Instruct depth cap is off by one** (claimed minor). The code matches the authoritative
  source verbatim — `REQUIREMENTS.md:784` `[R15.16]` rule 2: "Reject if
  `len(path) >= max_chain_depth` (platform default 5)". The apparent conflict is a units
  mismatch between two documents: `docs/implement/G-orchestration.md:176` counts nodes ("depth
  6 rejected") where the requirement and the code count hops. Both describe the same behavior.
- **`workflow.state_changed` is a declared-but-never-emitted chatroom event** (claimed low).
  The frontend type is `ChatroomEventType | string`, so the union is documentation with no
  exhaustiveness check and no dead code path, and the name is copied verbatim from
  `REQUIREMENTS.md:690` `[R13.19]` — the frontend conforms to spec rather than inventing a
  phantom. The only true statement is that `[R13.19]`'s event is unimplemented backend-side:
  unbuilt spec, not a defect. See FU-3.
- **The WS writer discards a dequeued frame on teardown** (claimed low). `shutdown` is set only
  in `connection_loop`'s `finally` (`connection.py:444`), when the connection is already
  terminating, and `while not shutdown.is_set()` means the entire remaining queue is abandoned
  by design. Discarding one already-dequeued item is consistent with that design, not a
  deviation. No enqueue-then-shutdown pattern exists that would lose a meaningful final frame:
  every `_request_close` records a close code and returns without enqueuing a payload.
- **RAG retrieval lacks a runtime project re-check** (self-rated latent by the finder). The
  structural asymmetry against the key-group path is real, but no trigger exists: `AgentPatchIn`
  has no `project_id` field and sets `extra: "forbid"`, there is no clone/duplicate/transfer
  endpoint, and a repo-wide search finds no UPDATE writing an agent's or a config's
  `project_id`. Both sides of the binding are immutable after the write-time check, so the
  missing re-check cannot fire. Recorded as FU-4.
- **Concept Map recency half-life "can never be cleared"** (claimed high). Split: the
  non-clearability is documented as deliberate
  (`graphrag_repositories.py:895-902`, "a deliberate limitation (WS5)") and is not a defect.
  Only the 422-on-clear survives, and its root cause is the shared number input — reported as
  F-22.
- **Removing an MCP advanced-config key never persists** (claimed medium). The dict-spread
  merge at `agent_service.py:826` carries a comment one line above stating the intent — a
  partial PATCH must never drop sealed auth or other persisted fields — and PATCH is merge
  semantics by definition. The finder's own evidence also undercuts the severity: the JSON
  editor is seeded from the stored config *minus* every key the validator reads, so anything a
  user can delete there is inert clutter. A UX note about a delete affordance the API cannot
  honor, not a functional bug.
- **The post-release drain duplicates a whole agent turn** (claimed medium). Refuted by
  `redis.getdel` single-consumer semantics; see F-30, which records the narrower sub-claim
  that did survive.

Additionally, one confirmed item is **not reported as a new finding** because it is already
recorded: `agent.warning` (`kind: "skills_unavailable"`) is emitted at
`turn_engine.py:1540-1561` and has zero consumers anywhere in `frontend/src/`, so a
skill-degraded answer reaches the user with no signal. This is already triaged as FU-23 in
`docs/tasks/2026-07-16-agent-skills/spec.md:3182-3202` (verified 2026-07-17), with the same
analysis and a prescribed Phase 2 acceptance criterion. It is an owner-less spec item, not a
new discovery.

## 5. Hand-off

Per the dossier contract, this section links the task slugs this audit spawned. A finding with
no dossier and no explicit decision to skip it is an unfinished triage.

Awaiting user triage — no dispositions recorded yet.

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | | |
| F-2 | | |
| F-3 | | |
| F-4 | | |
| F-5 | | |
| F-6 | | |
| F-7 | | |
| F-8 | | |
| F-9 | | |
| F-10 | | |
| F-11 | | |
| F-12 | | |
| F-13 | | |
| F-14 | | |
| F-15 | | |
| F-16 | | |
| F-17 | | |
| F-18 | | |
| F-19 | | |
| F-20 | | |
| F-21 | | |
| F-22 | | |
| F-23 | | |
| F-24 | | |
| F-25 | | |
| F-26 | | |
| F-27 | | |
| F-28 | | |
| F-29 | | |
| F-30 | | |
| F-31 | | |
| F-32 | | |

## 6. Out-of-scope Observations

- **FU-1** — Knowledge Map retrieval has no recall floor for agents whose document allowlist
  is a small subset of the corpus. The post-retrieval edge filter is the accepted design (§4),
  but `top_k` is a fixed 5 (`graphrag_retrieve.py:100`) and is not widened to compensate, so
  recall degrades as the non-allowlisted document count grows. Tuning work, not a defect.
- **FU-2** — The egress proxy blocks a host outright when *any* resolved address is
  disallowed (`services/egress_proxy/app.py:274`, `ip_policy.py:49,66-69`). This is a
  documented anti-DNS-rebinding posture, so it is not an audit finding; if the posture itself
  should be revisited (a legitimate host that also resolves into 100.64/10, common for cloud
  load balancers, is rejected), route it to `check-security`.
- **FU-3** — `[R13.19]`'s `workflow.state_changed` chatroom event is unimplemented
  backend-side. Unbuilt spec rather than a defect; either build the emitter or amend the
  requirement.
- **FU-4** — RAG and Knowledge Map config resolution at turn time trusts the stored FK without
  the project re-check that the key-group path performs
  (`turn_engine.py:1690-1700`). Unreachable today because agents and configs cannot change
  project, but the guard pattern already exists a few lines away and closing the asymmetry
  pre-emptively would make any future agent-duplication or project-transfer feature safe by
  construction.
- **FU-5** — `docs/implement/G-orchestration.md:176` and `REQUIREMENTS.md:784` use "depth" for
  different units (nodes vs. hops), which cost this audit a false positive. Worth one
  clarifying sentence in the implement note.
- **FU-6** — `backend/app/workers/tasks/rag.py:120` states "Arq's default max_tries is 1 (no
  retry)". The default is 5. The comment is load-bearing for anyone reasoning about retry
  safety.
- **FU-7** — Structural observation for `check-quality`: `turn_engine.py` is ~145 KB in a
  single module and concentrates the turn lifecycle, prompt assembly, compaction, tool
  dispatch and cancellation. Six of this audit's confirmed findings (F-5, F-6, F-7, F-8, F-15,
  F-17) are cases where a guard exists in one region of that file and is absent in a sibling
  region — a pattern that module size makes hard to see.
</content>
</invoke>
