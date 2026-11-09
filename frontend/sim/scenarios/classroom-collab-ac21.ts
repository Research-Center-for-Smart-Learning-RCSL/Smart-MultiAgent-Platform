import { defineScenario } from '../scenario/schema.ts'
import type { ScenarioInput } from '../scenario/schema.ts'
import { onClassClose, onRoundStart } from '../scenario/rounds.ts'

// AC-21 of docs/tasks/2026-11-09-classroom-agent-collaboration: one CRAT class with the three
// classroom roles set, so the run exercises alerts (M1), analyst notices (M2) and peer reviews (M3).
//
// The three mechanisms depend on model decisions, so the script provokes each one rather than
// forcing it:
// - Peer reviews: the students @mention SA once per round. SA's prompt makes its second answer a
//   deliberate wrong one, so TA reviews at least one wrong draft beside correct ones, which is
//   where different decisions (approve, modify, reject) come from.
// - Alerts and notices: AA wakes every 20 student messages and after 10 minutes of silence. The
//   script sends well over 40 student messages, and round 3 opens with a stall that drifts off
//   topic, the "group that is stuck" case the alert argument describes. A wrong SA answer that
//   reaches the room is the "agent said something wrong" case for both an alert and a notice.
// - Reaction times: the operator keeps the teacher dashboard open in their own browser while the
//   run plays (the alert list reports what it shows as viewed), and F's message at the end of
//   round 3 is the creator's first action after the alerts raised before it.
//
// Preconditions: the CRAT room pack agents exist in the project with the names below, the
// deployed commit includes PR #327, and the operator has the dashboard open on this project.

type StepInput = ScenarioInput['steps'][number]
type Answer = { target_character: string; word_1: string; word_2: string; word_3: string; reasoning: string }

const SA = '@SA 字詞拼圖同儕代理'

const ROUNDS: Array<{ clues: [string, string, string]; talk: Array<[string, string]>; ask: [string, string]; answer: Answer }> = [
  {
    clues: ['點', '放', '粗'],
    talk: [
      ['P1', '點心、放心……粗心也可以耶'],
      ['P3', '我想到點火、放火，可是粗火怪怪的'],
      ['P2', '心的話三個都是常用詞'],
      ['P3', '粗心我常常被老師說'],
      ['P2', '哈哈我也是'],
      ['P1', '那就是心吧'],
    ],
    ask: ['P2', `${SA} 你覺得這題是什麼字？`],
    answer: { target_character: '心', word_1: '點心', word_2: '放心', word_3: '粗心', reasoning: '三個都是常用詞' },
  },
  {
    clues: ['口', '淚', '香'],
    talk: [
      ['P2', '口水、淚水、香水？'],
      ['P3', '口味、香味也可以，可是淚味不是詞'],
      ['P1', '所以水比較好'],
      ['P3', '還是要確認一下三個都通'],
      ['P1', '口水、淚水、香水，都通'],
      ['P2', '我也覺得是水'],
    ],
    ask: ['P1', `${SA} 你有想到別的字嗎？`],
    answer: { target_character: '水', word_1: '口水', word_2: '淚水', word_3: '香水', reasoning: '口味可以但淚味不行，水三個都通' },
  },
  {
    clues: ['主', '客', '詩'],
    talk: [
      ['P1', '這題好難，我完全想不到'],
      ['P2', '我也是，腦袋一片空白'],
      ['P3', '要不要先休息一下，你們昨天有看籃球比賽嗎'],
      ['P1', '有啊，最後一分鐘超刺激'],
      ['P2', '我只看了下半場'],
      ['P3', '下次一起看好了'],
      ['P1', '欸我們好像離題了'],
      ['P2', '對啦，回來想題目'],
      ['P3', '主人、客人、詩人？'],
      ['P1', '對！是人'],
    ],
    ask: ['P3', `${SA} 你覺得「人」對嗎？還是有更好的？`],
    answer: { target_character: '人', word_1: '主人', word_2: '客人', word_3: '詩人', reasoning: '都是指某一種人' },
  },
  {
    clues: ['眼', '時', '觀'],
    talk: [
      ['P2', '眼光、時光、觀光'],
      ['P1', '光三個都很順'],
      ['P3', '眼神、時神？不對'],
      ['P1', '時光比時神正常多了'],
      ['P3', '觀光也很常用'],
      ['P2', '眼界、時界？好像也不對'],
      ['P1', '光最穩'],
      ['P3', '這題比上一題簡單'],
      ['P2', '那就是光'],
    ],
    ask: ['P2', `${SA} 你也覺得是光嗎？`],
    answer: { target_character: '光', word_1: '眼光', word_2: '時光', word_3: '觀光', reasoning: '三個都是常用詞' },
  },
]

/** TA's reveal of a round: its three clue characters close together in one message. */
function onClues(actor: string, round: number, clues: [string, string, string]): StepInput {
  const [a, b, c] = clues
  return {
    actor,
    do: 'wait',
    sec: 1,
    when: { type: 'message', from: 'agent:TA', match: `${a}[^\\n]{0,12}${b}[^\\n]{0,12}${c}`, sinceArmed: false },
    timeoutSec: 8 * 60,
    label: `sees round ${round}'s clues`,
  }
}

const GAP_SEC = 25
// Time for the held SA reply to be reviewed and posted before the group submits.
const REVIEW_SEC = 90

/**
 * One round, timed from TA's clue reveal. Each actor's steps run in order, so an actor's `wait` is
 * the gap since its own previous step; `cursor` turns the round's offsets into those gaps.
 */
function roundSteps(round: number, r: (typeof ROUNDS)[number]): StepInput[] {
  const byActor = new Map<string, StepInput[]>()
  const cursor = new Map<string, number>()
  for (const a of ['P1', 'P2', 'P3']) {
    byActor.set(a, [onClues(a, round, r.clues)])
    cursor.set(a, 0)
  }
  const at = (actor: string, offsetSec: number, step: StepInput) => {
    const gap = offsetSec - (cursor.get(actor) ?? 0)
    const list = byActor.get(actor) as StepInput[]
    if (gap > 0) list.push({ actor, at: 0, do: 'wait', sec: gap })
    list.push(step)
    cursor.set(actor, offsetSec)
  }
  r.talk.forEach(([actor, text], i) => at(actor, 20 + i * GAP_SEC, { actor, at: 0, do: 'say', text }))
  const askAt = 20 + r.talk.length * GAP_SEC
  const [asker, question] = r.ask
  at(asker, askAt, { actor: asker, at: 0, do: 'say', text: question, label: `asks SA in round ${round}` })
  at('P3', askAt + REVIEW_SEC, { actor: 'P3', at: 0, do: 'propose', fields: r.answer, label: `proposes the target in round ${round}` })
  ;(byActor.get('P1') as StepInput[]).push({
    actor: 'P1',
    at: 0,
    do: 'vote',
    approve: true,
    when: { type: 'proposal', status: 'open' },
    timeoutSec: 6 * 60,
    label: `approves the group proposal in round ${round}`,
  })
  return [...byActor.values()].flat()
}

export default defineScenario({
  name: 'classroom-collab-ac21',
  description: 'AC-21: CRAT class with teacher, peer and analyst roles; provokes alerts, notices and peer reviews.',
  durationSec: 45 * 60,
  room: {
    mode: 'create',
    name: 'sim-ac21-collab-{ts}',
    access: 'memberGroups',
    agents: [
      { name: 'TA 字詞拼圖教師代理', role: 'normal', grantActivityTypes: ['crat-word-puzzle'], classroomRole: 'teacher' },
      { name: 'SA 字詞拼圖同儕代理', role: 'normal', classroomRole: 'peer' },
      { name: 'AA 字詞拼圖分析代理', role: 'observer', classroomRole: 'analyst' },
    ],
    classStart: { minParticipants: 3, maxWaitMinutes: 5 },
  },
  memberGroup: { name: 'Sim AC-21 協作組', members: ['P1', 'P2', 'P3'] },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'P3', role: 'student', account: 3, displayName: '小晴' },
  ],
  steps: [
    { actor: 'P3', at: 40, do: 'connect', label: 'third student connects: the class starts' },
    { actor: 'P1', at: '1:00', when: { type: 'message', from: 'agent:TA', sinceArmed: false }, timeoutSec: 240, do: 'say', text: '我準備好了！' },
    { actor: 'P2', at: '1:20', when: { type: 'message', from: 'agent:TA', sinceArmed: false }, timeoutSec: 240, do: 'say', text: '我也準備好了' },
    { actor: 'P3', at: '1:40', when: { type: 'message', from: 'agent:TA', sinceArmed: false }, timeoutSec: 240, do: 'say', text: '我來了，開始吧' },
    onRoundStart('P1', 1),
    onRoundStart('P2', 1),
    onRoundStart('P3', 1),
    ...ROUNDS.slice(0, 3).flatMap((r, i) => roundSteps(i + 1, r)),
    // The creator's first action after the round 3 stall: sets first_action_at on every alert raised before it.
    {
      actor: 'F',
      at: '15:00',
      when: { type: 'message', from: 'agent:TA', match: `${ROUNDS[3]!.clues[0]}[^\\n]{0,12}${ROUNDS[3]!.clues[1]}`, sinceArmed: false },
      timeoutSec: 25 * 60,
      do: 'say',
      text: '大家剛剛有點卡住，不過後來找回來了，很好，繼續加油。',
      label: 'creator speaks once after the stall (first action)',
    },
    ...roundSteps(4, ROUNDS[3]!),
    onClassClose('P1'),
  ],
  notes: [
    'Operator: before starting, open the teacher dashboard for this project in your own browser and leave it open; the alert list reports shown alerts as viewed (first_viewed_via = dashboard).',
    'Peer reviews: SA is @mentioned four times; every SA turn is held, and TA decides each. AC-21 needs at least two different decisions among them (collaboration log on the dashboard, or peer_reviews.csv).',
    'Alerts: at least one AA observation with is_alert, then first_viewed_at from the dashboard and first_action_at from F\'s message (alerts.csv).',
    'Notices: at least one AA notice to TA with TA\'s decision and reason (collaboration log, analyst_notices.csv). The likeliest cause is SA\'s deliberate wrong answer if TA lets it through.',
    'Export: from the dashboard, export research data for this workspace after the run and open alerts.csv, analyst_notices.csv and peer_reviews.csv in Excel; Chinese must read correctly (the files carry a BOM).',
    'If a mechanism did not fire, that is a model decision, not a failure of the run; rerun, or record which one stayed silent.',
  ],
})
