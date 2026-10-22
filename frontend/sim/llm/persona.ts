import type { Persona } from '../scenario/schema.ts'
import { parseDecision, type PersonaDecision, type Provider } from './provider.ts'

/** Run-wide hard limits on persona spending (spec AC-10). */
export class Budget {
  readonly maxCalls: number
  readonly maxOutputTokens: number
  calls = 0
  inputTokens = 0
  outputTokens = 0

  constructor(maxCalls: number, maxOutputTokens: number) {
    this.maxCalls = maxCalls
    this.maxOutputTokens = maxOutputTokens
  }

  tryReserve(): boolean {
    if (this.calls >= this.maxCalls || this.outputTokens >= this.maxOutputTokens) return false
    this.calls++
    return true
  }

  record(input: number, output: number): void {
    this.inputTokens += input
    this.outputTokens += output
  }
}

export interface PersonaHost {
  selfName: string
  transcript(): string[]
  /** True while a script step owns this actor (hybrid pre-emption). */
  isBusy(): boolean
  act(decision: Exclude<PersonaDecision, { action: 'silent' }>, signal: AbortSignal): Promise<void>
  log(kind: string, fields: Record<string, unknown>): void
}

export interface Timers {
  now(): number
  setTimeout(fn: () => void, ms: number): unknown
  clearTimeout(handle: unknown): void
  random(): number
}

export const realTimers: Timers = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as NodeJS.Timeout),
  random: () => Math.random(),
}

export function buildPrompts(persona: Persona, selfName: string, transcript: string[]): { system: string; user: string } {
  const system = [
    `You are role-playing a student named "${selfName}" in a live classroom chat. Write in ${persona.language}.`,
    persona.prompt,
    'Behave like a real student: short chat messages, no lists or headings, never mention being an AI or a simulation.',
    'Staying silent is often right, for example when someone else just answered, when the teacher asked the group to work quietly, or when you have nothing new to add.',
    `Reply with one JSON object only: {"action": "say", "text": "..."} to send a message, {"action": "type_pause", "text": "..."} to start typing and then hesitate, or {"action": "silent"}. Keep text under ${persona.maxChars} characters.`,
  ].join('\n')
  const user = ['Recent room messages, oldest first:', ...transcript, '', 'What do you do now?'].join('\n')
  return { system, user }
}

/**
 * Decides when a simulated student speaks. Each new room message (not its own) schedules
 * one decision after a reading delay and the persona's minimum interval; messages that
 * arrive meanwhile are folded into that decision, which reads the latest transcript.
 */
export class PersonaLoop {
  private readonly persona: Persona
  private readonly provider: Provider
  private readonly model: string
  private readonly budget: Budget
  private readonly host: PersonaHost
  private readonly timers: Timers
  enabled = true
  turns = 0
  private lastSpokeAt = Number.NEGATIVE_INFINITY
  private pending: unknown = null
  private inflight: AbortController | null = null
  private exhaustedLogged = false

  constructor(persona: Persona, provider: Provider, model: string, budget: Budget, host: PersonaHost, timers: Timers = realTimers) {
    this.persona = persona
    this.provider = provider
    this.model = model
    this.budget = budget
    this.host = host
    this.timers = timers
  }

  onRoomMessage(fromSelf: boolean): void {
    if (fromSelf || !this.enabled) return
    this.schedule()
  }

  /** Called periodically; prompts a decision when the room has been quiet long enough. */
  onQuietCheck(lastMessageAt: number): void {
    const quiet = this.persona.speakOnQuietSec
    if (!this.enabled || quiet === undefined) return
    const now = this.timers.now()
    if (now - lastMessageAt >= quiet * 1000 && now - this.lastSpokeAt >= quiet * 1000) this.schedule()
  }

  setEnabled(on: boolean): void {
    this.enabled = on
    if (!on) this.cancel()
  }

  /** Drops a scheduled decision and aborts one in flight, so a script step goes first. */
  cancel(): void {
    if (this.pending !== null) this.timers.clearTimeout(this.pending)
    this.pending = null
    this.inflight?.abort()
    this.inflight = null
  }

  private schedule(): void {
    if (this.pending !== null || this.inflight) return
    const [lo, hi] = this.persona.readDelaySec
    const reading = (lo + this.timers.random() * Math.max(hi - lo, 0)) * 1000
    const sinceSpoke = this.timers.now() - this.lastSpokeAt
    const interval = Math.max(this.persona.minIntervalSec * 1000 - sinceSpoke, 0)
    this.pending = this.timers.setTimeout(() => {
      this.pending = null
      void this.decide()
    }, Math.max(reading, interval))
  }

  async decide(): Promise<void> {
    if (!this.enabled || this.host.isBusy()) return
    if (this.turns >= this.persona.maxTurns || !this.budget.tryReserve()) {
      if (!this.exhaustedLogged) {
        this.exhaustedLogged = true
        this.host.log('persona.exhausted', {
          turns: this.turns,
          calls: this.budget.calls,
          outputTokens: this.budget.outputTokens,
        })
      }
      return
    }
    const controller = new AbortController()
    this.inflight = controller
    try {
      const { system, user } = buildPrompts(this.persona, this.host.selfName, this.host.transcript())
      const result = await this.provider.complete({
        model: this.model,
        system,
        user,
        maxOutputTokens: Math.max(64, this.persona.maxChars * 4),
        signal: controller.signal,
      })
      this.budget.record(result.inputTokens, result.outputTokens)
      const decision = parseDecision(result.text, this.persona.maxChars)
      this.host.log('persona.decision', {
        action: decision.action,
        inputTokens: result.inputTokens,
        outputTokens: result.outputTokens,
      })
      if (decision.action === 'silent' || controller.signal.aborted || this.host.isBusy()) return
      this.turns++
      this.lastSpokeAt = this.timers.now()
      await this.host.act(decision, controller.signal)
    } catch (err) {
      if (!controller.signal.aborted) this.host.log('persona.error', { error: String(err) })
    } finally {
      if (this.inflight === controller) this.inflight = null
    }
  }
}
