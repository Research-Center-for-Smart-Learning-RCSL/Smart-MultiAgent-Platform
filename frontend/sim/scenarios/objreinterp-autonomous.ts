import { defineScenario } from '../scenario/schema.ts'
import { individualRound, onRoundEnd, onRoundStart } from '../scenario/rounds.ts'

// Phase 4 AC-11, object course: docs/tasks/2026-10-27-crat-facilitation-phase4-autonomous-teacher.
// The facilitator configures the room and speaks exactly once, to override round 3. The class
// start uses the waiting path: the minimum is 5 but only four students come, so the class starts
// when 2 minutes have passed since the first arrival (the worker's per-minute sweep fires it).
//
// P4 is connected and never submits, so rounds 1, 2, 5 and 6 end on the 3-minute limit. Round 3 is
// the creator override: F says time is up after one minute and TA must end the round then. In
// round 4 P4 disconnects, so the remaining three submitting ends the round on the completion path:
// a student who left is not waited for. P4 reconnects for round 5.
//
// Preconditions as in charbuild-autonomous.ts.

const ROUNDS: Array<{ p1: string[]; p2: string[]; p3: string[]; share1: string; share3: string }> = [
  { p1: ['書籤', '臨時掛勾'], p2: ['手機取卡針'], p3: ['拉鍊拉環', '小雕塑'], share1: '迴紋針可以夾在書裡當書籤', share3: '拉鍊拉環壞掉的時候可以用迴紋針代替' },
  { p1: ['包東西的填充物', '擦玻璃'], p2: ['摺紙帽'], p3: ['塞進濕鞋子吸水'], share1: '報紙揉一揉可以塞在箱子裡防撞', share3: '下雨天把報紙塞進鞋子會比較快乾' },
  { p1: ['小花盆'], p2: [], p3: [], share1: '寶特瓶剪一半可以種小植物', share3: '我還沒寫完，本來想說可以做撲滿' },
  { p1: ['勾掉到沙發後面的東西', '吊盆栽'], p2: ['撐開垃圾袋'], p3: ['平板支架'], share1: '衣架拉直可以勾沙發後面的東西', share3: '衣架彎一彎可以變成平板支架' },
  { p1: ['門擋', '書擋'], p2: ['壓住防水布'], p3: ['墊高東西'], share1: '磚塊放在門邊可以擋門', share3: '書桌太矮可以用磚塊墊高' },
  { p1: ['開罐頭防滑', '捆線材'], p2: ['標記杯子'], p3: ['筆的防滑握把'], share1: '橡皮筋套在瓶蓋上比較好開', share3: '纏在筆上寫字比較不會滑' },
]

const fields = (uses: string[]): Record<string, string> | null =>
  uses.length === 0 ? null : Object.fromEntries(uses.map((u, i) => [`use_${i + 1}`, u]))

export default defineScenario({
  name: 'objreinterp-autonomous',
  description: 'Phase 4 AC-11 object class run by TA: waiting-path start, a creator override, a leaver.',
  durationSec: 60 * 60,
  room: {
    mode: 'create',
    name: 'sim-p4-obj-{ts}',
    access: 'projectMembers',
    agents: [
      { name: 'TA 萬物新解教師代理', role: 'normal', grantActivityTypes: ['object-reinterpretation'] },
      { name: 'SA 萬物新解同儕代理', role: 'normal' },
      { name: 'AA 萬物新解分析代理', role: 'observer' },
    ],
    classStart: { minParticipants: 5, maxWaitMinutes: 2 },
    timeLimits: { 'object-reinterpretation': 3 },
  },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'P3', role: 'student', account: 3, displayName: '小晴' },
    { name: 'P4', role: 'student', account: 4, displayName: '小遠' },
  ],
  steps: [
    // Round 3 override, F's only message: one minute into the round, before anyone but P1 submits.
    // F follows the rounds' ends first: a round wait times out 20 minutes after it is armed, and
    // armed at the start it expired before round 3 once the class ran at its own pace.
    onRoundEnd('F', 1),
    onRoundEnd('F', 2),
    onRoundStart('F', 3),
    { actor: 'F', at: 0, do: 'wait', sec: 60 },
    { actor: 'F', at: 0, do: 'say', text: '時間到，大家停筆。', label: 'creator override: time is up in round 3' },
    { actor: 'P1', at: '2:00', when: { type: 'message', from: 'agent:TA', sinceArmed: false }, timeoutSec: 300, do: 'say', text: '好，我準備好了' },
    { actor: 'P2', at: '2:15', when: { type: 'message', from: 'agent:TA', sinceArmed: false }, timeoutSec: 300, do: 'say', text: '物品是什麼？' },
    ...ROUNDS.flatMap((r, i) => {
      const round = i + 1
      return [
        ...individualRound({ actor: 'P1', round, thinkSec: 40, fields: fields(r.p1), share: { delaySec: 20, text: r.share1 } }),
        ...individualRound({ actor: 'P2', round, thinkSec: 75, fields: fields(r.p2) }),
        ...individualRound({ actor: 'P3', round, thinkSec: 100, fields: fields(r.p3), share: { delaySec: 50, text: r.share3 } }),
      ]
    }),
    // P4 opens the form each round and never submits; it leaves during round 4 and is back for round 5.
    // A connect step anywhere makes an actor a late joiner, so P4's first connect is explicit.
    { actor: 'P4', at: 0, do: 'connect', label: 'P4 connects at the start' },
    ...[1, 2, 3].map((round) => onRoundStart('P4', round)),
    onRoundStart('P4', 4),
    { actor: 'P4', at: 0, do: 'leave', label: 'P4 disconnects in round 4' },
    { actor: 'P4', do: 'connect', when: { type: 'message', from: 'system', match: 'ended\\.', sinceArmed: false, count: 4 }, timeoutSec: 20 * 60, label: 'P4 reconnects after round 4' },
    onRoundStart('P4', 5),
    onRoundStart('P4', 6),
  ],
  notes: [
    'Autonomy: F sends exactly one message (round 3) and takes no panel action. Every activation is started and ended by TA through its tools.',
    'Class start: one TA wakeup.fired row with trigger class_start about 2 to 3 minutes after the first student arrived (chatroom.class_started: reason max_wait, connected 4); TA opens with A1.',
    'Deadline path: rounds 1, 2, 5 and 6 each show a TA wakeup.fired row with trigger activity_deadline; TA announces time up once and ends the activity in the same turn.',
    'Override: in round 3 F saying time is up shows a TA wakeup.fired row with trigger creator_message within seconds, and TA ends the activity in that turn, well before the limit; it treats no student message as the teacher.',
    'Leaver: round 4 shows a TA wakeup.fired row with trigger all_submitted after P4 left (activity.all_submitted: connected 3), and TA ends the round before the limit.',
    'Bank: O1 to O6 in order, then a summary and the close. Reference uses are cited only after a student shares one.',
  ],
})
