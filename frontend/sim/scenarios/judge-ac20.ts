import { defineScenario } from '../scenario/schema.ts'

// docs/tasks/2026-11-08-research-data-integrity AC-20: a short object-reinterpretation round in
// which two signed-in students and a guest each submit, one student twice, so the export has a
// guest row, a resubmission and real judge scores to check.
//
// Precondition the tool cannot set: a project type `object-reinterpretation-scored` with the
// course type's schema and validator and creativity scoring on, judged by 「AA 萬物新解分析代理」.
// The course type itself is platform-scoped, and a platform type cannot carry scoring (D-23).
// The judge is bound to the room below as an observer, so it is in the room (the job skips a
// judge that is not) but never wakes on this few messages.
export default defineScenario({
  name: 'judge-ac20',
  description: 'Research data integrity AC-20: scored submissions by two students and a guest.',
  durationSec: 7 * 60,
  room: {
    mode: 'create',
    name: 'sim-judge-{ts}',
    access: 'projectMembers',
    guestLinks: true,
    agents: [{ name: 'AA 萬物新解分析代理', role: 'observer' }],
  },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'G', role: 'guest', displayName: '訪客小林' },
  ],
  steps: [
    { actor: 'F', at: 5, do: 'say', text: '同學好，今天來想想迴紋針還能怎麼用。', label: 'opens the class' },
    { actor: 'F', at: 15, do: 'startActivity', type: 'object-reinterpretation-scored', label: 'starts the scored type' },
    { actor: 'P1', at: 20, when: { type: 'activation', state: 'active' }, timeoutSec: 60, do: 'openActivity' },
    { actor: 'P2', at: 20, when: { type: 'activation', state: 'active' }, timeoutSec: 60, do: 'openActivity' },
    { actor: 'G', at: 20, when: { type: 'activation', state: 'active' }, timeoutSec: 60, do: 'openActivity' },
    {
      actor: 'P1',
      at: '1:00',
      do: 'submit',
      fields: { use_1: '當書籤', best_pick: '當書籤', best_pick_reasoning: '很方便' },
      label: 'first attempt',
    },
    {
      actor: 'P2',
      at: '1:20',
      do: 'submit',
      fields: {
        use_1: '拉直當手機取卡針',
        use_2: '彎成小掛勾掛鑰匙',
        use_3: '串起來當項鍊',
        best_pick: '拉直當手機取卡針',
        best_pick_reasoning: '它夠細又夠硬，一般人只拿它夾紙',
      },
    },
    {
      actor: 'G',
      at: '1:40',
      do: 'submit',
      fields: { use_1: '拿來清鍵盤縫', use_2: '當拉鍊拉環', best_pick: '拿來清鍵盤縫', best_pick_reasoning: '鍵盤縫很窄，一般工具進不去' },
      label: 'guest submits',
    },
    {
      actor: 'P1',
      at: '2:30',
      do: 'submit',
      fields: {
        use_1: '當書籤',
        use_2: '彎成手機支架',
        use_3: '當 SIM 卡退卡針',
        use_4: '做成小雕塑',
        best_pick: '彎成手機支架',
        best_pick_reasoning: '把鐵絲的形狀和硬度拿來支撐東西',
      },
      label: 'resubmission (the latest counts on the dashboard)',
    },
    { actor: 'G', at: '3:00', do: 'say', text: '我交好了', label: 'guest speaks once' },
    // Leaves time for the judge jobs before the round ends.
    { actor: 'F', at: '6:00', do: 'endActivity', label: 'ends the round' },
  ],
  notes: [
    'Operator, while the run is going: open the dashboard watchlist as the owner and check that creativity means appear for 小安, 小博 and the guest (a pending or reason text first is fine).',
    'Operator, after the run: export research data for the workspace and keep the ZIP.',
    'Pass (AC-20): submissions.csv opens in Excel with readable Chinese; the guest row joins its transcript by subject code; scored rows carry judge_status scored, four integer scores and judge_model.',
  ],
})
