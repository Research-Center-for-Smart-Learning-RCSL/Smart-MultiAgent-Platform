---
type: feature
status: implemented
created: 2026-10-19
requirements: [R13.75, R13.76, R30.35, R33.10]
depends_on: []
---

# Peer agents deliberately propose wrong answers in the three college course packs

## 1. Summary

The NSTC research design (local research plan `_projects_documents/量表編製計畫.md`, sections 4.9 and 9.3, git-ignored) assumes that the AI classmate (SA, the peer agent) in the college Chinese-character creativity courses sometimes proposes wrong answers on purpose, so that students practise judging an AI contribution instead of accepting it. Today no peer prompt does this: the CRAT peer only offers a self-flagged near miss, and the character-building peer is required to share only characters that really can be built. This task makes the peer in the three college course packs propose a deliberately wrong answer for about one in three of its own answer proposals, has each teacher agent (TA) tell students at the opening that the AI classmate's ideas may be wrong, and keeps the TA from settling the peer's answer before students have judged it. The change is prompt-only, in the six course pack files, plus tests, the adoption doc and one new requirement.

## 2. Goals and Non-goals

**Goals**
- The SA in `crat-word-puzzle-room`, `character-building-room` and `object-reinterpretation-room` proposes a deliberately wrong answer for about one in three of its own answer proposals, using an error it has itself checked to be wrong, presented in the same confident, first-person way as its other proposals.
- The SA handles a challenge to any of its proposals the same way: it concedes when the challenge gives a valid reason, explains its reasoning once when the challenge is invalid, and never says or implies that an error was deliberate.
- Each of the three TAs tells the group at its opening step that the AI classmate's ideas may be wrong and that students should judge them themselves, without saying the errors are deliberate.
- Each of the three TAs, when the SA has just proposed an answer, neither confirms nor corrects it before at least one student has responded to it, and then steers verification through its existing guidance rather than announcing a verdict.
- The three course designer agents (DA) carry both rules into any TA or SA draft they write, so a teacher who drafts a new room from a designer keeps the behaviour.
- Tests pin every rule above, and pin that the creative-thinking pack does not carry it.

**Non-goals**
- No backend, API, schema, frontend or export change. No hidden "deliberate error" marker is recorded anywhere (see Q-7 and FU-1).
- No change to `creative-thinking-room` or `creative-thinking-design` (junior-high guidance course; deliberate errors are inappropriate there).
- No change to the analyst agents (AA) or to what they observe.
- No exact ratio enforcement. The ratio is model guidance derived from the visible transcript and is documented as such.
- No update of already-installed agents. Installs are copies; adopters reinstall or paste by hand (`docs/examples/chinese-character-courses-adoption.md:99-123`).
- No change to the phase-2 cadence, bank, invitation or answer-secrecy rules, beyond the new lines this task adds next to them.
- No teacher-facing switch to turn the behaviour on or off (Q-3 rejected a configurable toggle).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Write a dossier first or implement directly? | Dossier first | Touches six pack files, tests, docs and an LLM prompt surface; project rule for 3+ files. |
| Q-2 | Which packs? | The three college packs: CRAT word puzzle, character building, object reinterpretation | Chosen by the user over "CRAT and character building only". Creative-thinking (junior-high guidance) is excluded. |
| Q-3 | How often and in what form? | A fixed ratio | Chosen by the user over "at most once per activity" and a teacher-configurable toggle (the toggle needs backend and frontend work). |
| Q-4 | Which branch? | `feat/peer-deliberate-errors`, cut from `feat/crat-facilitation-phase2` | Keeps phase 2's latest prompt text; the two tasks keep separate commits. |
| Q-5 | Should this dossier depend on `2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence` (in progress, same six files, live ACs 14/20/21/24 still open)? | No dependency; implement now | User decision. The overlap risk is handled operationally in §10 (do not deploy this branch to rooms used for phase-2 live runs until those runs finish). |
| Q-6 | Ratio value? | About one in three of the SA's own answer proposals | Recommended option; at the SA's low speaking rate this yields roughly one error per activity. |
| Q-7 | How will researchers know which peer contributions were deliberately wrong? | Post-hoc judgement by raters, no code | The SA has no private write surface (§4.4), message metadata is readable by students and not exported (§4.5). CRAT and character-building errors are rule-checkable; the research coding scheme Q2 already rates AI-suggestion correctness. A hidden marker is FU-1. |
| Q-8 | What counts as "wrong" in object reinterpretation, which has no correct answer? | A use the object cannot actually perform given its material or size | Recommended option over "wrong transformation level" and "both". The existing safety exclusions (fire, eating, projectiles) still apply. |
| Q-9 | What do students get told? | At the opening: the AI classmate's ideas may be wrong and students should judge them; never that errors are deliberate. Debrief after the study. | Research decision of 2026-10-19 (research plan section 9.3); the debrief belongs to the ethics protocol, not to the platform. |

## 4. Current State

### 4.1 Pack layout

Each room pack holds TA, SA and AA; the `system_prompt` strings are on `*-room.json:26` (TA), `:48` (SA) and `:66` (AA) in all three college room files under `backend/contexts/agents/infrastructure/examples/packs/`. Each designer pack holds one DA prompt at `*-design.json:24`. SA carries `room_role: "normal"`; the only room roles are `normal` and `observer` (`backend/contexts/conversation/domain/models.py:41-43`).

### 4.2 Peer prompts today

- **CRAT** (`crat-word-puzzle-room.json:48`): speaks only on a 3-minute lull or when addressed, once per lull (temperature and wake-up at `:34-44`). Strategy 3 offers a near miss that the SA itself flags ("我本來想到 X 這個字，但第二個線索搭不上"). Boundaries 4 and 5 forbid giving what it believes is the correct answer and forbid guessing an answer "說成是正確的"; it says it does not know the answer.
- **Character building** (`character-building-room.json:48`): silent while the worksheet is open; in the sharing phase it shares one or two characters with its reasoning. Boundary 3 requires "確保你分享的字確實可以由給定部件組成".
- **Object reinterpretation** (`object-reinterpretation-room.json:48`): silent while the form is open; shares one idea with its transformation level; boundary 3 forbids dangerous uses (fire, eating, projectiles).

### 4.3 Teacher prompts today

- **CRAT** (`crat-word-puzzle-room.json:26`): opening is `<步驟A1>`; TA knows the bank `<題庫>` and must not reveal it before submission; C1 already says "若小組收斂到錯誤答案 → 不直接糾正，引導驗證"; C2 says not to endorse unverified words; group rule 6 keeps TA silent after an SA message unless a lull rule applies; B1 names the SA as the one who shares first on a lull.
- **Character building** (`character-building-room.json:26`): opening `<步驟A1>`; D1 gives concrete feedback on any share and may compare it with a reference character. Nothing distinguishes an SA share, so today the TA could confirm or correct an SA character immediately.
- **Object reinterpretation** (`object-reinterpretation-room.json:26`): opening `<步驟A1>`; D2's third follow-up layer already asks whether an idea is feasible; E1 compares with reference uses without calling them correct.
- The `<邀請發言>` block must stay byte-identical across the three TAs (`backend/tests/unit/test_agent_example_packs.py:869-892`).

### 4.4 What the peer can see and write

- History: the last 500 room messages (`backend/contexts/agents/application/runtime/transcript.py:66`, `:185-230`). Its own messages arrive as unprefixed assistant turns (`backend/contexts/agents/application/runtime/turn_engine.py:4371-4373`), so it can count its own earlier proposals. Silent turns leave no message (`turn_engine.py:3601-3611`). In `compact` context mode older messages become a summary (`turn_engine.py:4451-4460`), which weakens counting.
- No persistent memory for a normal-role agent (the observer memory block is observer-only, `turn_engine.py:3097`, `:3950-3968`). No tool lets a normal agent write a hidden note or message metadata (`backend/contexts/agents/application/runtime/tool_registry.py:119-148`, `:882-885`; `present_observation` is observer-only, `observer_tools.py:108`).

### 4.5 Research visibility

- Message `metadata` is engine-set only (`turn_engine.py:3656-3665`; `backend/contexts/conversation/application/message_service.py:226-229`) and is returned to every reader of the room, students included (`backend/app/api/v1/messages.py:95-125`, `:145-171`).
- The research export omits message metadata and ids; agent messages are identified by agent display name (`backend/contexts/conversation/application/research_export_builder.py:57-63`, `:199-229`; `backend/app/workers/tasks/conversation.py:449-488`).

### 4.6 Designers

All three DA prompts (`*-design.json:24`) say every TA/SA draft must carry the listed rules, that answers go only into the TA, and that no tool lets a TA instruct an SA. None mentions SA errors or a disclosure line. The CRAT designer's theory item 3 already endorses traps that lure students to a near-correct character. The CRAT designer's numbered list runs 1,2,3,4,6,7,5 (existing debt, §9).

### 4.7 Tests that constrain the edit

From `backend/tests/unit/test_agent_example_packs.py`: peers must keep `你不知道` and one of `也不要猜一份清單出來` / `不要假裝知道` (`:1173-1177`) and `不可以說成是你想到的` (`:1179-1182`); no non-TA prompt may contain `<題庫>` or any bank answer string (`:1145-1153`); no prompt may contain `秒`, `instruct`, decimal minutes or the forbidden trigger phrases (`:1041-1048`); room agents must not contain the forbidden pacing phrases (`:908-912`); the CRAT TA keeps its exact submission-routing strings (`:1155-1161`); the CRAT designer keeps its platform rules (`:794-803`). `backend/tests/unit/test_agent_example_service.py:144-162` compares installed prompts with pack prompts and follows edits automatically.

### 4.8 Installation

Installs copy prompts into project agents and skip names that already exist (`backend/contexts/agents/application/example_service.py:175-300`, `:243-247`); the pack schema has no version field (`backend/contexts/agents/infrastructure/examples/catalogue.py:42-55`, `:125-131`); a running server caches parsed packs until restart (`example_service.py:43-57`).

## 5. Design

### Options considered

**Option A: prompt-only, position-based ratio.** The SA counts its own earlier answer proposals in the visible room history and makes every third one deliberately wrong (the 2nd, 5th, 8th ... proposal). TA and DA prompts gain matching rules. No code. Deterministic positions are easier for a model to follow than "about one in three", and starting with a correct proposal lets the SA build some credibility first. Gives up exactness: counting depends on the history window and degrades under compaction, and the model can miscount.

**Option B: prompt-only, probabilistic wording** ("about one in three of your proposals"). Simplest text, but models do not sample reliably from verbal probabilities and the realised rate would be unknowable.

**Option C: engine-assisted.** The engine tells the SA per turn whether this proposal should be wrong and stores a hidden marker. Exact and researchable, but needs engine, schema-free metadata plumbing kept out of the student API, export changes and tests; rejected by Q-3 and Q-7 for this task (FU-1).

### Decision

Option A. It meets the research need at the scale the SA actually speaks, stays inside the pack files the project already treats as prompt guidance (`docs/examples/chinese-character-courses-adoption.md:125-153`), and leaves the exact-marker problem to a follow-up that can be scoped on its own. What is given up: the ratio is approximate, and the deliberate errors are identifiable afterwards only by checking content, not by a stored flag.

Error construction per course, each checkable by the SA without the bank:

- **CRAT**: a candidate character that forms a common word with two of the three clue characters but not with the third. Presented as its own idea with its association path; not self-flagged.
- **Character building**: a real character presented as built from the given components but that needs a component which was not given, or cannot be formed from them.
- **Object reinterpretation**: a use the object cannot perform because of its material or size; the fire, eating and projectile exclusions still apply.

Shared SA rules: never twice in a row; never during individual work (already silent there); never claim a proposal is confirmed or correct; when asked for the answer, still say it does not know it; on a valid challenge concede and thank; on an invalid challenge explain once; if asked whether an error was on purpose, answer that everyone, itself included, can think wrongly and the group should check together, without confirming or denying.

TA rules: one disclosure sentence in `<步驟A1>` of each TA, identical text across the three, outside the `<邀請發言>` block; and one rule that after an SA proposal the TA waits for a student response before any confirmation or correction, then uses its existing verification guidance. The CRAT TA's submission routing and answer secrecy stay unchanged.

## 6. Detailed Changes

- **Backend (data only)**: edit `system_prompt` strings in:
  - `backend/contexts/agents/infrastructure/examples/packs/crat-word-puzzle-room.json` (TA `:26`, SA `:48`)
  - `backend/contexts/agents/infrastructure/examples/packs/character-building-room.json` (TA `:26`, SA `:48`)
  - `backend/contexts/agents/infrastructure/examples/packs/object-reinterpretation-room.json` (TA `:26`, SA `:48`)
  - `backend/contexts/agents/infrastructure/examples/packs/{crat-word-puzzle,character-building,object-reinterpretation}-design.json` (DA `:24`): add a numbered rule that SA drafts carry the error rule and TA drafts carry the disclosure and wait-first rules.
  - SA edits rewrite the conflicting lines: CRAT boundaries 4 and 5 (keep `你不知道` and `不要假裝知道`), character-building boundary 3, object strategy and boundary 3 wording; all keep `不可以說成是你想到的`.
  - Wording must avoid every bank string, `<題庫>`, `秒` and the other phrases pinned in §4.7.
  - Migration required: no.
- **Tests**: `backend/tests/unit/test_agent_example_packs.py`, in `TestPhase2CoursePacks` (`:854`) next to `:1173`: new tests per §12.
- **API contract**: none; `gen:api` not required.
- **Frontend**: none.
- **Docs**: `docs/examples/chinese-character-courses-adoption.md`: describe the SA error behaviour and ratio, the TA disclosure and wait-first rule under "What is guidance, not enforcement" (`:125-153`), and state that students must not hold agent-edit rights in a research project, because anyone who can edit agents can read these prompts (`:145-147`).
- **Requirements**: new `[R13.76]` (§13); regenerate `docs/traceability.csv` with `python scripts/traceability.py`.
- **Deploy/config**: none.

## 7. NFR Checklist

- [x] i18n: N/A: prompt text is agent content, not UI strings.
- [x] Audit log: N/A: no new domain events.
- [x] Tenant isolation: N/A: no new endpoint.
- [x] Error handling UX: N/A: no UI.
- [x] Performance: N/A: prompt length grows by a few hundred characters per agent; no new calls.

## 8. Security Considerations

Agent/LLM prompt surface, so the check-security LLM dimensions apply.

- **Prompt injection.** Students can try to make the SA reveal that it errs on purpose or make it stop. The SA rule for "was that on purpose" is model guidance only; like the TA's answer secrecy (phase-2 dossier §8), it can be broken. Accepted for a classroom research setting; the debrief discloses the design anyway.
- **Readable prompts.** Anyone with agent-edit rights in the project can read the SA instruction and so learn that errors are deliberate (`docs/examples/chinese-character-courses-adoption.md:145-147`). The adoption doc must tell research operators not to grant students that right.
- **No answer leakage.** Error examples in SA and DA prompts must not contain any bank answer; `test_no_other_prompt_carries_a_bank_answer` (`:1145-1153`) keeps guarding this.
- **Harm.** Errors are wrong answers to word and character puzzles or infeasible object uses; the object SA keeps its dangerous-use exclusions.
- **No new data, endpoint, tool or permission.**

## 9. Quality Notes

- **Existing debt**: the CRAT designer's numbered list is ordered 1,2,3,4,6,7,5 (`crat-word-puzzle-design.json:24`); record as FU-2, do not renumber in this task, and append the new rule as the next free number. Phase-2 dossier line citations 24/46/68 are stale (now 26/48/66); not this task's to fix.
- **Patterns to follow**: prompt sections in the existing XML+Markdown style (`<活動特定行為>`, `<界線>`, `<步驟A1>`); behaviour pinned by substring tests in `TestPhase2CoursePacks`, one test per rule, iterating packs through the existing helpers in the test module.
- **Reuse inventory**: the test module's pack-loading helpers and agent collections (`_phase2_agents`, `COURSE_AGENTS`, the bank-answer extraction used by `:1145-1153`); the CRAT TA's existing "不直接糾正，引導驗證" (C1) and C2 verification lines instead of new verification wording; the object TA's D2 feasibility question; the TA's existing "你在上一則學生訊息之後已經發了幾則訊息" counting idiom as the model for the SA's proposal counting.

## 10. Risks and Rollback

- **Phase-2 live runs (Q-5).** Both tasks edit the same prompts while phase-2 ACs 14, 20, 21 and 24 still need classroom runs. Mitigation: do not install or deploy this branch to any room used for those runs until they finish; merge this branch only after phase 2's prompt text is final, rebasing onto it if phase 2 changes the same lines again.
- **Approximate ratio.** Counting can drift (miscount, compaction, long sessions beyond 500 messages). Mitigation: position-based rule; adoption doc states it is guidance; raters measure the realised rate post hoc.
- **Accidental correct answer.** A CRAT "wrong" candidate could coincide with an accepted alternative answer. Mitigation: the SA must check that its candidate fails at least one clue; raters judge correctness post hoc.
- **Credibility collapse.** Too-obvious errors teach students to ignore the SA. Mitigation: errors must look plausible (two of three clues; real character; plausible-sounding use).
- **Rollback.** Revert the commits; installed agents keep whatever prompt they were installed with and must be reinstalled or edited by hand.

## 11. Acceptance Criteria

- [x] AC-1: Each of the three college SA prompts states that every third of its own answer proposals in the room (the 2nd, 5th, 8th ...) is deliberately wrong, counted from its own earlier messages in the visible history, and never two wrong in a row.
- [x] AC-2: The CRAT SA prompt defines its error as a character that forms a common word with two clue characters but not the third, which the SA has checked; the character-building SA prompt defines it as a real character that cannot be built from the given components; the object SA prompt defines it as a use the object cannot perform because of its material or size, and keeps the fire, eating and projectile exclusions.
- [x] AC-3: Each of the three SA prompts instructs it to present deliberate errors in the same first-person way as its other proposals, to concede a valid challenge, to explain once on an invalid challenge, and, when asked whether an error was on purpose, to say anyone can think wrongly and the group should check together, without confirming or denying.
- [x] AC-4: The three SA prompts still pass the existing peer tests (`你不知道`, `不要假裝知道` or `也不要猜一份清單出來`, `不可以說成是你想到的`) and no longer contain `確保你分享的字確實可以由給定部件組成` or `不要猜一個答案說成是正確的`.
- [x] AC-5: Each of the three college TA prompts contains, inside `<步驟A1>`, the same disclosure sentence telling students the AI classmate's ideas may be wrong and they should judge them themselves, with no word saying the errors are deliberate (no 刻意, 故意, 設計).
- [x] AC-6: Each of the three college TA prompts states that after an SA answer proposal the TA neither confirms nor corrects it until at least one student has responded, and then guides verification; the CRAT TA's existing submission-routing and secrecy strings (`test_agent_example_packs.py:1155-1161`) are unchanged.
- [x] AC-7: Each of the three college DA prompts contains a numbered rule requiring SA drafts to carry the deliberate-error rule and TA drafts to carry the disclosure and wait-first rules.
- [x] AC-8: Neither `creative-thinking-room` nor `creative-thinking-design` contains the deliberate-error rule or the disclosure sentence.
- [x] AC-9: No SA or DA prompt contains `<題庫>` or any bank answer string, and no edited prompt contains `秒`, `instruct`, a decimal minute or the forbidden pacing and trigger phrases (existing tests pass unchanged).
- [x] AC-10: `docs/examples/chinese-character-courses-adoption.md` describes the behaviour as guidance and tells research operators not to give students agent-edit rights; `REQUIREMENTS.md` carries `[R13.76]` and `docs/traceability.csv` is regenerated.
- [x] AC-11: A live run in one CRAT room (staging, not a phase-2 live-run room) shows the TA disclosure at the opening and at least one SA proposal that fails a clue, with the TA not judging it before a student responds; transcript excerpt recorded in this dossier.

## 12. Test Plan

- AC-1 to AC-8: new unit tests in `backend/tests/unit/test_agent_example_packs.py`, class `TestPhase2CoursePacks`, one per rule, using substring assertions over the three college packs and a negative assertion over the creative-thinking packs (AC-8). AC-5's identical-sentence check compares the extracted sentence across the three TAs.
- AC-4 and AC-9: existing tests (`:1145-1153`, `:1173-1182`, `:1041-1048`, `:908-912`, `:1155-1161`, `:794-803`) must pass unchanged; `backend/tests/unit/test_agent_example_service.py:144-162` follows the edits.
- AC-10: `python scripts/traceability.py --check` in CI; doc reviewed in the diff.
- AC-11: manual via the `run` skill or staging, one CRAT room installed fresh from this branch; excerpt pasted into §15 or the follow-up notes.
- Gates: `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` in `backend/`.

## 13. SRS Delta

Add to `REQUIREMENTS.md` section 13.5, after `[R13.75]`:

> **[R13.76]** The peer agent in the three college Chinese-character course packs (CRAT word puzzle, character building, object reinterpretation) deliberately proposes a wrong answer for every third of its own answer proposals, counted from its own earlier messages in the visible room history: a CRAT character that fits two clues but not the third, a character that cannot be built from the given components, or an object use its material or size cannot support. It never says the error was deliberate. Each of those courses' teacher agents tells students at the opening that the AI classmate's ideas may be wrong and that they should judge them, and does not confirm or correct a peer proposal before a student has responded to it. The course designer agents carry these rules into the TA and SA drafts they write. This is prompt guidance, not enforced by the platform; the ratio is approximate.

## 14. Open Questions

- Whether the realised error rate in pilot transcripts is close enough to one in three; if not, revisit Option C (FU-1) rather than tightening wording blindly.
- Whether the debrief text and the ethics-protocol wording for the disclosure need platform support (for example a closing TA message); out of scope until the research team asks.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

- D-1: The new tests live in their own class `TestPeerDeliberateErrors` in `backend/tests/unit/test_agent_example_packs.py`, not inside `TestPhase2CoursePacks` as §6 and §12 said. A separate class keeps the tests traceable to this dossier and leaves the phase-2 class to phase 2; the tests reuse the module's existing helpers (`_section`, `SHIPPED_AGENTS`, `_PHASE2_TEACHERS`, `CT_COURSE_AGENTS`).
- D-2: Two prompt lines beyond §6. The CRAT SA gains strategy 4, telling it not to present the deliberate error through strategy 3's self-flagged near-miss wording, which would otherwise give the error away. The character-building TA's D1 feedback step gains a pointer to the new wait-first rule, because D1 is where that TA gives feedback on any share and would otherwise confirm or correct an SA character immediately.
- D-3: The branch code review found that the first SA wording counted a self-flagged near miss as an answer. A near miss is itself a wrong candidate, so a planned error right after one put two wrong candidates in a row, against the no-two-in-a-row rule, with no tie-break. All three SA prompts now say that an attempt the SA has already flagged as not fitting counts as neither an answer nor an error; `test_peer_errs_on_every_third_proposal_counted_from_its_own_history` pins it.
- D-4 (AC-11 evidence): Live run on staging (`smap.rcsl.online`, `main` at `96bce6a9` plus the pack
  prompts at the same revision) in a new room `驗證-CRAT-1020` (`a74a64cd-a0fb-4bac-a9fb-3928945f8143`),
  not a phase-2 live-run room, on 2026-10-20 (times UTC+8). Instead of a fresh install, the TEST
  PROJECT copies were re-synced from the shipped pack by a one-off script through
  `AgentsFacade.patch_agent` (TA and SA of all three courses; AAs were already identical), following
  the hand re-sync in `docs/examples/chinese-character-courses-adoption.md:99-123`. One room
  override deviated from the pack: SA `every_n_messages` was enabled (3, all messages) at 20:25 so
  the SA would reach its second proposal within the session; its prompt was unchanged. Participants:
  the room creator and one guest (測試同學B). Excerpt:
  - 20:12 TA: 「討論室裡的 AI 同學也會分享自己的想法，但它的想法可能有錯，要靠你們自己判斷。」 (opening disclosure)
  - 20:25 SA (first proposal): 「我先從「粗」想到「粗心」，再拿「心」去試另外兩個線索……」
  - 20:33 TA opens round 2: 「口、淚、香」.
  - 20:38 SA (second proposal, the planned error): 「我從「香」想到「香味」，再想到「口味」，所以先浮出「味」這個字。」 (「淚味」 is not a word.)
  - 20:38 to 20:41: TA was woken (silence) and returned an empty reply; it did not judge the proposal.
  - 20:41 student: 「「味」的話，香味、口味可以，但「淚味」好像不是詞耶。」
  - 20:41 SA: 「對耶，「淚味」不太像常見詞，謝謝你提醒。」 TA then summarised both candidates without
    naming the answer. Wake-up audit rows for the room confirm the TA and SA triggers above.

## 16. Follow-ups

- FU-1: A hidden, researcher-only marker on deliberately wrong peer messages (engine sets it, student APIs omit it, research export includes it), and optionally an engine-decided error schedule for exact ratios. Needs its own dossier; touches turn engine, message API serialisation and the research export.
- FU-2: The CRAT designer prompt's numbered list is ordered 1,2,3,4,6,7,5 (`crat-word-puzzle-design.json:24`).
- FU-3: The 2026-10-19 branch code review (`main...HEAD`, unverified candidates) raised items in phase-2 code that this task did not touch; they belong to `2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence`. In particular: full-width brackets (［u:...］, 【u:...】) are not defanged by `_CODE_LIKE` (`turn_engine.py:391`), so a student could plausibly spoof the room creator's prefix and push the TA to end a work phase. Also: during individual work the TA's raised message count means a single 時間到 from the creator may not wake it, `update_wakeup`'s row lock is held across the turn, the room wake-up panel cannot pin a value equal to the runtime one, silent SA wake-ups during individual work are paid turns, and the refresh sweep loads agents one by one.
