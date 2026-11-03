import { defineScenario } from '../scenario/schema.ts'

// docs/tasks/2026-10-19-chat-bubble-sides-and-agent-colors AC-13 and AC-17: two signed-in students,
// a guest and the facilitator each post, then everyone stays connected while the operator, signed
// in as a project or org owner, colours TA and SA in room settings, @mentions both, and later
// changes one colour. The end-of-run screenshots (screens/end-<actor>.png) show each viewer's sides
// and whether the later colour reached clients that never reloaded.

export default defineScenario({
  name: 'bubble-check',
  description: 'Chat bubble sides and agent colours across two students, a guest and the facilitator.',
  durationSec: 14 * 60,
  room: {
    mode: 'create',
    name: 'sim-bubbles-{ts}',
    access: 'projectMembers',
    guestLinks: true,
    agents: [
      { name: 'TA 字詞拼圖教師代理', role: 'normal' },
      { name: 'SA 字詞拼圖同儕代理', role: 'normal' },
    ],
  },
  actors: [
    { name: 'F', role: 'facilitator' },
    { name: 'P1', role: 'student', account: 1, displayName: '小安' },
    { name: 'P2', role: 'student', account: 2, displayName: '小博' },
    { name: 'G', role: 'guest', displayName: '訪客小林' },
  ],
  steps: [
    { actor: 'F', at: 5, do: 'say', text: '大家好，這是聊天泡泡的檢查，請每個人說一句話。', label: 'facilitator posts' },
    { actor: 'P1', at: 20, do: 'say', text: '我是小安，這是我的訊息。', label: 'first student posts' },
    { actor: 'P2', at: 35, do: 'say', text: '我是小博，這是我的訊息。', label: 'second student posts' },
    { actor: 'G', at: 50, do: 'say', text: '我是訪客小林，這是我的訊息。', label: 'guest posts' },
    { actor: 'P1', at: 65, do: 'say', text: '小安的第二則訊息。', label: 'first student posts again' },
    // Agent replies push the opening messages out of view; these keep every viewer's own message
    // and the others' in the end screenshots.
    { actor: 'F', at: '12:30', do: 'say', text: '老師的最後一則訊息。', label: 'facilitator posts last' },
    { actor: 'P1', at: '12:40', do: 'say', text: '小安的最後一則訊息。', label: 'first student posts last' },
    { actor: 'P2', at: '12:50', do: 'say', text: '小博的最後一則訊息。', label: 'second student posts last' },
    { actor: 'G', at: '13:00', do: 'say', text: '訪客小林的最後一則訊息。', label: 'guest posts last' },
  ],
  notes: [
    'Operator, from minute 2: colour TA and SA in room settings, then @mention each so both answer.',
    'Operator, at about minute 8: change TA to a different colour and do not reload any client; at about minute 11 @mention TA and SA again so their newest replies sit just above the closing messages.',
    'Pass (AC-17): in each end screenshot only that viewer\'s own messages sit on the right; TA and SA sit on the left in their colours.',
    'Pass (AC-13): every end screenshot shows TA in the colour chosen at minute 8, with no reload on any client.',
  ],
})
