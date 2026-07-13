# SMAP System Reference & Deep Fit Assessment — NSTC Multi-Agent Creativity-Learning Projects

Status: reviewed (second-confirmation pass complete; deepened against the proposals' research design)
Date: 2026-07-13
Scope: a complete, verified map of everything SMAP-related that bears on the two NSTC (國科會) proposals in `_projects_documents/` — **Part A** is the verified system reference; **Part B** maps SMAP against the proposals' *actual, granular research design* (task taxonomy, cognitive-process signals, knowledge-base experiment, measurement instruments, experimental design).

## Verification legend
- **[V]** = read the source first-hand this session and confirmed. Line numbers cited.
- **[R]** = reported by a scoped code-exploration pass, consistent with adjacent [V] facts but not independently re-read line-by-line here.
- **[P]** = drawn from the proposal PDFs (`_projects_documents/`), cited by section.

"Second confirmation" (二次確認) = every load-bearing system claim that would change a go/no-go decision was promoted from [R] to [V] (see §A.3).

---

# PART A — Verified system reference

## A.1 Verdict

> SMAP is sufficient as the **infrastructure substrate** — the hard 60–70% (real-time multi-party rooms, role-configurable agents, agent-to-agent messaging, approval gates, a workflow DAG engine, BYO-key Gemini integration, full audit) is production-quality and re-verified first-hand.
>
> It is **not turnkey**. The research-differentiating layers — an autonomous Analytics Agent (real-time creativity scoring + intervention over a **structured per-attempt interaction log**), a teacher diagnostics dashboard, and a deterministic Chinese-component knowledge tool — are net-new. Three non-functional realities (operations, token budget, scoring reproducibility) must be actively managed.
>
> **Deepest new finding (Part B):** the proposals' cognitive-process indicators (attempt count, strategy-transition point, error type, reaction time, look-back/fixation) require a **structured task-attempt data model** that SMAP's free-text *message* substrate does not natively provide — and two of those signals (回視/注視) are eye-tracking-derived and unobtainable without hardware. This, not any chat feature, is the true center of gravity of the build.

## A.2 Capability inventory (condensed; full detail in prior revision history)

| Subsystem | What / key files | Verdict |
|---|---|---|
| Real-time rooms (`conversation`) | multi-human + multi-agent, WebSocket, guest links; `sender_type user/agent/system`. **[V]** `access.py:96-122`, `domain/models.py` | native |
| Agents (`agents`) | persona `system_prompt` + provider (claude/openai/**gemini**) + tools (incl. `local_function`, `hosted_mcp`). **[V]** `agents/domain/models.py:129-150` | native (role = convention) |
| Providers / keys (`keys`) | Gemini chat+embed+stream; Vault-Transit envelope. **[V]** `adapters/gemini.py`; catalog `models.py:28-46` | native |
| Orchestration | A2A call/notify/instruct; wakeup; **approval gate (agent-vote only)**. **[V]** `approval_service.py` | native (human-vote caveat) |
| Workflow (`workflow`) | DAG+FSM; triggers `manual/cron/message_received/a2a_event/wakeup`; SEL conditions; approval-gate node parks/resumes. **[V]** `executors/approval_gate.py` | native |
| Observer / observations | OBSERVER role writes creator-only observations; **output severed from all automation**; release is human-only. **[V]** `turn_engine.py:1087-1117`, `observation_service.py` | semi-auto only |
| Knowledge | RAG (Qdrant, fuzzy) + GraphRAG/KnowMap (Neo4j, LLM-extracted). No exact-match/authoring API. **[R]** | RAG native; deterministic lookup absent |
| Prompt studio | versioned templates + LLM co-author; no param→prompt interpolation. **[R]** | partial |
| Tenancy / roles | Org→Project→membership; roles owner/member/admin/guest. **[R]** | teacher/student = mapping |
| Notifications | in-app + WS push; fixed kind enum. **[R]** | partial |
| Audit / export | pervasive event store + CSV/transcript export; 90-day retention. **[R]** | capture native; scoped export small build |
| Deploy / ops | 12-service stack; `/readyz` forces 6 stores; dev-Vault non-persistent. **[V]** `probes/__init__.py:29-36`, `docker-compose.yml:262-272` | operable with active mgmt |

## A.3 Second-confirmation results (verified first-hand this session)

| Claim | Result | Evidence [V] |
|---|---|---|
| Multi-human + multi-agent real-time room | CONFIRMED | `access.py:96-122` |
| Gemini BYO-key end-to-end | CONFIRMED | `adapters/gemini.py` |
| Approval gate real park/resume | CONFIRMED | `executors/approval_gate.py` |
| Observer = Analytics Agent | CORRECTED → semi/absent | `turn_engine.py:1087-1117` ("no room emit, no workflow signal, no reply wake-ups") |
| AA→TA autonomous loop | ABSENT (auto) | `.release()` only via `observations.py`; no `observation` signal source |
| Approval = TA approves SA | AI-vote-only, DAG-only | `approval_service.py`; `wakeup_service.py` no arbiter |
| Reproducible scoring (temp/seed) | PROBLEMATIC | grep `contexts/agents`: no temperature/seed/top_p |
| Token cost on defaults | TIGHT | `agent_service.py:204,331` (every_n=1, GENERAL); adapters no `cache_control` |
| readyz forces Neo4j/Qdrant/MinIO | CONFIRMED | `probes/__init__.py:29-36` |
| Vault dev non-persistence | CONFIRMED | `docker-compose.yml:262-272` (`server -dev`) |

## A.4 Non-functional risks (verified)
1. **Ops — MEDIUM-HIGH for a 1-RA lab.** Dev-Vault loses all keys on reboot (stored Gemini keys become undecryptable); no minimal profile (`/readyz` forces Neo4j+Qdrant+MinIO); silent single-worker failure of the observer/wakeup/approval crons. Recommend dev-mode on a ≥32 GB never-reboot box + a re-bootstrap runbook. **Note the collision with §B.7:** the professor's plan funds exactly **one** full-time developer (張偉倫, 100%) for the whole platform **[P CM06]** — ops + build + integration on one person is the real staffing risk.
2. **Token budget — TIGHT.** GENERAL default resends full history (≤500 msgs) every turn; every_n=1 wakes all 4 agents and cascades; no prompt caching → quadratic cost; no spend cap. NT$25k/2,500M tok ≈ US$0.31/M → viable only at Gemini-Flash tier. Safe setup: pin Flash + COMPACT + low `context_token_cap` + mention-driven turns.
3. **Reproducibility — PROBLEMATIC.** No temperature/seed/top_p exposed → non-deterministic; blocks Cohen's Kappa. Needs a code change to pin temperature=0/seed.
4. **Turn-order chaos.** 4 agents at every_n=1 all fire; no live floor-control. Configure call_only/@mention or choreograph via workflow DAG.

---

# PART B — Deep mapping to the proposals' research design

## B.0 Theoretical grounding (verified against primary literature) [L]

The proposals' design rests on four canonical results in the creativity literature. Each was checked against the primary source this session; each carries a concrete design/system implication. ([L] = literature-verified.)

1. **Standard definition — creativity = originality + effectiveness.** Runco & Jaeger (2012, *Creativity Research Journal* 24(1):92–96) consolidate decades of definitions: a creative response must be **both novel and effective/appropriate**; originality alone is insufficient (a merely-original response can be useless). → **Implication:** the AA cannot reward fluency alone. The proposal's fourth scoring dimension **收斂性/適切性 is precisely this effectiveness criterion — not a Guilford divergent factor** — which is exactly why the proposal uses "有限解答之擴散性思考" (bounded divergence: new characters must be *legal*). The rubric must weight appropriateness against originality.

2. **Divergent-production factors.** Guilford (1956, 1967) defines divergent thinking via **fluency** (number), **flexibility** (category variety), **originality** (unusualness), and **elaboration** (detail); the Alternative Uses Test (1967) operationalizes it. → **Implication:** the divergent tasks (部件旋轉/字詞組合) should be scored on these established axes. Note the proposal substitutes 收斂/適切性 for Guilford's *elaboration* — a deliberate, defensible choice under the standard definition, but state it explicitly so scoring maps to one coherent construct.

3. **Associative theory of creativity.** Mednick (1962, *Psychological Review* 69(3):220–232): creativity is forming associative elements into new, useful combinations — **the more remote the elements, the more creative**; creative individuals have **flat associative hierarchies** (many weak associations) versus **steep** (a few dominant ones). → **Implication:** this is the direct basis both for the remote-association tasks (CRRAT/CWRAT/CCRAP) and for the SA catalyst's designed behaviour. The proposal's "平緩連結層級 / 陸嶠式聯想" *is* Mednick's flat-vs-steep hierarchy; an SA that models "unusual, low-frequency but correct associations" is literally modelling a flat hierarchy.

4. **Insight = representational change through impasse.** Ohlsson's representational change theory (Ohlsson, 1992) frames insight as escaping an **impasse** by re-representing the problem; Knoblich, Ohlsson, Haider & Rhenius (1999, *JEP:LMC* 25(6):1534–1555) operationalize the change via two mechanisms — **constraint relaxation** (loosening self-imposed constraints) and **chunk decomposition** (breaking perceptual chunks into features and recombining) — tested on matchstick arithmetic. → **Implication (strong):** the Chinese-character component tasks are almost a *literal* instantiation of chunk decomposition — a character is a perceptual chunk decomposed into components and recombined; "忽略語義、把部件當純形狀" is constraint relaxation. This validates the proposal's core design choice. It also pins down what the system must detect: **the impasse and the representational-change moment are properties of the solver's *state*, not of a final answer** — which is precisely why a free-text answer log is insufficient and the structured process signals of §B.3 are required.

*References (verified this session): Runco & Jaeger (2012), doi:10.1080/10400419.2012.650092; Guilford, The Nature of Human Intelligence (1967); Mednick (1962), doi:10.1037/h0048850; Knoblich, Ohlsson, Haider & Rhenius (1999), JEP:LMC 25(6):1534–1555; Ohlsson (1992), in Keane & Gilhooly (eds.), Advances in the Psychology of Thinking. All five are also in the proposals' own reference lists.*

## B.1 The research design, precisely reconstructed [P]

**Three creativity dimensions**, each trained at two linguistic levels (部件 component / 字彙 lexical), giving **six core task types**. Examples are the proposals' own:

| Dimension | Component-level task (部件任務) | Lexical-level task (字彙任務) | Cognitive target |
|---|---|---|---|
| 擴散性思考 (divergent) | **部件旋轉與變形**: base「口」→ produce as many legal characters as possible in 5 min (土/尸/回…), via add/remove strokes, distort, rotate | **字詞組合**: base「口」→ produce max legal 2–3-char words (口氣/門口/口頭禪) | fluency, flexibility, originality |
| 頓悟性思考 (insight) | **變形部件轉換**: given 3 components (共/欠/列), find one component that combines with all after transformation (共→火, 欠→灬 → 烘/炊/烈) | **破音字詞轉換**: given 3 chars (聽/服/解), find one char that forms a legal word with each via a heteronym (說: ㄕㄨㄛ/ㄕㄨㄟˋ → 聽說/說服/解說) | representational change, breaking 心向固著 (impasse) |
| 遠距聯想 (remote assoc.) | **部件聯想重組** (CRRAT format): 3 unrelated components (女/子/禾) → one bridge component (乃) forming 奶/孕/秀 | **字彙聯想重組** (CWRAT/CCRAP format): 3 unrelated chars (先/緣/內) → one char (人) forming 先人/人員/內人 | cross-domain association, global efficiency |

**Two teaching modes** (Year 2): 個人式 (solo + TA/AA guidance) vs 合作式 (2–3-person groups; SA plays 催化劑 Catalyst — deliberately injects low-originality / plausible-but-wrong answers to force cognitive negotiation, and stalls the group into 僵局). **[P]**

**Two learning strategies** (Year 3): AI-as-Teacher (TA = authority-directed guide/diagnostician/intervener) vs AI-as-Student (SA = peer catalyst that models 固著, defends 表徵錯誤, and demonstrates 平緩連結層級 remote associations). **[P]**

**Experimental design** (Y2/Y3, per proposal): 60 juniors, any major, random 2×30; 10 weeks (wk1 pretest, wk2–9 eight units each covering all three dimensions, wk10 posttest); delayed posttest. Analysis: paired-sample t-test, **Cohen's Kappa (AA scoring vs human raters)**, reaction-time (teacher awareness), NASA-TLX (teacher cognitive load), time-series output-rate. **[P]**

## B.2 Task-by-task system fit

The six task types are **not equally system-friendly**. The decisive axes are (a) answer format and (b) whether the interaction *requires* visual manipulation.

| Task | Answer format | Needs visual manipulation? | Auto-scorable? | SMAP fit |
|---|---|---|---|---|
| 部件聯想重組 (遠距, 部件) | single target component, typed | No | **Yes** — fixed answer key (乃) | **Best** — text Q&A, auto-scorable |
| 字彙聯想重組 (遠距, 字彙) | single target char, typed | No | **Yes** — key (人) + word legality via corpus | **Best** |
| 破音字詞轉換 (頓悟, 字彙) | single char + heteronym, typed | No | Yes — key + heteronym check | Good; impasse signals needed |
| 變形部件轉換 (頓悟, 部件) | single component, typed; but "transformation" (共→火) is conceptual | Marginal — describable in text | Yes — key, but transformation reasoning is the point | Good; impasse signals needed |
| 字詞組合 (擴散, 字彙) | many words, typed list | No | Partial — legality via corpus; originality/flexibility need rubric | Medium — multi-answer rubric |
| 部件旋轉與變形 (擴散, 部件) | many characters, produced by rotating/distorting components | **Yes (ideally)** — rotation/stroke edits are spatial | Hard — 73 possible answers, six response categories | **Hardest** — needs visual canvas + rich rubric |

**Depth implication for the meeting and the build:**
- The **remote-association dimension is the easiest to ship and to validate** (single-answer, auto-scorable against a known key) — it should be the MVP's first vertical slice.
- The **divergent component task** (part rotation/distortion) is the one that most plausibly needs a **visual manipulation UI** and the richest scoring rubric (the 六大類反應 below). This is the single largest activity-design decision (text vs visual) — see the meeting questions doc §A.
- Insight tasks are text-answerable but their *value* is the impasse→resolution process, which depends entirely on §B.3.

## B.3 The cognitive-process signal problem (the deepest gap)

The Analytics Agent is defined in both proposals by the **process signals** it tracks, not by a final answer: **嘗試次數** (attempt count), **策略轉換點** (strategy-transition point), **錯誤類型** (error type), **反應時間/產出速率** (reaction time / output rate), **回視行為** (look-back) and **注視時間** (fixation time). Impasse is operationalized behaviorally, e.g. *"1 分鐘內重複聯想錯誤、注視時間過長"*. **[P Y3 unit example]**

Mapping these to SMAP:

| Signal | SMAP capture today | Verdict |
|---|---|---|
| Attempt count | messages exist, but an "attempt" is not a first-class typed event — you'd infer it from free-text | **needs a structured attempt model** |
| Strategy-transition point | not represented; requires typed, categorized attempts to detect a shift | **net-new** |
| Error type | not represented; requires per-attempt validation + an error taxonomy | **net-new** |
| Reaction time / output rate | message timestamps exist; per-attempt latency needs a structured submit event | **partial (needs structure)** |
| 回視 look-back / 注視 fixation | **eye-tracking only** — no hardware, no equivalent in a web chat | **impossible without hardware; use behavioral proxies** |

**This is the core architectural insight:** SMAP's substrate is a *conversation* (one message = one free-text utterance). The proposals' AA needs a *task-attempt log* (one row = one typed, timestamped, auto-validated submission against a known answer key, with an error class). These are different data models. To make the AA's Kappa-validated scoring and impasse detection defensible, the activities should be delivered as **structured task submissions** (a small new task/attempt schema + an auto-scoring service), with the chat room used for the *social/dialogic* layer (TA/SA talk, hints, negotiation) on top. Trying to make the AA infer attempt-count/strategy-shift/error-type from raw chat text is lossy and hard to validate.

Two of the proposals' signals (回視/注視) are eye-tracking-derived from the co-PI's prior eye-movement studies (Huang 2017/2019). The platform can only offer **behavioral proxies** (dwell-before-submit time, repeated same-error count, revision count). This substitution should be explicitly acknowledged in the research design and to reviewers — it is a genuine limitation of a software-only platform vs the lab's eye-tracking heritage.

## B.4 Knowledge-base experiment (V0→V3) mapped to SMAP [P Y1]

The proposal's Year-1 accuracy experiment (baseline LLM <50% on 部件分解/組合/延伸組合; target >95%) is a four-rung ladder that maps cleanly onto SMAP's agent primitives:

| Rung | Proposal definition | SMAP realization |
|---|---|---|
| V0 | plain LLM, no help | an agent with a bare `system_prompt` |
| V1 | + definitions of Chinese components | definitions inlined into `system_prompt` |
| V2 | + learn the component expert database | RAG/KnowMap over the database (fuzzy) **or** a lookup tool |
| V3 | + instruction on *how to use* the DB + expert-persona reasoning | **`local_function` / `hosted_mcp` tool** exposing exact decomposition/composition + a persona `system_prompt` |
| result | GPT-4.1 + DB + optimized instructions → **>95%** | achievable via the tool path |

**Depth conclusion:** the proposal itself proves that ">95%" comes from *structured knowledge + tool-style use*, not from a bare model. SMAP's **deterministic tool primitive is the right home for V2/V3** — the 439-component / 11-spatial-relation / ~6100-character database (陳學志 2011) becomes a callable exact-lookup tool, not a RAG corpus (RAG's fuzzy retrieval would reintroduce the <95% error the experiment is designed to remove). RQ1 validation (random 50 decomposition + 50 composition items, human-checked) is straightforward once the tool exists. Supporting corpora named in the proposal — 現代漢語語料庫 (中研院), 1200 中文雙字詞聯想常模 (胡中凡 2017), CLAD (Lin 2019) — are additional structured references, best exposed as tool/lookup data rather than embeddings.

## B.5 Measurement instruments & reproducibility [P]

The success metrics rely on established instruments; their system implications differ sharply:

- **有限解答之擴散性思考測驗** (陳學志 2009): 5 min, base「里」, add/remove strokes to form new legal characters (rotatable), max **73** correct, scored into **six response categories** (原始不旋轉-保留結構 / 原始不旋轉-破壞結構 / 原始不旋轉-部件扭曲 / 旋轉180° / 旋轉90° / 末端延展扭曲). → the richest rubric; the AA's divergent scoring must reproduce these six categories → strong case for a visual-submission format so category assignment is deterministic rather than inferred from prose.
- **Delis-based 字彙流暢** (60 s): base「英」→ 英俊/英才/石英 (exclude names/places), legality via 現代漢語語料庫. → auto-scorable against a corpus.
- **CRRAT** (張雨霖 2016) and **CCRAP** (Wu & Chen 2017): single-target, fixed answer key → **directly auto-scorable**; ideal for Kappa validation because the "correct" answer is unambiguous and only the *originality/creativity dimension* needs human comparison.

**Reproducibility tie-in:** the whole Kappa argument (AA vs human raters) presumes the AA scores the *same* way across runs. With temperature/seed not exposed (§A.3), AA scores drift. The auto-scorable instruments (CRRAT/CCRAP, corpus-legality) are robust because correctness is deterministic; but the *creativity-dimension* scoring (originality rarity, the six divergent categories) runs through the LLM and therefore needs the temperature/seed fix before Kappa is defensible. Recommend: split scoring into a **deterministic layer** (answer-key + corpus legality, code) and an **LLM-judged layer** (originality/flexibility), and pin the latter.

**In-system vs external:** pretest/posttest/delayed-posttest instruments should be administered as **structured, timed assessments** (same attempt-schema as §B.3), separate from the training tasks to avoid practice effects — which the proposals explicitly require. This is another argument for the structured task/attempt model over chat.

## B.6 Dimension difficulty ordering (by system-fit)

A concrete sequencing recommendation falls out of B.2–B.5:

1. **遠距聯想 first** — text Q&A, single-answer, auto-scorable, cleanest Kappa. Best MVP slice; exercises the full agent/room/observer/scoring loop with the least ambiguity.
2. **頓悟 second** — text-answerable, but requires the impasse-detection process signals (§B.3); build the attempt-log here.
3. **擴散 last** — needs the six-category rubric and most plausibly a visual manipulation canvas; highest engineering and scoring cost.

This ordering also de-risks the study: validate the pipeline on the dimension where "correct" is unambiguous before taking on rubric-heavy divergent scoring.

## B.7 The proposals' own flagged challenges → system reality [P]

The professor's proposal lists four risks; each maps to a concrete SMAP reality:

| Proposal challenge | Proposal's mitigation | SMAP reality |
|---|---|---|
| 1. AI component-operation accuracy imperfect | deep-integrate expert DB + instruction tuning | **matches** the V3 deterministic-tool path (§B.4) — the right call |
| 2. Dynamic guidance can't diagnose 僵局 in time | real-time monitoring on cognitive-process indicators | **the hard part** — needs the attempt-log + impasse detector; the observer channel alone won't do it (§B.3) |
| 3. Insight training degrades to "假頓悟" | strictly follow pure-insight problem design | design-side; system must present pure-insight items and detect the impasse→resolution transition (needs §B.3 signals) |
| 4. Results confounded by high verbal knowledge | use component-level (CRRAT) as core stimulus | **fits** the auto-scorable remote-association slice (§B.2, B.6) |

Note the honest reframing: the proposal's own Challenge 2 is the same gap Part B identifies as the center of the build — the cognitive-process signal capture. The proposals already know this is the crux; the platform work should be scoped around it.

---

## C. Net-new build backlog (ranked, updated with Part B depth)

1. **Structured task/attempt model + auto-scoring service** — typed, timestamped submissions against answer keys, with an error taxonomy; the substrate for the AA's process signals and for Kappa-clean scoring. *(Both; the true foundation — new insight from Part B.)*
2. **Analytics Agent proper** — creativity 4-dimension scoring (with the six divergent categories) + impasse / strategy-transition / error-type detection over the attempt log + structured diagnostic report. *(Both; core deliverable.)*
3. **Teacher analytics dashboard** — per-group status lights, output-rate trend, watchlist, diagnostics. *(Both.)*
4. **Chinese-component deterministic knowledge tool** — the 439/11/~6100 database as `local_function`/MCP (V2/V3 path). Not RAG. *(Professor Y1.)*
5. **Reproducible scoring** — expose temperature/seed, pin snapshot model, split deterministic vs LLM-judged scoring. *(Undergraduate hard requirement; both.)*
6. **Autonomous observation→intervention path** — observation-sourced workflow signal so AA drives TA/SA without a human. *(Professor; MVP can stay human-gated.)*
7. **Visual component-manipulation canvas** — only if the divergent part-rotation task must be spatial rather than text. *(Decide at the meeting; can defer.)*
8. **Operational hardening** — lab compose profile, persistent Vault + recovery runbook, worker alerting, budget kill-switch. *(Both; collides with the single-developer staffing.)*
9. **Design Agent param→prompt generation**; **live turn-order/floor-control** (or DAG choreography). *(Both; lower priority.)*

## D. Open decisions
1. **Delivery model** — deliver activities as **structured task submissions** (recommended, enables the AA + Kappa) or as free chat (simpler, but the AA's process signals become lossy)? This is now the pivotal decision, ahead of even the auto-loop question.
2. **Visual vs text** for the divergent part-rotation task (§B.2/B.6).
3. **Professor's autonomous AA→TA loop** — grant novelty vs human-in-the-loop MVP.
4. **Deployment** — full stack (≥32 GB, Vault risk, one developer) vs a trimmed lab profile.
5. **Model/budget policy** — mandate Gemini-Flash + COMPACT + mention-driven turns.

## Appendix — verified file anchors
- Rooms/access `conversation/application/access.py:96-122`; agents/defaults `agents/domain/models.py:28-150`, `agent_service.py:204,331`, `runtime/turn_engine.py:1087-1156`; Gemini `keys/infrastructure/adapters/gemini.py`; orchestration `orchestration/application/approval_service.py`, `wakeup_service.py`; workflow `workflow/application/executors/approval_gate.py`, `run_engine.py`, `event_dispatch.py`; observations `conversation/application/observation_service.py`, `app/api/v1/observations.py`; ops `deploy/compose/docker-compose.yml:262-272`, `shared_kernel/infra/probes/__init__.py:29-36`, `app/workers/main.py`.
- Proposal anchors: task tables (教授計畫 研究方法 一、系統開發 第二/三年); knowledge-base V0-V3 (第一年 (一)); measurement tools (三、成效評估的評量工具); experimental design + metrics (二、實證研究); personnel (表CM06); budget/tokens (表CM08).
