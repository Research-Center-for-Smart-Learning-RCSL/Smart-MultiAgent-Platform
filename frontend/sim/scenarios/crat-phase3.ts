import { defineScenario } from '../scenario/schema.ts'

// Staging ACs of docs/tasks/2026-10-20-crat-facilitation-phase3-late-joiners-and-activity-state:
// AC-19 (a late joiner is oriented once, a reconnect within 30 minutes is not), AC-20 (no form
// talk before the activity starts; a rejected proposal goes back to discussion; a submission
// moves to reflection) and AC-25 (a matching target is confirmed by TA, a differing one goes to
// the human teacher). It keeps crat-ac20's round 1 so the run also feeds phase 2's AC-20.
//
// Preconditions: the TEST PROJECT agents are re-synced from the pack at the deployed commit, the
// three teachers have `on_join` on, and the CRAT type carries quote_fields ["target_character"].
const ROUND_1 = { target_character: '心', word_1: '點心', word_2: '放心', word_3: '粗心' }

export default defineScenario({
  name: 'crat-phase3',
  description: 'Phase 3 staging ACs: late joiner, activity state, quoted submission targets.',
  durationSec: 34 * 60,
  room: {
    mode: 'create',
    name: 'sim-phase3-{ts}',
    access: 'memberGroups',
    guestLinks: false,
    agents: [
      { name: 'TA 字詞拼圖教師代理', role: 'normal', grantActivityTypes: ['crat-word-puzzle'] },
      { name: 'SA 字詞拼圖同儕代理', role: 'normal' },
      { name: 'AA 字詞拼圖分析代理', role: 'observer' },
    ],
  },
  // P4 is a registered account so its reconnect is the same principal; a guest's second entry
  // would be a new session. Four members make the 2/3 consent need three approvals.
  memberGroup: { name: 'Sim CRAT 第一組', members: ['P1', 'P2', 'P3', 'P4'] },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'P3', role: 'student', account: 3, displayName: '小晴' },
    { name: 'P4', role: 'student', account: 4, displayName: '小遠' },
  ],
  steps: [
    // P4 is the late joiner; everyone else connects at the start.
    { actor: 'P4', at: '7:00', do: 'connect', label: 'AC-19 late joiner connects mid-discussion' },
    { actor: 'P4', at: '9:30', do: 'leave', label: 'AC-19 late joiner drops' },
    { actor: 'P4', at: '10:15', do: 'connect', label: 'AC-19 reconnect within 30 minutes (expect no second orientation)' },
    { actor: 'P4', at: '11:30', do: 'openActivity' },

    { actor: 'F', at: 5, do: 'say', text: '同學好，今天我們來玩字詞拼圖，請助教帶大家開始。', label: 'opens the class' },
    { actor: 'P1', at: '2:00', do: 'say', text: '我準備好了！' },
    { actor: 'P2', at: '2:20', do: 'say', text: '我也準備好了，題目是什麼？' },
    { actor: 'P1', at: '5:00', do: 'say', text: '我覺得是「心」，點心、放心、粗心都可以' },
    { actor: 'P2', at: '5:05', do: 'say', text: '會不會是「火」？點火、放火' },
    { actor: 'P3', at: '6:30', do: 'say', text: '粗火好像不是一個詞耶' },
    { actor: 'P2', at: '8:00', do: 'say', text: '我再想想，火的話點火放火都對，可是粗火真的很怪' },
    { actor: 'P1', at: '9:00', do: 'say', text: '對啊，粗心是常用的詞' },
    { actor: 'P3', at: '10:45', do: 'say', text: '那我們先提交一個看看？' },

    // AC-20: a rejected proposal should send the group back to discussion.
    { actor: 'P1', at: '11:30', do: 'openActivity' },
    { actor: 'P2', at: '11:30', do: 'openActivity' },
    { actor: 'P3', at: '11:30', do: 'openActivity' },
    {
      actor: 'P3',
      at: '12:00',
      do: 'propose',
      fields: { target_character: '火', word_1: '點火', word_2: '放火', word_3: '粗火', reasoning: '先試試看火' },
      label: 'AC-20 first proposal (to be rejected)',
    },
    { actor: 'P1', at: '12:30', do: 'vote', approve: false },
    { actor: 'P2', at: '12:40', do: 'vote', approve: false },
    { actor: 'P1', at: '14:00', do: 'say', text: '火被否決了，那我們再確認一次心可不可以' },
    // AC-25 matching half: TA should confirm 心 itself.
    {
      actor: 'P3',
      at: '15:00',
      when: { type: 'proposal', status: 'rejected' },
      timeoutSec: 120,
      do: 'propose',
      fields: { ...ROUND_1, reasoning: '點心、放心、粗心都是常用的詞' },
      label: 'AC-25 matching proposal',
    },
    { actor: 'P1', at: '15:30', do: 'vote', approve: true },
    { actor: 'P4', at: '15:40', do: 'vote', approve: true },
    // Only if TA hands the match to the teacher, which would fail AC-25.
    {
      actor: 'F',
      at: '16:00',
      when: { type: 'message', from: 'agent:TA', match: '老師' },
      timeoutSec: 150,
      do: 'say',
      text: '這輪答案「心」是對的，可以算對。',
      label: 'confirms only if TA defers a matching target (AC-25 fail signal)',
    },
    { actor: 'F', at: '19:00', do: 'say', text: '進行下一輪' },

    // Round 2: AC-25 differing half.
    { actor: 'P1', at: '20:00', do: 'say', text: '這一輪好像跟液體有關？' },
    { actor: 'P2', at: '20:50', do: 'say', text: '口油？淚油？香油？香油是詞耶' },
    { actor: 'P3', at: '21:40', do: 'say', text: '口油好像怪怪的，但我們試試看油' },
    {
      actor: 'P3',
      at: '23:00',
      do: 'propose',
      fields: { target_character: '油', word_1: '口油', word_2: '淚油', word_3: '香油', reasoning: '香油是常用詞' },
      label: 'AC-25 differing proposal',
    },
    { actor: 'P1', at: '23:30', do: 'vote', approve: true },
    { actor: 'P4', at: '23:40', do: 'vote', approve: true },
    {
      actor: 'F',
      at: '24:00',
      when: { type: 'message', from: 'agent:TA', match: '老師' },
      timeoutSec: 240,
      do: 'say',
      text: '「油」這輪不算對，口油和淚油不是常用詞。',
      label: 'answers when TA defers the differing target',
    },
    { actor: 'P2', at: '29:00', do: 'say', text: '原來不是油，下一題會不會更難' },
    { actor: 'F', at: '31:00', do: 'say', text: '時間差不多了，請助教幫大家總結今天的活動。' },
    {
      actor: 'F',
      at: '33:00',
      when: { type: 'activation', state: 'active' },
      timeoutSec: 5,
      do: 'endActivity',
      label: 'ends the activity if TA has not',
    },
  ],
  notes: [
    'AC-19: one TA orientation of one or two sentences within about 2 minutes of 07:00, not naming P4 as late; none after the 10:15 reconnect.',
    'AC-20: no mention of the form before the activation starts; after the 12:40 rejection TA returns the group to discussion; after the accepted submission TA moves on (reflection or confirmation).',
    'AC-25: after the 心 submission TA confirms without the facilitator (the 16:00 step should not fire); after the 油 submission TA defers to the teacher.',
  ],
})
