import { defineScenario } from '../scenario/schema.ts'
import { individualRound, onClassClose } from '../scenario/rounds.ts'

// Phase 4 AC-11, character course: docs/tasks/2026-10-27-crat-facilitation-phase4-autonomous-teacher.
// The facilitator creates and configures the room and then never speaks or touches the panel. The
// class starts when the fourth student connects (minimum 4), each round has a 3-minute limit, and
// TA runs all six rounds of its bank and closes the class.
//
// Round 1 covers the deadline path: P4 stays connected and never submits, so only the time limit
// ends it. Rounds 2 to 6 cover the completion path: every connected student submits within about
// two minutes, before the limit.
//
// Preconditions: the phase 4 pack prompt and wake-up defaults (class_start on) are synced to the
// three agents, and phase 4 is deployed. Each round step waits for the k-th activation notice, so a
// round TA never starts shows up as timeouts rather than as out-of-step submissions.

const ROUNDS: Array<{ p1: string[]; p2: string[]; p3: string[]; p4: string[]; share1: string; share3: string }> = [
  { p1: ['杏', '呆'], p2: ['束', '困'], p3: ['困', '杏', '呆'], p4: [], share1: '我寫了「呆」，口在上面木在下面', share3: '「困」是木被口包住，算包圍' },
  { p1: ['明', '昌'], p2: ['朋', '明'], p3: ['晶', '昌'], p4: ['明', '朋'], share1: '「明」是日和月左右放', share3: '「晶」用了三個日，因為可以重複' },
  { p1: ['炎', '焱'], p2: ['炎', '焱'], p3: ['焱', '炎'], p4: ['炎', '焱'], share1: '兩個火疊起來是「炎」', share3: '三個火就是「焱」，越來越熱' },
  { p1: ['好', '如'], p2: ['好', '如'], p3: ['如', '好'], p4: ['好', '如'], share1: '女和子放一起是「好」', share3: '女加口是「如」，口放右邊' },
  { p1: ['吐', '圭'], p2: ['圭', '吐'], p3: ['哇', '圭'], p4: ['吐', '圭'], share1: '口在左邊土在右邊是「吐」', share3: '兩個土疊起來是「圭」，再加口變「哇」' },
  { p1: ['加', '另'], p2: ['加', '另'], p3: ['另', '加'], p4: ['加', '另'], share1: '力和口左右放是「加」', share3: '口在上面力在下面是「另」' },
]

const fields = (chars: string[]): Record<string, string> | null =>
  chars.length === 0 ? null : Object.fromEntries(chars.map((c, i) => [`char_0${i + 1}`, c]))

export default defineScenario({
  name: 'charbuild-autonomous',
  description: 'Phase 4 AC-11 character class run by TA alone: six rounds, a non-submitter in round 1.',
  durationSec: 60 * 60,
  room: {
    mode: 'create',
    name: 'sim-p4-char-{ts}',
    access: 'projectMembers',
    agents: [
      { name: 'TA 拆字蓋樓教師代理', role: 'normal', grantActivityTypes: ['character-building'] },
      { name: 'SA 拆字蓋樓同儕代理', role: 'normal' },
      { name: 'AA 拆字蓋樓分析代理', role: 'observer' },
    ],
    classStart: { minParticipants: 4, maxWaitMinutes: 5 },
    timeLimits: { 'character-building': 3 },
  },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'P3', role: 'student', account: 3, displayName: '小晴' },
    { name: 'P4', role: 'student', account: 4, displayName: '小遠' },
  ],
  steps: [
    // F has no steps: it connects at the start like everyone without a connect step and stays silent.
    { actor: 'P4', at: '1:00', do: 'connect', label: 'fourth student connects: the class starts' },
    // The opening: answer TA once it has spoken, then a rules question.
    { actor: 'P1', at: '1:10', when: { type: 'message', from: 'agent:TA', sinceArmed: false }, timeoutSec: 240, do: 'say', text: '好，聽起來很好玩' },
    { actor: 'P2', do: 'wait', sec: 10, at: '1:10', when: { type: 'message', from: 'agent:TA', sinceArmed: false }, timeoutSec: 240 },
    { actor: 'P2', at: 0, do: 'say', text: '我準備好了' },
    { actor: 'P3', at: '2:30', when: { type: 'message', from: 'agent:TA', count: 2, sinceArmed: false }, timeoutSec: 300, do: 'say', text: '所以只要查得到的字都算嗎？' },
    ...ROUNDS.flatMap((r, i) => {
      const round = i + 1
      return [
        ...individualRound({ actor: 'P1', round, thinkSec: 40, fields: fields(r.p1), share: { delaySec: 20, text: r.share1 } }),
        ...individualRound({ actor: 'P2', round, thinkSec: 70, fields: fields(r.p2) }),
        ...individualRound({ actor: 'P3', round, thinkSec: 90, fields: fields(r.p3), share: { delaySec: 50, text: r.share3 } }),
        ...individualRound({ actor: 'P4', round, thinkSec: 110, fields: fields(r.p4) }),
      ]
    }),
    onClassClose('P3'),
  ],
  notes: [
    'Autonomy: no message and no panel action from F after setup. Every activation is started and ended by TA through its start_activity and end_activity tools.',
    'Class start: one TA wakeup.fired row with trigger class_start at about 01:00 (the audit log also holds chatroom.class_started with reason min_participants, connected 4); TA opens with A1 without any student message before it.',
    'Deadline path: round 1 ends only after a TA wakeup.fired row with trigger activity_deadline (P4 never submits); TA announces time up once and ends the activity in the same turn.',
    'Completion path: rounds 2 to 6 each show a TA wakeup.fired row with trigger all_submitted at about 01:50 into the round, and TA ends the round before the 3-minute limit.',
    'Bank: K1 to K6 in order; after K6 TA summarises, ends any running activity and closes the class. No reference character is revealed before a student shares it.',
    'Quiet work: no TA or SA message between a round start and its end other than the time-up or all-submitted announcement.',
  ],
})
