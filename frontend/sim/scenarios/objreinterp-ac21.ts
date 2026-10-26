import { defineScenario } from '../scenario/schema.ts'

// Phase 2 AC-21, object half: docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence.
// Round 1 traverses every stage with the teacher holding the grant (start at B2, quiet ideation,
// the creator's 「發想時間到」, C2, D1 to D3 with the creator's confirmation and end_activity, E1,
// E2). Round 2 starts, and the creator ends the activity from the panel during ideation: the case
// that failed in D-18, where the teacher went on to ask for a submission to a form that was gone.
// P4 stays connected and silent as the non-speaker for AC-14's invitation evidence.
//
// Preconditions: the teacher, peer and analyst are re-synced from the pack at the deployed commit
// (the teacher carries 「下一次換別人，不論他有沒有回應、怎麼回應」), and the teacher has on_join on.
export default defineScenario({
  name: 'objreinterp-ac21',
  description: 'AC-21 object-reinterpretation class with a teacher grant and a panel end in round 2.',
  durationSec: 43 * 60,
  room: {
    mode: 'create',
    name: 'sim-ac21-obj-{ts}',
    access: 'projectMembers',
    agents: [
      { name: 'TA 萬物新解教師代理', role: 'normal', grantActivityTypes: ['object-reinterpretation'] },
      { name: 'SA 萬物新解同儕代理', role: 'normal' },
      { name: 'AA 萬物新解分析代理', role: 'observer' },
    ],
  },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'P3', role: 'student', account: 3, displayName: '小晴' },
    { name: 'P4', role: 'student', account: 4, displayName: '小遠' },
  ],
  steps: [
    { actor: 'F', at: 5, do: 'say', text: '同學好，今天我們來玩萬物新解，請助教帶大家開始。', label: 'opens the class' },
    // A1 and A2.
    { actor: 'P1', at: '1:30', do: 'say', text: '好啊' },
    { actor: 'P2', at: '1:50', do: 'say', text: '功能固著是什麼意思？' },
    { actor: 'P3', at: '4:00', do: 'say', text: '所以材料轉換就是把它拆開來用？' },
    { actor: 'P1', at: '4:20', do: 'say', text: '懂了' },
    // B1 reveals O1 with a demo; B2 starts the activity with the grant.
    { actor: 'P2', at: '6:40', do: 'say', text: '可以開始了' },
    { actor: 'P3', at: '7:00', do: 'say', text: '好' },
    // C1: quiet ideation. P3 has not submitted when ideation ends, so C2's reminder applies to it.
    { actor: 'P1', at: '8:30', when: { type: 'activation', state: 'active' }, timeoutSec: 240, do: 'openActivity' },
    { actor: 'P2', at: '8:30', when: { type: 'activation', state: 'active' }, timeoutSec: 240, do: 'openActivity' },
    { actor: 'P3', at: '8:30', when: { type: 'activation', state: 'active' }, timeoutSec: 240, do: 'openActivity' },
    { actor: 'P4', at: '8:30', when: { type: 'activation', state: 'active' }, timeoutSec: 240, do: 'openActivity' },
    {
      actor: 'P1',
      at: '10:30',
      when: { type: 'activation', state: 'active' },
      timeoutSec: 180,
      do: 'submit',
      fields: {
        use_1: '拉直當手機取卡針',
        use_2: '彎成小掛勾掛鑰匙',
        use_3: '當書籤夾在頁面上',
        best_pick: '拉直當手機取卡針',
        best_pick_reasoning: '它夠細又夠硬，大家只會拿它夾紙',
      },
    },
    {
      actor: 'P2',
      at: '11:20',
      do: 'submit',
      fields: { use_1: '當書籤', use_2: '串起來當項鍊', best_pick: '當書籤', best_pick_reasoning: '很方便' },
    },
    {
      actor: 'P4',
      at: '12:00',
      do: 'submit',
      fields: { use_1: '拿來清鍵盤縫', best_pick: '拿來清鍵盤縫', best_pick_reasoning: '鍵盤縫很窄，一般工具進不去' },
    },
    // The creator ends ideation; the third human message wakes the teacher (n 3).
    { actor: 'F', at: '13:00', do: 'say', text: '發想時間到。', label: 'creator ends ideation' },
    { actor: 'P1', at: '13:20', do: 'say', text: '好' },
    { actor: 'P2', at: '13:40', do: 'say', text: '我交了' },
    {
      actor: 'P3',
      at: '14:40',
      do: 'submit',
      fields: { use_1: '彎成小掛勾', use_2: '當拉鍊拉環', best_pick: '彎成小掛勾', best_pick_reasoning: '把它的鐵絲材質拿來塑形' },
      label: 'P3 submits after the C2 reminder',
    },
    // D1 and D2: sharing a choice, one strong and one weak reason.
    { actor: 'P1', at: '15:30', do: 'say', text: '我選的是把迴紋針拉直當手機取卡針，因為它夠細又夠硬' },
    { actor: 'P1', at: '16:40', do: 'say', text: '一般人只拿它夾紙，我是把它的材質拿來用' },
    { actor: 'P2', at: '17:40', do: 'say', text: '我選當書籤' },
    { actor: 'P2', at: '18:50', do: 'say', text: '嗯…其實它還是在夾東西，好像沒打破什麼' },
    // D3: the creator confirms the end only after the teacher asks. Armed when P2's last share
    // is sent, since the ask can follow it within seconds and a condition ignores earlier messages.
    {
      actor: 'F',
      at: '18:50',
      when: { type: 'message', from: 'agent:TA', match: '結束' },
      timeoutSec: 240,
      do: 'say',
      text: '可以結束了。',
      label: 'creator confirms the end when TA asks (D3)',
    },
    // E1 and E2.
    { actor: 'P3', at: '23:00', do: 'say', text: '我選的是彎成小掛勾，是用它的鐵絲材質' },
    { actor: 'P1', at: '24:00', do: 'say', text: '跟我一樣是材料轉換耶' },
    { actor: 'F', at: '26:00', do: 'say', text: '進行下一輪', label: 'creator confirms round 2' },
    // Round 2 B1 and B2; then the creator ends the activity from the panel during ideation.
    { actor: 'P1', at: '27:30', do: 'say', text: '好' },
    { actor: 'P2', at: '27:45', do: 'say', text: '開始吧' },
    // The round-2 start drifts (31:09 in the 2026-10-26 run), so the panel end waits for TA's work
    // announcement and the reactions follow the platform's end notice, not the clock.
    {
      actor: 'F',
      at: '28:00',
      when: { type: 'message', from: 'agent:TA', match: '開始.*作答' },
      timeoutSec: 600,
      do: 'endActivity',
      label: 'ends round 2 from the panel during ideation (teacher-ended case)',
    },
    { actor: 'P3', at: '29:00', when: { type: 'message', from: 'system', match: 'ended' }, timeoutSec: 600, do: 'say', text: '咦，表單不見了？' },
    { actor: 'P1', at: '29:00', when: { type: 'message', from: 'P3', match: '表單不見' }, timeoutSec: 660, do: 'say', text: '我還沒寫完' },
    { actor: 'P2', at: '29:00', when: { type: 'message', from: 'P1', match: '還沒寫完' }, timeoutSec: 720, do: 'say', text: '老師是結束了嗎？' },
    { actor: 'F', at: '42:00', do: 'say', text: '今天先到這裡，謝謝大家。' },
  ],
  notes: [
    'Grant: TA calls start_activity at B2 and announces the form only after the activation; it calls end_activity in the same turn as the creator\'s 19:30 confirmation.',
    'Quiet work: no TA or SA message between the work announcement and 13:00.',
    'C2: the reminder to pick a best answer comes after 13:00 while the activity is still running.',
    'Teacher-ended: after the 29:30 panel end TA says the round was ended, does not ask anyone to submit or mention the form, and goes to E2 (asks the creator about the next round).',
    'Stages: A1 (with the AI-peer disclosure), A2, B1 with O1 and a demo outside the bank, B2, C1, C2, D1, D2 (follow-up on reasons), D3, E1, E2, then B1 with O2 after 26:00. No reference use is revealed during ideation.',
    'AC-14: invitations only outside C1, one connected participant at a time, never the creator, not the person last invited.',
  ],
})
