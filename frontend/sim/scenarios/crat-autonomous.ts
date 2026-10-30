import { defineScenario } from '../scenario/schema.ts'
import type { ScenarioInput } from '../scenario/schema.ts'
import { onRoundStart } from '../scenario/rounds.ts'

// Phase 4 AC-11, CRAT course: docs/tasks/2026-10-27-crat-facilitation-phase4-autonomous-teacher.
// The facilitator configures the room and never speaks or touches the panel. Three group members
// play all eight rounds of TA's bank; the class starts when the third connects (minimum 3).
//
// One CRAT activation spans the whole class (the TA prompt never ends it on a submission), so the
// rounds are marked by TA revealing each round's clue characters, not by activation notices. The
// type ships with no time limit and this room keeps it that way: an end on the limit would end
// every remaining round at once. The deadline path is covered by the other two courses.
//
// In round 4 the group first submits a wrong target, which TA must judge and explain itself without
// asking the creator; the group then submits the right one.
//
// Preconditions as in charbuild-autonomous.ts. The group is its own, so the 2/3 consent needs the
// proposer plus one approval.

type StepInput = ScenarioInput['steps'][number]
type Answer = { target_character: string; word_1: string; word_2: string; word_3: string; reasoning: string }

const ROUNDS: Array<{ clues: [string, string, string]; guess: string; agree: string; answer: Answer; wrong?: Answer }> = [
  { clues: ['點', '放', '粗'], guess: '我覺得是「心」，點心、放心、粗心', agree: '三個都很常用，應該是心', answer: { target_character: '心', word_1: '點心', word_2: '放心', word_3: '粗心', reasoning: '三個都是常用詞' } },
  { clues: ['口', '淚', '香'], guess: '口水、淚水、香水，是「水」吧', agree: '一開始想到口味，可是淚味不是詞，所以是水', answer: { target_character: '水', word_1: '口水', word_2: '淚水', word_3: '香水', reasoning: '口味可以但淚味不行，水三個都通' } },
  { clues: ['主', '客', '詩'], guess: '主人、客人、詩人，是「人」', agree: '對，三個都通', answer: { target_character: '人', word_1: '主人', word_2: '客人', word_3: '詩人', reasoning: '都是指某一種人' } },
  {
    clues: ['颱', '威', '兜'],
    guess: '威力很常見，會不會是「力」？',
    agree: '颱力好像怪怪的，不過先交交看',
    wrong: { target_character: '力', word_1: '颱力', word_2: '威力', word_3: '兜力', reasoning: '威力是常用詞' },
    answer: { target_character: '風', word_1: '颱風', word_2: '威風', word_3: '兜風', reasoning: '三個都是常用詞' },
  },
  { clues: ['眼', '時', '觀'], guess: '眼光、時光、觀光，是「光」', agree: '我也想到光', answer: { target_character: '光', word_1: '眼光', word_2: '時光', word_3: '觀光', reasoning: '三個都是常用詞' } },
  { clues: ['熱', '專', '竅'], guess: '熱門、專門，竅門也可以，是「門」', agree: '竅門我有聽過', answer: { target_character: '門', word_1: '熱門', word_2: '專門', word_3: '竅門', reasoning: '三個都是常用詞' } },
  { clues: ['秘', '證', '圖'], guess: '秘書、證書、圖書，是「書」', agree: '對，都是書', answer: { target_character: '書', word_1: '秘書', word_2: '證書', word_3: '圖書', reasoning: '三個都是常用詞' } },
  { clues: ['饅', '碼', '枕'], guess: '饅頭、碼頭、枕頭，是「頭」', agree: '這題比較簡單', answer: { target_character: '頭', word_1: '饅頭', word_2: '碼頭', word_3: '枕頭', reasoning: '三個都是常用詞' } },
]

/** TA's reveal of a round: its three clue characters close together in one message. */
function onClues(actor: string, round: number, clues: [string, string, string]): StepInput {
  const [a, b, c] = clues
  return {
    actor,
    do: 'wait',
    sec: 1,
    when: { type: 'message', from: 'agent:TA', match: `${a}[^\\n]{0,12}${b}[^\\n]{0,12}${c}`, sinceArmed: false },
    timeoutSec: 20 * 60,
    label: `sees round ${round}'s clues`,
  }
}

const approveWhenOpen = (round: number): StepInput => ({
  actor: 'P1',
  do: 'vote',
  approve: true,
  when: { type: 'proposal', status: 'open' },
  timeoutSec: 5 * 60,
  label: `approves the group proposal in round ${round}`,
})

function roundSteps(round: number, r: (typeof ROUNDS)[number]): StepInput[] {
  const p1: StepInput[] = [onClues('P1', round, r.clues), { actor: 'P1', at: 0, do: 'wait', sec: 30 }, { actor: 'P1', at: 0, do: 'say', text: r.guess }]
  const p2: StepInput[] = [onClues('P2', round, r.clues), { actor: 'P2', at: 0, do: 'wait', sec: 50 }, { actor: 'P2', at: 0, do: 'say', text: r.agree }]
  const p3: StepInput[] = [onClues('P3', round, r.clues), { actor: 'P3', at: 0, do: 'wait', sec: 75 }]
  if (r.wrong) {
    p3.push({ actor: 'P3', at: 0, do: 'propose', fields: r.wrong, label: `proposes a wrong target in round ${round}` })
    p1.push(approveWhenOpen(round))
    // The second attempt follows TA's judgement of the first submission. P1 waits for it too:
    // armed at once, its next approval matched the first proposal while it still read as open.
    p1.push({ actor: 'P1', do: 'wait', sec: 1, when: { type: 'message', from: 'agent:TA' }, timeoutSec: 4 * 60 })
    p3.push({ actor: 'P3', do: 'wait', sec: 15, when: { type: 'message', from: 'agent:TA' }, timeoutSec: 4 * 60 })
  }
  p3.push({ actor: 'P3', at: 0, do: 'propose', fields: r.answer, label: `proposes the target in round ${round}` })
  p1.push(approveWhenOpen(round))
  return [...p1, ...p2, ...p3]
}

export default defineScenario({
  name: 'crat-autonomous',
  description: 'Phase 4 AC-11 CRAT class run by TA alone: eight group rounds, one wrong answer judged by TA.',
  durationSec: 50 * 60,
  room: {
    mode: 'create',
    name: 'sim-p4-crat-{ts}',
    access: 'memberGroups',
    agents: [
      { name: 'TA 字詞拼圖教師代理', role: 'normal', grantActivityTypes: ['crat-word-puzzle'] },
      { name: 'SA 字詞拼圖同儕代理', role: 'normal' },
      { name: 'AA 字詞拼圖分析代理', role: 'observer' },
    ],
    classStart: { minParticipants: 3, maxWaitMinutes: 5 },
  },
  memberGroup: { name: 'Sim CRAT P4 組', members: ['P1', 'P2', 'P3'] },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'P3', role: 'student', account: 3, displayName: '小晴' },
  ],
  steps: [
    { actor: 'P3', at: 40, do: 'connect', label: 'third student connects: the class starts' },
    { actor: 'P1', at: '1:00', when: { type: 'message', from: 'agent:TA' }, timeoutSec: 240, do: 'say', text: '我準備好了！' },
    { actor: 'P2', at: '1:20', when: { type: 'message', from: 'agent:TA' }, timeoutSec: 240, do: 'say', text: '我也準備好了，題目是什麼？' },
    // The single activation: each member opens the form once it starts.
    onRoundStart('P1', 1),
    onRoundStart('P2', 1),
    onRoundStart('P3', 1),
    ...ROUNDS.flatMap((r, i) => roundSteps(i + 1, r)),
  ],
  notes: [
    'Autonomy: no message and no panel action from F after setup. TA starts the one activation through its tool and ends it after C8.',
    'Class start: one TA wakeup.fired row with trigger class_start at about 00:40 (chatroom.class_started: reason min_participants, connected 3); TA opens with A1 before any student speaks.',
    'Judging: in round 4 TA rejects 「力」 itself, saying which clue word does not form a common word, does not reveal 「風」, and does not ask the creator; it accepts the second submission.',
    'Bank: C1 to C8 in order; after C8 TA summarises, ends the activity and closes the class. No target is revealed before the group submits.',
    'No time limit: [Activity state] says "No time limit." throughout, and no wakeup row with trigger activity_deadline or all_submitted appears (a group activity never completes on submissions).',
    'AC-14 still applies: invitations only where the step allows discussion, never the creator.',
  ],
})
