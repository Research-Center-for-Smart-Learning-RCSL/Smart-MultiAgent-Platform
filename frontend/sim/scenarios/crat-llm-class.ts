import { defineScenario } from '../scenario/schema.ts'

// Free discussion: three LLM-driven students in the CRAT room, for watching whether the
// teacher agent keeps control. Needs ANTHROPIC_API_KEY (or switch the provider). The
// group's answer is proposed by script, since personas only speak (spec §2 Non-goals).
const persona = (prompt: string) =>
  ({
    provider: 'anthropic',
    prompt: `${prompt}\nThe class is a CRAT word puzzle: find one character that forms a common two-character word with each of three clue characters.`,
    minIntervalSec: 25,
    maxTurns: 25,
    maxChars: 60,
    speakOnQuietSec: 90,
  }) as const

export default defineScenario({
  name: 'crat-llm-class',
  description: 'Three persona students and a silent guest in a 20-minute CRAT class.',
  durationSec: 20 * 60,
  room: {
    mode: 'create',
    name: 'sim-llm-{ts}',
    access: 'memberGroups',
    guestLinks: true,
    agents: [
      { name: 'TA 字詞拼圖教師代理', role: 'normal', grantActivityTypes: ['crat-word-puzzle'] },
      { name: 'SA 字詞拼圖同儕代理', role: 'normal' },
      { name: 'AA 字詞拼圖分析代理', role: 'observer' },
    ],
  },
  memberGroup: { name: 'Sim CRAT 第一組', members: ['P1', 'P2', 'P3'] },
  actors: [
    { name: 'F', role: 'facilitator' },
    {
      name: 'P1',
      role: 'student',
      account: 1,
      displayName: '小安',
      drive: 'hybrid',
      persona: persona('You are eager and answer fast, sometimes before thinking it through.'),
    },
    {
      name: 'P2',
      role: 'student',
      account: 2,
      displayName: '小博',
      drive: 'hybrid',
      persona: persona('You are hesitant: you often start typing and stop, and you ask others whether you are right.'),
    },
    {
      name: 'P3',
      role: 'student',
      account: 3,
      displayName: '小晴',
      drive: 'hybrid',
      persona: persona('You drift off topic now and then (games, lunch) but come back when the teacher asks.'),
    },
    { name: 'P4', role: 'guest', displayName: '小安' },
  ],
  steps: [
    { actor: 'F', at: 5, do: 'say', text: '同學好，今天我們來玩字詞拼圖，請助教帶大家開始。' },
    { actor: 'P1', at: '9:00', do: 'openActivity' },
    { actor: 'P2', at: '9:00', do: 'openActivity' },
    {
      actor: 'P1',
      at: '9:30',
      when: { type: 'activation', state: 'active' },
      timeoutSec: 300,
      do: 'propose',
      fields: { target_character: '心', word_1: '點心', word_2: '放心', word_3: '粗心', reasoning: '三個詞都常用' },
    },
    { actor: 'P2', at: '10:00', when: { type: 'proposal', status: 'open' }, timeoutSec: 300, do: 'vote', approve: true },
    {
      actor: 'F',
      at: '10:30',
      when: { type: 'message', from: 'agent:TA', match: '老師|確認' },
      timeoutSec: 300,
      do: 'say',
      text: '答案「心」是對的。',
    },
    { actor: 'F', at: '19:00', do: 'say', text: '時間到，請助教幫大家總結。' },
  ],
  budget: { maxCalls: 240, maxOutputTokens: 40_000 },
})
