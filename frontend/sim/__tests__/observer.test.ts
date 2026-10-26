import { describe, expect, it } from 'vitest'
import { RoomObserver, type Activation, type Proposal, type RoomMessage, type RoomSource } from '../observe/room.ts'

function msg(id: string, sender_type: RoomMessage['sender_type'], sender_id: string | null, content: string): RoomMessage {
  return { id, sender_type, sender_id, content_md: content, metadata: {}, created_at: '2026-10-22T10:00:00Z' }
}

function proposal(id: string, status: string): Proposal {
  return { id, activation_id: 'act', member_group_id: 'g', status, required_approvals: 2, approvals: 1, rejections: 0, undecided: 2, created_at: null, resolved_at: null, submission_id: null }
}

function source() {
  const s = {
    latest: [] as RoomMessage[],
    activation: null as Activation | null,
    open: [] as Proposal[],
    byId: new Map<string, Proposal>(),
    fetched: [] as string[],
  }
  const src: RoomSource = {
    latestMessages: async () => [...s.latest].reverse(),
    members: async () => [
      { user_id: 'u1', display_name: '小安', kind: 'user' },
      { user_id: 'g1', display_name: '小安', kind: 'guest' },
    ],
    agents: async () => [{ agent_id: 'ta', name: 'TA 字詞拼圖教師代理' }, { agent_id: 'aa', name: 'AA 字詞拼圖分析代理' }],
    activeActivation: async () => s.activation,
    proposals: async () => s.open,
    proposal: async (id) => {
      s.fetched.push(id)
      return s.byId.get(id) as Proposal
    },
  }
  return { s, src }
}

describe('RoomObserver', () => {
  it('treats messages present before the first poll as history', async () => {
    const { s, src } = source()
    s.latest = [msg('m1', 'user', 'u1', '舊訊息')]
    const obs = new RoomObserver(src, () => undefined)
    const heard: string[] = []
    obs.onMessage((m) => heard.push(m.id))
    obs.actorBySender.set('u1', 'P1')
    await obs.pollMessages()
    expect(heard).toEqual([])
    expect(obs.satisfied({ type: 'message', from: 'P1', sinceArmed: true }, 1)).toBe(false)
    expect(obs.satisfied({ type: 'message', from: 'P1', sinceArmed: false }, 1)).toBe(true)

    s.latest.push(msg('m2', 'user', 'u1', '我覺得是心'))
    const armedAt = Date.now()
    await obs.pollMessages()
    expect(heard).toEqual(['m2'])
    expect(obs.satisfied({ type: 'message', from: 'P1', match: '心', sinceArmed: true }, armedAt)).toBe(true)
    expect(obs.satisfied({ type: 'message', from: 'P1', match: '火', sinceArmed: true }, armedAt)).toBe(false)
  })

  it('matches senders by actor, agent name, kind and anyone', async () => {
    const { s, src } = source()
    const obs = new RoomObserver(src, () => undefined)
    await obs.pollMessages()
    s.latest = [msg('a', 'agent', 'ta', '請老師確認'), msg('b', 'guest', 'g1', 'hi'), msg('c', 'system', null, 'joined')]
    await obs.pollMessages()
    const [ta, guest, sys] = s.latest as [RoomMessage, RoomMessage, RoomMessage]
    expect(obs.fromMatches('agent:TA', ta)).toBe(true)
    expect(obs.fromMatches('agent:AA', ta)).toBe(false)
    expect(obs.fromMatches('agent', ta)).toBe(true)
    expect(obs.fromMatches('human', guest)).toBe(true)
    expect(obs.fromMatches('anyone', sys)).toBe(false)
    expect(obs.fromMatches('system', sys)).toBe(true)
    expect(obs.fromMatches('system', ta)).toBe(false)
    obs.actorBySender.set('g1', 'P4')
    expect(obs.fromMatches('P4', guest)).toBe(true)
    expect(obs.fromMatches('P1', guest)).toBe(false)
  })

  it('reads the outcome of a proposal that left the open list (OQ-1)', async () => {
    const { s, src } = source()
    s.activation = { id: 'act', activity_type_id: 't', status: 'active', created_at: null, ended_at: null }
    const obs = new RoomObserver(src, () => undefined)
    await obs.pollActivation()
    s.open = [proposal('p1', 'open')]
    await obs.pollProposals()
    expect(obs.satisfied({ type: 'proposal', status: 'open' }, 0)).toBe(true)
    expect(s.fetched).toEqual([])

    s.open = []
    s.byId.set('p1', proposal('p1', 'rejected'))
    await obs.pollProposals()
    expect(s.fetched).toEqual(['p1'])
    expect(obs.satisfied({ type: 'proposal', status: 'rejected' }, 0)).toBe(true)
    expect(obs.satisfied({ type: 'proposal', status: 'open' }, 0)).toBe(false)

    await obs.pollProposals()
    expect(s.fetched).toEqual(['p1'])
  })

  it('tracks activation start and end', async () => {
    const { s, src } = source()
    const obs = new RoomObserver(src, () => undefined)
    await obs.pollActivation()
    expect(obs.satisfied({ type: 'activation', state: 'active' }, 0)).toBe(false)
    expect(obs.satisfied({ type: 'activation', state: 'ended' }, 0)).toBe(false)
    s.activation = { id: 'act', activity_type_id: 't', status: 'active', created_at: null, ended_at: null }
    await obs.pollActivation()
    expect(obs.satisfied({ type: 'activation', state: 'active' }, 0)).toBe(true)
    s.activation = null
    await obs.pollActivation()
    expect(obs.satisfied({ type: 'activation', state: 'ended' }, 0)).toBe(true)
  })

  it('labels the persona transcript with self, AI and system markers', async () => {
    const { s, src } = source()
    const obs = new RoomObserver(src, () => undefined)
    s.latest = [msg('1', 'user', 'u1', '我先'), msg('2', 'agent', 'ta', '很好'), msg('3', 'system', null, 'x joined')]
    await obs.pollMessages()
    const lines = obs.transcript('u1', 10)
    expect(lines[0]).toContain('小安 (you): 我先')
    expect(lines[1]).toContain('TA 字詞拼圖教師代理 (AI): 很好')
    expect(lines[2]).toContain('[system]: x joined')
  })
})
