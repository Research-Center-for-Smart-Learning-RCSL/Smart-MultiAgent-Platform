import { defineScenario } from '../scenario/schema.ts'

// docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence/ac20-live-run-protocol.md,
// one step group per row of its §2 timeline. Run with `--facilitator human` to play the
// teacher yourself; the facilitator's steps then print as timed cues.
//
// Preconditions the tool cannot check: the TA prompt contains 「但它的想法可能有錯」 (the
// re-synced revision), and the deployed commit is written down. Record both before running.
// Since phase 3 (phase 2 D-19) TA confirms a target matching its bank itself, so the facilitator
// answers only when TA defers to the teacher.
const ROUND_1 = { target_character: '心', word_1: '點心', word_2: '放心', word_3: '粗心' }

export default defineScenario({
  name: 'crat-ac20',
  description: 'AC-20 four-person CRAT class: three group members, a same-name guest, a facilitator.',
  durationSec: 32 * 60,
  room: {
    mode: 'create',
    name: 'sim-ac20-{ts}',
    access: 'memberGroups',
    guestLinks: true,
    agents: [
      { name: 'TA 字詞拼圖教師代理', role: 'normal', grantActivityTypes: ['crat-word-puzzle'] },
      { name: 'SA 字詞拼圖同儕代理', role: 'normal' },
      { name: 'AA 字詞拼圖分析代理', role: 'observer' },
    ],
  },
  // Its own group: provisioning only adds members, and crat-phase3 put a fourth account into
  // 「Sim CRAT 第一組」, which would raise the 2/3 consent to three approvals.
  memberGroup: { name: 'Sim CRAT AC-20 組', members: ['P1', 'P2', 'P3'] },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'P3', role: 'student', account: 3, displayName: '小晴' },
    { name: 'P4', role: 'guest', displayName: '小安' },
  ],
  steps: [
    // Minute 0: everyone connects at the start (no connect steps); P4 stays silent all of round 1.
    { actor: 'F', at: 5, do: 'say', text: '同學好，今天我們來玩字詞拼圖，請助教帶大家開始。', label: 'opens the class' },
    // Minute 2: answer TA's opening questions.
    { actor: 'P1', at: '2:00', do: 'say', text: '我準備好了！' },
    { actor: 'P2', at: '2:20', do: 'say', text: '我也準備好了，題目是什麼？' },
    // Minute 5: simultaneous candidates, about five seconds apart.
    { actor: 'P1', at: '5:00', do: 'say', text: '我覺得是「心」，點心、放心、粗心都可以' },
    { actor: 'P2', at: '5:05', do: 'say', text: '會不會是「火」？點火、放火' },
    // Minutes 6 to 12: discussion, a long draft paused before sending, P4 silent.
    { actor: 'P3', at: '6:30', do: 'say', text: '粗火好像不是一個詞耶' },
    {
      actor: 'P2',
      at: '7:30',
      do: 'typePause',
      text: '我再想想，火的話點火放火都對，可是粗火真的很怪，所以我覺得心比較合理一點',
      typeChars: 14,
      pauseSec: 25,
      then: 'send',
      label: 'long draft with a pause',
    },
    { actor: 'P1', at: '9:00', do: 'say', text: '對啊，粗心是常用的詞' },
    { actor: 'P3', at: '10:30', do: 'say', text: '那我們先提交一個看看？' },
    // Minute 12: proposal, rejection, new proposal, consent. The proposer's own approval
    // is implicit (group_proposal_service.py:175-179), so with 2 of 3 required the first
    // proposal is rejected by P1 and P2 both, and P1 alone carries the second.
    { actor: 'P1', at: '11:45', do: 'openActivity' },
    { actor: 'P2', at: '11:45', do: 'openActivity' },
    { actor: 'P3', at: '11:45', do: 'openActivity' },
    {
      actor: 'P3',
      at: '12:00',
      do: 'propose',
      fields: { target_character: '火', word_1: '點火', word_2: '放火', word_3: '粗火', reasoning: '先試試看火' },
      label: 'first proposal (to be rejected)',
    },
    { actor: 'P1', at: '12:30', do: 'vote', approve: false },
    { actor: 'P2', at: '12:40', do: 'vote', approve: false },
    {
      actor: 'P3',
      at: '13:00',
      when: { type: 'proposal', status: 'rejected' },
      timeoutSec: 120,
      do: 'propose',
      fields: { ...ROUND_1, reasoning: '點心、放心、粗心都是常用的詞' },
      label: 'second proposal',
    },
    { actor: 'P1', at: '13:30', do: 'vote', approve: true },
    // Minute 15: the facilitator confirms only if TA hands the result over.
    {
      actor: 'F',
      at: '14:00',
      when: { type: 'message', from: 'agent:TA', match: '老師' },
      timeoutSec: 150,
      do: 'say',
      text: '這輪答案「心」是對的，可以算對。',
      label: 'confirms the result if TA defers it',
    },
    // Minute 16: teacher-confirmed transition.
    { actor: 'F', at: '16:00', do: 'say', text: '進行下一輪' },
    // Minutes 16 to 30: steady discussion, nobody quiet for more than a minute.
    { actor: 'P1', at: '17:00', do: 'say', text: '這一輪好像跟液體有關？' },
    { actor: 'P2', at: '17:50', do: 'say', text: '口水、淚水、香水，會是「水」嗎' },
    { actor: 'P3', at: '18:40', do: 'say', text: '我覺得是水，三個都很常用' },
    { actor: 'P4', at: '19:30', do: 'say', text: '我也覺得是水', label: 'guest speaks for the first time' },
    { actor: 'P1', at: '20:20', do: 'say', text: '還有沒有別的可能？口味、香味？' },
    { actor: 'P2', at: '21:10', do: 'say', text: '淚味不是詞，所以應該還是水' },
    { actor: 'P3', at: '22:00', do: 'say', text: '同意，水比較好' },
    { actor: 'P1', at: '22:50', do: 'say', text: '那我們等一下提交水' },
    { actor: 'P4', at: '23:40', do: 'say', text: '我可以一起提交嗎？' },
    { actor: 'P2', at: '24:30', do: 'say', text: '訪客好像不能投票，我們三個來就好' },
    { actor: 'P3', at: '25:20', do: 'say', text: '下一題會不會更難' },
    { actor: 'P1', at: '26:10', do: 'say', text: '我覺得這種題目很好玩' },
    { actor: 'P2', at: '27:00', do: 'say', text: '可以再給一點提示嗎' },
    // AA wakes every 20 human messages, first at about minute 25 in this script (phase 2
    // D-22), so the release waits until an observation exists.
    { actor: 'F', at: '27:30', do: 'releaseObservation', label: 'releases one AA observation' },
    { actor: 'P3', at: '27:50', do: 'say', text: '我想到另一個字，但不太確定' },
    { actor: 'P1', at: '28:40', do: 'say', text: '說說看嘛' },
    { actor: 'P3', at: '29:30', do: 'say', text: '算了，我覺得還是水' },
    // Minute 30: wrap-up; end from the panel only if TA has not.
    { actor: 'F', at: '30:00', do: 'say', text: '時間差不多了，請助教幫大家總結今天的活動。' },
    {
      actor: 'F',
      at: '31:00',
      when: { type: 'activation', state: 'active' },
      timeoutSec: 5,
      do: 'endActivity',
      label: 'ends the activity if TA has not',
    },
  ],
  notes: [
    'Pass criteria: ac20-live-run-protocol.md §4. Judge them from transcript.json and record a D-n in the phase 2 dossier.',
    'Protocol deviation: the first proposal is rejected by P1 and P2 together, because the proposer approves implicitly and P1 approving would already reach 2 of 3.',
    'Minute 2 answers are fixed texts, not responses to what TA actually asked; read TA turns around 02:00 with that in mind.',
    'AC-14: every TA invitation is optional, names one connected participant, is not repeated to the person last invited, never targets the facilitator, and never uses the shared name 小安 to call on P1 or P4.',
  ],
})
