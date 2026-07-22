---
type: audit
status: closed
created: 2026-07-23
requirements: [R7.04, R9.11]
---

# Audit: adversarial verification of model-hint provider routing

## 1. Scope

- **Area**: provider eligibility and rotation, agent room/headless turn preconditions,
  summarisation/forced compaction, and the conversation UI error presentation affected by
  `2026-07-22-model-hint-provider-routing`.
- **Intent sources**: `[R7.04]` in `REQUIREMENTS.md:273-275`; `[R9.11]` at `:427`; the
  implementation contract in `docs/implement/K-agent-runtime.md:49,75,79`; and the approved
  task dossier `docs/tasks/2026-07-22-model-hint-provider-routing/spec.md`, especially
  §7.4 and AC-6.
- **Depth**: thorough. Three independent investigation lenses (routing lifecycle,
  runtime/error events, caller compatibility), followed by a separate source-trace
  refutation pass for every surviving candidate.

## 2. Coverage

Read in full: `backend/contexts/keys/application/provider_router.py`,
`backend/contexts/agents/application/runtime/turn_engine.py`,
`backend/contexts/agents/application/runtime/summariser.py`, the provider adapter model
contract, key carry/group repositories and revocation event code, all current router/turn
regression tests, the forced-compaction worker, approval worker, and the conversation error
mapping/locales. Also traced GraphRAG, Knowledge Map, embedding, reranking, and Prompt Assistant
router callers.

Not exercised against a live Postgres/Redis/provider stack: the local integration environment
does not resolve its `postgres` host. The confirmed findings are deterministic code-path traces;
the missing live stack limits only timing measurements, not their reproduction conditions.

## 3. Findings

## F-1: Rotation can issue a new provider call after the selected key was withdrawn or removed

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/keys/application/provider_router.py:351-380,421-456`
  loads eligible members once and rotates over that snapshot. `_unwrap_secret` at `:830-842`
  checks only cache or active key row, not current carry or group membership. The intended
  revocation boundary is `[R7.04]` (`REQUIREMENTS.md:273-275`): active outbound calls complete,
  but no new calls issue; `key_revocation_events.py:8-16` likewise distinguishes cache
  invalidation for a withdrawn carry.
- **Failure scenario**: a group contains provider-matched keys A and B. A returns a pre-token
  429 or 500, causing rotation. Before the router starts B's adapter request, the owner
  withdraws B's carry or removes B from the group. The router continues from its stale member
  snapshot and invokes B, creating a new billable provider request after revocation.
- **Blast radius**: withdrawn BYO keys and removed group members can still be charged during a
  rotating request; a membership-revocation action does not take effect at the promised next
  outbound call.
- **Intent source**: `[R7.04]`.

## F-2: The new unserviceable-provider error is rendered as a generic retryable failure

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the room path emits `agent.finished` with
  `error="model_hint_unserviceable"` at
  `backend/contexts/agents/application/runtime/turn_engine.py:1762-1780`. The frontend mapping
  omits that key at `frontend/src/slices/conversation/constants/agentErrors.ts:6-17`; both
  error surfaces therefore use `conversation.chatroom.agentFailed`, whose English copy says
  "Please try again" (`frontend/src/slices/conversation/locales/en.json:66`).
- **Failure scenario**: an agent loses its only carried key for `model_hint=claude`; its next
  room turn correctly skips. The user sees a generic retry message, retries, and receives the
  identical deterministic skip instead of being told to carry/add a Claude key.
- **Blast radius**: every room user and operator encountering the new hard-fail configuration
  state; the new backend audit reason never becomes actionable UI guidance.
- **Intent source**: provider-routing dossier §3 Q-1 and AC-6 require a diagnosable,
  room-visible hard failure rather than silent or advisory fallback.

## F-3: Unserviceable approval-agent skips lose the approval gate's authoritative room in audit

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the approval worker passes its gate room to `run_input_turn` at
  `backend/app/workers/tasks/approvals.py:90-94`. The new preflight audit discards that value
  by passing `None` at `backend/contexts/agents/application/runtime/turn_engine.py:688-700`.
  `_skip_headless` documents the opposite invariant at `:1661-1691`, and its corresponding
  regression test asserts the room is retained at
  `backend/tests/unit/test_a2a_turn_dispatch.py:587-608`.
- **Failure scenario**: an approval gate wakes an agent whose hinted-provider carry was removed.
  The turn skips before draining the pending approval notice; its audit row has no chatroom id,
  so an operator investigating the gate timeout cannot correlate the refusal to the gate room.
- **Blast radius**: approval-gate incidents caused by the new precondition become materially
  harder to diagnose; the existing room-correlated audit contract is bypassed only on this
  early path.
- **Intent source**: `turn_engine.py:1672-1675` and its pinned test define the approval gate
  room as the required correlation identity.

## 4. Refuted Candidates

- GraphRAG and Knowledge Map triple extractors remain intentionally unpinned: they pass
  `provider=None` with a provider-keyed model map (`triple_extractor.py:86-100`,
  `knowmap_triple_extractor.py:82-93`), and the router bypasses the filter for `None`
  (`provider_router.py:761-765`).
- Embedding, reranking, and Prompt Assistant already use a pinned key and scalar model; the new
  group-provider filter is not in their call path (`embedders.py:70-77`, `rerankers.py:64-75`,
  `prompt_assistant.py:111-125`).
- A race after the turn-time provider preflight cannot route to a sibling provider: the router
  independently filters the requested provider and fails closed with `provider_unavailable`
  (`provider_router.py:351-355,421-425`). It may lose the friendlier skip reason, but does not
  violate the routing invariant.
- A custom `model_id` retained across an API-only model-hint patch can be invalid for the new
  provider. The first-party UI clears it on provider switch and arbitrary custom model ids are
  supported, so server-side inference cannot distinguish a valid custom id from malformed
  configuration without a new validation contract.
- Unary quota waiting does not reload group membership. The SRS specifies queue polling but not
  dynamic membership admission while queued, so this is not a confirmed defect.

## 5. Hand-off

| Finding | Decision | Task dossier |
|---|---|---|
| F-1 | selected and repaired | `2026-07-22-model-hint-provider-routing` |
| F-2 | selected and repaired | `2026-07-22-model-hint-provider-routing` |
| F-3 | selected and repaired | `2026-07-22-model-hint-provider-routing` |

## 6. Out-of-scope Observations

- **FU-1** — Forced `/compact` can log `agent.compact_failed` yet the worker returns
  `"completed"` because `_assemble_history` converts `CompactFailed` to unchanged history and
  `run_compaction` then returns `True` (`turn_engine.py:2574-2594,2621-2635`). This predates
  the provider-routing change and belongs with
  `docs/tasks/2026-07-22-compaction-scoping-and-durability/`, which already owns related
  `[R9.11]` compaction durability work.
- **FU-2** — Application-layer dependency inversion in the router and turn engine, and the
  provider-key security threat model, were inspected only to trace behavior. They belong to
  `check-quality` and `check-security`, not this functional audit.
