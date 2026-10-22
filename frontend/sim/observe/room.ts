import type { Condition } from '../scenario/schema.ts'

export interface RoomMessage {
  id: string
  sender_type: 'user' | 'agent' | 'system' | 'guest'
  sender_id: string | null
  content_md: string
  metadata: Record<string, unknown>
  created_at: string | null
}

export interface Proposal {
  id: string
  activation_id: string
  member_group_id: string
  status: string
  required_approvals: number
  approvals: number
  rejections: number
  undecided: number
  created_at: string | null
  resolved_at: string | null
  submission_id: string | null
}

export interface Activation {
  id: string
  activity_type_id: string
  status: string
  created_at: string | null
  ended_at: string | null
}

/** Reads the facilitator's view of the room; implemented over the API client. */
export interface RoomSource {
  latestMessages(limit: number): Promise<RoomMessage[]>
  members(): Promise<Array<{ user_id: string; display_name: string | null; kind: string }>>
  agents(): Promise<Array<{ agent_id: string; name?: string | null }>>
  activeActivation(): Promise<Activation | null>
  /** Open proposals only (`proposal_repo.py:192`). */
  proposals(activationId: string): Promise<Proposal[]>
  proposal(id: string): Promise<Proposal>
}

export interface Seen {
  message: RoomMessage
  /** Local receive time, ms since epoch. */
  seenAt: number
}

type Listener = (m: RoomMessage) => void

/**
 * One shared view of the room, built by polling as the facilitator, so conditions and
 * personas work the same whether the facilitator is scripted or a person (D-1).
 */
export class RoomObserver {
  private readonly source: RoomSource
  readonly messages: Seen[] = []
  private readonly ids = new Set<string>()
  readonly names = new Map<string, string>()
  /** Sender id to scenario actor name, filled in as identities become known. */
  readonly actorBySender = new Map<string, string>()
  readonly proposals = new Map<string, Proposal>()
  activation: Activation | null = null
  lastEndedActivation: Activation | null = null
  private readonly listeners = new Set<Listener>()
  private timers: NodeJS.Timeout[] = []
  private readonly onError: (where: string, err: unknown) => void

  constructor(source: RoomSource, onError: (where: string, err: unknown) => void) {
    this.source = source
    this.onError = onError
  }

  onMessage(fn: Listener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  start(intervals = { messagesMs: 2000, activationMs: 5000, proposalsMs: 5000 }): void {
    const loop = (fn: () => Promise<void>, ms: number, where: string): void => {
      let running = false
      const tick = (): void => {
        if (running) return
        running = true
        fn()
          .catch((err) => this.onError(where, err))
          .finally(() => {
            running = false
          })
      }
      tick()
      this.timers.push(setInterval(tick, ms))
    }
    loop(() => this.pollMessages(), intervals.messagesMs, 'messages')
    loop(() => this.pollActivation(), intervals.activationMs, 'activation')
    loop(() => this.pollProposals(), intervals.proposalsMs, 'proposals')
  }

  stop(): void {
    for (const t of this.timers) clearInterval(t)
    this.timers = []
  }

  async pollMessages(): Promise<void> {
    const page = await this.source.latestMessages(50)
    const fresh = page.filter((m) => !this.ids.has(m.id)).reverse()
    if (fresh.some((m) => m.sender_id && !this.names.has(m.sender_id))) await this.refreshNames()
    // Messages already in the room before the first poll are history: kept for persona
    // context, but they satisfy no armed condition and wake no listener.
    const history = !this.primed
    this.primed = true
    for (const m of fresh) {
      this.ids.add(m.id)
      this.messages.push({ message: m, seenAt: history ? 0 : Date.now() })
      if (!history) for (const fn of this.listeners) fn(m)
    }
  }

  private primed = false

  async refreshNames(): Promise<void> {
    const [members, agents] = await Promise.all([this.source.members(), this.source.agents()])
    for (const m of members) if (m.display_name) this.names.set(m.user_id, m.display_name)
    for (const a of agents) if (a.name) this.names.set(a.agent_id, a.name)
  }

  async pollActivation(): Promise<void> {
    const next = await this.source.activeActivation()
    if (this.activation && (!next || next.id !== this.activation.id)) {
      this.lastEndedActivation = { ...this.activation, status: 'ended' }
    }
    this.activation = next
  }

  async pollProposals(): Promise<void> {
    const activationIds = new Set<string>()
    if (this.activation) activationIds.add(this.activation.id)
    if (this.lastEndedActivation) activationIds.add(this.lastEndedActivation.id)
    const listed = new Set<string>()
    for (const id of activationIds) {
      for (const p of await this.source.proposals(id)) {
        listed.add(p.id)
        this.proposals.set(p.id, p)
      }
    }
    // The list shows open proposals only, so one that left it has settled; read its outcome.
    // A proposal opened and settled between two polls is never seen.
    for (const p of [...this.proposals.values()]) {
      if (p.status === 'open' && !listed.has(p.id)) this.proposals.set(p.id, await this.source.proposal(p.id))
    }
  }

  senderName(m: RoomMessage): string {
    if (m.sender_type === 'system') return 'system'
    return (m.sender_id && this.names.get(m.sender_id)) ?? m.sender_id?.slice(0, 8) ?? 'unknown'
  }

  /** Whether a condition holds now, considering messages seen at or after `armedAt`. */
  satisfied(cond: Condition, armedAt: number): boolean {
    switch (cond.type) {
      case 'message': {
        const re = cond.match ? new RegExp(cond.match) : null
        return this.messages.some(
          (s) =>
            (!cond.sinceArmed || s.seenAt >= armedAt) &&
            this.fromMatches(cond.from, s.message) &&
            (!re || re.test(s.message.content_md)),
        )
      }
      case 'activation':
        return cond.state === 'active' ? this.activation !== null : this.activation === null && this.lastEndedActivation !== null
      case 'proposal':
        return [...this.proposals.values()].some((p) => p.status === cond.status)
    }
  }

  fromMatches(from: string, m: RoomMessage): boolean {
    if (from === 'anyone') return m.sender_type !== 'system'
    if (from === 'human') return m.sender_type === 'user' || m.sender_type === 'guest'
    if (from === 'agent') return m.sender_type === 'agent'
    if (from.startsWith('agent:')) {
      const want = from.slice('agent:'.length)
      return m.sender_type === 'agent' && !!m.sender_id && (this.names.get(m.sender_id) ?? '').includes(want)
    }
    return !!m.sender_id && this.actorBySender.get(m.sender_id) === from
  }

  /** The recent room as text lines, for a persona prompt. */
  transcript(selfSenderId: string | null, lastN: number): string[] {
    return this.messages.slice(-lastN).map(({ message: m }) => {
      const time = m.created_at ? new Date(m.created_at).toISOString().slice(11, 19) : ''
      const who =
        m.sender_type === 'system'
          ? '[system]'
          : m.sender_id && m.sender_id === selfSenderId
            ? `${this.senderName(m)} (you)`
            : m.sender_type === 'agent'
              ? `${this.senderName(m)} (AI)`
              : this.senderName(m)
      return `[${time}] ${who}: ${m.content_md.replace(/\s+/g, ' ').slice(0, 500)}`
    })
  }
}
