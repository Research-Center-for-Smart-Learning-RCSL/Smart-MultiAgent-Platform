# SMAP Fit Assessment — Undergraduate NSTC Multi-Agent Creativity Proposal

Status: initial
Date: 2026-09-13
Scope: code-verified mapping of the undergraduate NSTC proposal
("基於多重 AI Agent 架構的 AI 賦能合作學習平台之系統開發與成效評估：
以採用中文字屬性培養學生的創造力為例") against SMAP's current codebase.

Cross-references:
- `nstc-multiagent-fit-assessment.md` — professor's multi-year proposal (2026-07-13)
- `nstc-meeting-activity-design-questions.md` — meeting discussion outline (2026-07-13)
- `nstc-meeting-program-planning.md` — program planning document (2026-07-15)

This document covers the **undergraduate proposal only** — a 27-week MVP with
20–30 students, four agents, and three Chinese-character creativity tasks. The
professor's multi-year proposal (60 students, six task types, eye-tracking signals,
knowledge-base V0–V3 experiment) is documented separately in the files above.

## Verification legend

- **[V]** = source read first-hand this session and confirmed
- **[D]** = delegated to a scoped code-exploration agent, cross-checked against
  adjacent [V] facts
- **[P]** = drawn from the proposal PDF (`_projects_documents/大專生國科會計畫.pdf`)

---

## 1. Verdict

SMAP covers the undergraduate proposal's technical requirements to a degree
substantially higher than the July 2026 assessment of the professor's proposal
found, because three subsystems that were then nascent or absent are now
production-grade:

1. **Structured activities** (`contexts/activities/`) — typed task submissions with
   `sub_scores`, group proposals with voting, pluggable validators, and a frontend
   plugin SDK (`defineActivityPlugin`). A shipped `creative-thinking.json` example
   course already demonstrates the pattern the proposal needs.

2. **Observer pipeline** (`AgentObservation` + `ObserverPanel.vue` + six observation
   block types) — observer agents produce private reports visible only to the room
   creator, with a safety model that separates agent-authored narrative blocks from
   server-computed data blocks.

3. **Agent sampling reproducibility** — `temperature`, `top_p`, and `seed` are now
   exposed on the `Agent` model. The July assessment's "PROBLEMATIC" verdict on
   reproducibility no longer applies.

**Quantified coverage: 14 of 26 requirements fully built, 7 partially built,
5 need new work.** The five gaps are all in the education-domain vertical layer
(teacher dashboard, creativity scoring, task content, reaction-time tracking,
research data export), not in the platform core.

---

## 2. Four-Agent Mapping (code-verified)

### 2.1 Teacher Agent (TA)

| Proposal requirement [P] | SMAP capability | Evidence |
|---|---|---|
| Gemini API integration | `Agent.model_hint = gemini`; `adapters/gemini.py` | [V] `agents/domain/models.py:147` |
| Free-text system prompt defining scaffolding | `Agent.system_prompt: str` (unlimited) | [V] `agents/domain/models.py:155` |
| Approve/reject SA intervention requests | `ApprovalGateConfig` with `mode=SINGLE`, TA as leader | [D] `orchestration/application/approval_service.py` |
| Record approval rationale | `ApprovalVote.rationale: str` | [D] `orchestration/domain/models.py` |
| Highest-priority agent in hierarchy | No first-class role field; hierarchy is emergent from system prompt + approval config + wakeup config | [D] |
| Activity control delegation | `ChatroomAgent.may_control_activities = True` with `activity_type_allowlist` | [D] `conversation/domain/models.py` |
| Real-time creativity scoring (4 dimensions) | `ActivitySubmission.sub_scores: dict` can carry any dimensions; scoring **logic** is net-new | [D] `activities/domain/models.py` |

**Shipped example**: `creative-thinking-room.json` includes `ta-guidance-teacher`
with `every_n_messages: 1`, detailed pedagogical system prompt, and activity
control grants. [D]

### 2.2 Student Agent (SA)

| Proposal requirement [P] | SMAP capability | Evidence |
|---|---|---|
| Peer-style persona via prompt engineering | `system_prompt` defines persona/tone | [V] |
| Trigger on prolonged silence | `WakeupConfig.silence_minutes` trigger | [D] `orchestration/domain/models.py` |
| Must request TA approval before intervening | Two paths: (1) workflow `approval_gate` node; (2) A2A `CALL` + `InstructService` | [D] |
| Anti-loop protection | `autostop_rounds` resets only on user messages | [D] `orchestration/application/wakeup_service.py` |

**Shipped example**: `sa-peer-catalyst` with `silence_minutes: 3`. [D]

### 2.3 Design Agent (DA)

| Proposal requirement [P] | SMAP capability | Evidence |
|---|---|---|
| Transform teacher parameters into TA/SA system prompts | **Not built as described.** `prompt_studio` is an interactive chat assistant for human prompt authors, not a parameter-to-prompt engine. | [D] `prompt_studio/application/prompts.py:14-22` |
| Operate in backend only, no student interaction | Any agent can be excluded from chatrooms; workflow `agent_invocation` can call DA headlessly | [D] |

**Gap**: The proposal's DA ("teacher sets parameters on dashboard, DA auto-generates
structured prompts") requires: (1) a parameter-setting UI, (2) a template engine
or LLM-driven generation pipeline, (3) auto-writeback to Agent.system_prompt. The
building blocks exist (workflow `agent_invocation` + `PromptTemplate` + `set_variable`
node) but the assembled flow does not.

### 2.4 Analytics Agent (AA)

| Proposal requirement [P] | SMAP capability | Evidence |
|---|---|---|
| Observer role (silent, teacher-only reports) | `ChatroomAgent.role = OBSERVER`; `AgentObservation` private to room creator | [D] `conversation/domain/models.py` |
| Structured data feed from activities | `ActivityContextProvider` injects `[Recent room activity]` block into every agent's context window | [D] `activities/application/activity_context_provider.py` |
| Structured report with computed blocks | Six block types: `prose`, `key_points`, `timeline`, `field_coverage`, `mandala_grid`, `attempt_table` | [D] `agents/application/runtime/observation_blocks.py` |
| Selective release to room or specific agents | `ObservationService.release()` with content override, target agent wake-up | [D] |
| Detect TA/SA errors and notify | A2A `NOTIFY` (fire-and-forget) to TA; `NotificationService.send()` for in-app push to teacher | [D] |
| Synchronous analysis (periodic/on-demand) | `WakeupConfig` with `every_n_messages` or `silence_minutes`; teacher can @mention | [D] |
| Asynchronous analysis (continuous) | Observer receives all messages; `ActivityContextProvider` feeds structured data | [D] |

**Strongest fit of the four agents.** The observer pipeline's safety model (computed
blocks carry only server-measured values, never model-supplied numbers) directly
addresses the proposal's concern about AA report trustworthiness.

---

## 3. Activity System Mapping

### 3.1 Structural capabilities (all [D]-verified)

| Capability | Status | Key classes |
|---|---|---|
| Typed task templates with JSON Schema validation | Built | `ActivityType.payload_schema` |
| Per-participant session tracking | Built | `ActivitySession` (polymorphic: user or member group) |
| Scored submissions with sub-scores and latency | Built | `ActivitySubmission.sub_scores`, `latency_ms` |
| Pluggable validators (in-process / MCP / webhook) | Built | `ValidatorKind` enum; 3 shipped validators |
| Group proposals with voting and consent fractions | Built | `GroupProposal`, `ProposalVote`, `VoteChoice` |
| Agent-delegated activation (agent starts/ends rounds) | Built | `ActivityActivation.started_by_agent_id` |
| Frontend plugin SDK | Built | `defineActivityPlugin` with sandboxed contract |
| Shipped creative-thinking example | Built | `creative-thinking.json` (5 activity types from NTNU thesis) |

### 3.2 Three creativity tasks — what needs building

| Task [P] | Payload schema | Validator approach | Frontend plugin | Effort |
|---|---|---|---|---|
| **CRAT (字詞拼圖)** | 3 clue characters + target answer | `exact_match` for known-answer; `mcp` for open-ended | Simple text input | Low |
| **拆字蓋樓** | Radical/component + generated character list | Custom `in_process` validator checking legal characters via corpus | List input with timer | Medium |
| **萬物新解** | Object name + reinterpretation text | `mcp` validator with LLM-judged originality | Free text input | Medium-high (scoring) |

The `SchemaForm.vue` generic renderer can handle all three as JSON Schema forms
without writing custom plugins, though custom plugins would provide a better UX.

### 3.3 Scoring dimensions

The proposal's four dimensions map to `ActivitySubmission.sub_scores` keys:

| Dimension [P] | Scoring method | Deterministic? |
|---|---|---|
| Fluency (流暢性) | Count of valid submissions per time unit | Yes — computable from `created_at` + `is_valid` |
| Originality (原創性) | Semantic distance / rarity against corpus | No — needs LLM or corpus lookup |
| Flexibility (變通性) | Count of distinct concept categories | No — needs categorization (LLM or taxonomy) |
| Convergence (收斂性) | Reasonableness/practicality of best answer | No — needs LLM judgment |

The July assessment's recommendation to split scoring into a **deterministic layer**
(answer-key + corpus legality) and an **LLM-judged layer** (originality/flexibility)
remains the right architecture. See `nstc-multiagent-fit-assessment.md` §B.5.

---

## 4. Infrastructure Mapping

### 4.1 Student groups

`MemberGroup` (in `tenancy/domain/models.py`) supports named subsets of project
members. Chatrooms can be restricted to specific groups via
`ChatroomMemberGroupRepository.replace()`. `ActivitySession` supports group
subjects via `subject_member_group_id`. The proposal's "5–7 collaborative learning
groups" maps directly. [D]

### 4.2 Real-time communication

Full WebSocket infrastructure: per-chatroom channels, presence tracking,
agent status streaming (`agent.thinking/token/finished`), silence-trigger
evaluation on join/leave. [D]

### 4.3 Audit and data export

`AuditEntry` with append-only logging, structured metadata (JSONB), and
`AuditQueryService.export_csv()` with REPEATABLE READ snapshot. All agent
actions, approval votes, observation releases emit audit events. [D]

### 4.4 Notification

In-app only (WS push via Redis pub/sub). `NotificationKind` enum is extensible.
Adding new kinds for AA alerts (e.g., `CREATIVITY_ALERT`, `IMPASSE_DETECTED`)
requires only an enum value addition and a `send()` call. [D]

### 4.5 Ethics and privacy

| Proposal requirement [P] | SMAP capability |
|---|---|
| Disclose AI identity to students | `disclose_observers` flag + `ObserverDisclosureChip.vue`; SA disclosure needs system prompt or UI treatment |
| Retain student names for tracking, de-identify for analysis | `AttemptSummary` uses truncated participant codes |
| Encrypted data storage | Vault Transit envelope encryption for API keys; standard DB encryption at rest |
| Data destruction after study | `ActivityType.retention_days` for automated purge; manual deletion for other data |

---

## 5. Gaps — Net-New Build Items

Ordered by impact on the research:

### 5.1 Teacher dashboard (cross-room aggregation view)
**Status**: Not built. Largest greenfield item.

The proposal requires a dashboard with: status indicators per group (traffic lights),
output trend charts, and a student watchlist. SMAP's frontend has **no charting
library** (no line charts, bar charts, or trend visualizations anywhere), no
cross-chatroom aggregation queries, and no threshold-based alerting UI. The backend
has per-room aggregates (`ActivityAggregate`, `FieldCoverage`, `AttemptSummary`)
but no cross-room rollup endpoints.

Needed: new frontend View, backend aggregation API, charting library integration,
WebSocket/polling for live updates.

### 5.2 Creativity four-dimension scoring validators
**Status**: Framework exists, domain logic does not.

Fluency is computable from existing data. Originality, flexibility, and convergence
need either corpus-based heuristics or LLM-judged scoring via `mcp` validators.
The `ValidationResult` model and `sub_scores` field are ready to carry the results.

### 5.3 Three Chinese-character creativity tasks
**Status**: Activity framework exists, task content does not.

Each task needs: `ActivityType` definition (payload schema), frontend plugin or
`SchemaForm` config, and a validator. The `creative-thinking.json` example course
provides a pattern to follow.

### 5.4 Reaction-time tracking (RQ2)
**Status**: Not built.

The proposal requires measuring time from "AA issues alert" to "teacher takes
action." `ObservationService` records observation creation timestamps but does not
track when the teacher first views or responds. Needs event-pairing logic between
observation creation and teacher's subsequent action (activity start, message send,
or observation release).

### 5.5 Research data export API
**Status**: Partial.

`AuditQueryService.export_csv()` exists for audit logs. A cross-chatroom activity
submission rollup with de-identified participant codes (for import into SPSS/R)
does not exist. `AttemptSummary` already implements truncated codes but is scoped
to single rooms.

---

## 6. Architectural Constraints

### 6.1 Approval gates are agent-only

`ApprovalService` accepts votes only from agents with
`workflow_capabilities.can_approve`. There is no human-in-the-loop voting path.
If the proposal requires the **teacher themselves** to click approve/reject in the
UI (rather than TA acting as the teacher's proxy), the approval system needs a
new code path.

**Recommendation**: Accept TA-as-proxy. The teacher controls TA's behavior via
system prompt; TA exercises approval authority on the teacher's behalf. This is
consistent with the proposal's own description ("TA 接收後可參考該提示內容，但仍保有
最終決定權"). The teacher's "final decision" is exercised by configuring TA, not by
casting votes.

### 6.2 Wake-up triggers are structural, not content-aware

`WakeupService` triggers on message count, silence duration, or explicit mention.
It cannot trigger on content patterns ("when topic X is discussed" or "when a
student hasn't contributed in 3 turns"). Content-aware triggering must happen
inside the agent's turn (LLM reasoning within the system prompt), not at the
wake-up layer.

### 6.3 Observer observations are severed from automation

`AgentObservation` output does not feed back into workflow triggers or wake up
other agents automatically. The `release()` method is human-initiated (room
creator only). For the proposal's "AA detects error, notifies TA" flow, AA must
use A2A `NOTIFY` during its turn, not through the observation pipeline.

This was flagged in the July assessment as "observation-sourced workflow signal
absent" and remains the case. For the undergraduate MVP, the A2A `NOTIFY` path
is sufficient.

---

## 7. Changes Since July 2026 Assessment

| Item | July 2026 status | September 2026 status |
|---|---|---|
| Structured task/attempt model | "needs a structured attempt model" | **Built**: `ActivitySubmission` with `sub_scores`, `latency_ms`, `validation_status`, `error_class` |
| Agent sampling reproducibility | "PROBLEMATIC — no temperature/seed/top_p" | **Fixed**: `Agent.temperature`, `top_p`, `seed` exposed |
| Activity context for agents | Not mentioned | **Built**: `ActivityContextProvider` injects structured data into agent turns |
| Observer presentation blocks | Not mentioned | **Built**: 6 block types with safety model (computed vs narrative) |
| Creative-thinking example pack | Not mentioned | **Shipped**: `creative-thinking-room.json` with TA/SA/Observer roles |
| Group proposals with voting | Not mentioned | **Built**: `GroupProposal`, consent fractions, pinned voter lists |
| Frontend activity plugin SDK | Not mentioned | **Built**: `defineActivityPlugin` with sandboxed contract |
| Approval gates | "AI-vote-only, DAG-only" | Unchanged — still agent-only |
| Observer→automation loop | "ABSENT (auto)" | Unchanged — still human-gated release |
| Teacher dashboard | "net-new" | Unchanged — still net-new |

---

## 8. Relationship to Prior Assessments

The undergraduate proposal is a **subset** of the professor's multi-year proposal.
Key simplifications:

1. **Three task types** (CRAT, 拆字蓋樓, 萬物新解) vs six in the professor's proposal
2. **No eye-tracking signals** — the undergraduate proposal does not mention 回視/注視
3. **No knowledge-base V0–V3 experiment** — assumed as a given ("fixed model version")
4. **No visual manipulation canvas** — all tasks are text-based
5. **27-week timeline** vs multi-year
6. **20–30 students** vs 60
7. **MVP positioning** — explicitly disclaims commercial-grade engineering

The July assessment's core architectural insight — that the AA needs a structured
task-attempt log, not free-text chat inference — has been addressed by the
activities context build. The July assessment's build backlog items #1 (structured
attempt model) and #5 (reproducible scoring) are now resolved; items #2 (AA
scoring), #3 (dashboard), and #9 (DA param→prompt) remain as the undergraduate
proposal's gaps.
