import { defineScenario } from '../scenario/schema.ts'

// Phase 2 AC-21, character half: docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence.
// The teacher holds no activity grant here, so the run covers the missing-grant case that failed in
// D-18 (the teacher must ask the room creator to start, and must not announce the form before
// [Activity state] shows it running), quiet individual work, the creator-confirmed end of work and
// sharing. P4 stays connected and silent as the non-speaker for AC-14's invitation evidence.
//
// Preconditions: the teacher, peer and analyst are re-synced from the pack at the deployed commit
// (the teacher carries 「下一次換別人，不論他有沒有回應、怎麼回應」), and the teacher has on_join on.
export default defineScenario({
  name: 'charbuild-ac21',
  description: 'AC-21 character-building class without a teacher grant: four students, a facilitator.',
  durationSec: 32 * 60,
  room: {
    mode: 'create',
    name: 'sim-ac21-char-{ts}',
    access: 'projectMembers',
    agents: [
      { name: 'TA 拆字蓋樓教師代理', role: 'normal' },
      { name: 'SA 拆字蓋樓同儕代理', role: 'normal' },
      { name: 'AA 拆字蓋樓分析代理', role: 'observer' },
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
    { actor: 'F', at: 5, do: 'say', text: '同學好，今天我們來玩拆字蓋樓，請助教帶大家開始。', label: 'opens the class' },
    // A1 and A2: answer the opening, then a rules question.
    { actor: 'P1', at: '1:30', do: 'say', text: '好，聽起來很好玩' },
    { actor: 'P2', at: '1:50', do: 'say', text: '我準備好了' },
    { actor: 'P3', at: '4:00', do: 'say', text: '所以只要查得到的字都算嗎？' },
    { actor: 'P1', at: '4:20', do: 'say', text: '了解' },
    // B1 reveals K1 with a demo; then the students are ready.
    { actor: 'P2', at: '6:40', do: 'say', text: '懂了，可以開始了' },
    { actor: 'P3', at: '7:00', do: 'say', text: '好' },
    // B2 without a grant: the creator starts the activity only after TA asks for it. The ask can
    // come right after B1 (04:26 in the 2026-10-26 run), so any earlier TA message counts.
    {
      actor: 'F',
      at: '6:00',
      when: { type: 'message', from: 'agent:TA', match: '(老師|建立者).*開始|開始.*活動', sinceArmed: false },
      timeoutSec: 240,
      do: 'startActivity',
      type: 'character-building',
      label: 'starts the activity when TA asks for it (missing grant)',
    },
    // Two students react to the form so TA wakes (every_n 2) and sees the activity running.
    { actor: 'P1', at: '7:30', when: { type: 'activation', state: 'active' }, timeoutSec: 300, do: 'say', text: '表單出來了' },
    { actor: 'P2', at: '7:50', when: { type: 'activation', state: 'active' }, timeoutSec: 300, do: 'say', text: '我也看到了' },
    // C1: quiet individual work, nobody talks.
    { actor: 'P1', at: '10:00', do: 'openActivity' },
    { actor: 'P2', at: '10:00', do: 'openActivity' },
    { actor: 'P3', at: '10:00', do: 'openActivity' },
    { actor: 'P4', at: '10:00', do: 'openActivity' },
    { actor: 'P1', at: '11:30', do: 'submit', fields: { char_01: '杏', char_02: '呆', char_03: '困' }, label: 'P1 submits three characters' },
    { actor: 'P3', at: '12:10', do: 'submit', fields: { char_01: '困', char_02: '杏', char_03: '呆', char_04: '束' } },
    { actor: 'P2', at: '12:50', do: 'submit', fields: { char_01: '束', char_02: '杏' } },
    { actor: 'P4', at: '13:20', do: 'submit', fields: { char_01: '呆', char_02: '困' } },
    // The creator ends the work. The teacher raised n to 3, so the third human message wakes it.
    { actor: 'F', at: '15:00', do: 'say', text: '時間到，大家停筆。', label: 'creator says time is up' },
    { actor: 'P1', at: '15:20', do: 'say', text: '我寫完了' },
    { actor: 'P2', at: '15:40', do: 'say', text: '我也交了' },
    // Without end_activity the teacher only announces; the creator then closes the form.
    {
      actor: 'F',
      at: '15:05',
      when: { type: 'message', from: 'agent:TA', match: '時間到' },
      timeoutSec: 240,
      do: 'endActivity',
      label: 'ends the activity from the panel after TA announces time up',
    },
    // D1 sharing stays with P1 and P3 at first, so the invitation condition (sharing concentrated
    // in a few people while connected students say nothing) holds; P2 shares later, P4 never.
    { actor: 'P1', at: '18:00', do: 'say', text: '我想到「困」，木被口包起來，算包圍結構' },
    { actor: 'P3', at: '19:00', do: 'say', text: '我寫了「呆」和「杏」，同樣兩個部件上下換一下就不一樣' },
    { actor: 'P1', at: '20:00', do: 'say', text: '我也有寫「杏」' },
    { actor: 'P2', at: '21:30', do: 'say', text: '我只想到兩個，「束」是木中間加一個口' },
    { actor: 'P3', at: '22:30', do: 'say', text: '原來還可以穿過去，我沒想到' },
    // D2 and D3: the creator decides the next round, and TA reveals K2.
    { actor: 'P1', at: '23:30', do: 'say', text: '所以擺法越多越好' },
    { actor: 'F', at: '25:00', do: 'say', text: '進行下一輪', label: 'creator confirms the next round' },
    { actor: 'P2', at: '27:00', do: 'say', text: '這次的部件可以重複用嗎？' },
    { actor: 'P1', at: '27:20', do: 'say', text: '好' },
    { actor: 'F', at: '31:00', do: 'say', text: '今天先到這裡，謝謝大家。' },
  ],
  notes: [
    'Missing grant: before the 07:00 start step fires, TA asks the room creator to start and does not say 「開始個人作答」 or mention the form; after the activation it announces the work once.',
    'Quiet work: no TA or SA message between the work announcement and 15:00.',
    'Creator-confirmed end: TA announces time up only after 15:00, does not claim to have closed the form, and moves to sharing (D1) after the panel end.',
    'Stages: A1 (with the AI-peer disclosure), A2, B1 with K1 and a demo on other components, B2, C1, D1 opened right after the time-up (not left to SA), D2, D3, then B1 with K2 after 25:00. No reference character is revealed before a student shares it.',
    'AC-14: invitations only in D1 to D3, one connected participant at a time, never the creator, not the person last invited.',
  ],
})
