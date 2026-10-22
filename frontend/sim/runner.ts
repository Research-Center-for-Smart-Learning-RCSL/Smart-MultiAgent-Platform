import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium, type Browser } from '@playwright/test'
import type { Target } from './config/target.ts'
import { assertRateLimitChangeAllowed } from './config/target.ts'
import { requireEnv, secretFromEnvOrPrompt } from './config/secrets.ts'
import { ApiClient } from './lib/http.ts'
import { Logger, Redactor } from './lib/log.ts'
import { AuthLimitGuard, withRaisedAuthLimit, type RateLimitAdmin, type RateLimitPolicy } from './limits/authLimit.ts'
import type { Actor, Persona, Scenario, Step } from './scenario/schema.ts'
import { provision, type SimAccount } from './setup/provision.ts'
import { resolveType, setupRoom, type RoomSetup } from './setup/room.ts'
import { Participant, bubbleSnippet } from './browser/participant.ts'
import { RoomObserver, type Activation, type Proposal, type RoomMessage, type RoomSource } from './observe/room.ts'
import { AnthropicProvider, OpenAIProvider, type Provider } from './llm/provider.ts'
import { Budget, PersonaLoop } from './llm/persona.ts'
import { realClock, runActor, stepsByActor, type StepExecutor, type StepRecord } from './scheduler/run.ts'
import { buildReport, collectTranscript, collectWakeAudit, writeJson, type EvidenceSource } from './evidence/collect.ts'
import { formatOffset } from './scenario/load.ts'

export interface Session {
  target: Target
  redactor: Redactor
  log: Logger
  admin: ApiClient
  facilitator: ApiClient
  guard: AuthLimitGuard
  simRoot: string
  /** Kept for the facilitator's browser login, since a prompted password is not in the environment. */
  facilitatorPassword: string
}

export function rateLimitAdmin(admin: ApiClient): RateLimitAdmin {
  return {
    list: () => admin.get<RateLimitPolicy[]>('/api/admin/rate-limits?limit=500'),
    patch: (key, body) => admin.patch<RateLimitPolicy>(`/api/admin/rate-limits/${encodeURIComponent(key)}`, body),
  }
}

export const SECRET_ENV = [
  'SMAP_SIM_ADMIN_PASSWORD',
  'SMAP_SIM_FACILITATOR_PASSWORD',
  'SMAP_SIM_SECRET',
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
] as const

/** Logs in the admin and the facilitator and restores any limit a killed run left raised. */
export async function openSession(target: Target, simRoot: string, redactor: Redactor): Promise<Session> {
  const log = new Logger(redactor)
  const adminPassword = await secretFromEnvOrPrompt('SMAP_SIM_ADMIN_PASSWORD', `Admin password for ${target.adminEmail}: `)
  const admin = new ApiClient(target.baseUrl, redactor)
  await admin.login(target.adminEmail, adminPassword)
  let facilitator = admin
  let facilitatorPassword = adminPassword
  if (target.facilitatorEmail.toLowerCase() !== target.adminEmail.toLowerCase()) {
    facilitatorPassword = await secretFromEnvOrPrompt('SMAP_SIM_FACILITATOR_PASSWORD', `Facilitator password for ${target.facilitatorEmail}: `)
    facilitator = new ApiClient(target.baseUrl, redactor)
    await facilitator.login(target.facilitatorEmail, facilitatorPassword)
  }
  const guard = new AuthLimitGuard(rateLimitAdmin(admin), join(simRoot, '.state', `${target.name}.json`), target.name)
  const leftover = await guard.restoreLeftover()
  if (leftover) log.warn(`restored the AUTH limit an earlier run left raised (max_count ${leftover.max_count}/${leftover.window_sec}s)`)
  return { target, redactor, log, admin, facilitator, guard, simRoot, facilitatorPassword }
}

function describeLimit(p: RateLimitPolicy | null): string {
  return p ? `restored to ${p.max_count} per ${p.window_sec}s` : 'not restored (nothing recorded)'
}

export async function withAuthLimit<T>(s: Session, body: () => Promise<T>): Promise<{ value: T; restored: string }> {
  assertRateLimitChangeAllowed(s.target)
  let restored = 'unknown'
  const value = await withRaisedAuthLimit(s.guard, s.target.authLimitPerMin, body, (p) => {
    restored = describeLimit(p)
    s.log.info(`AUTH limit ${restored}`)
  })
  return { value, restored }
}

function runTag(d = new Date()): string {
  return d.toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15)
}

function facilitatorSource(api: ApiClient, roomId: string): RoomSource & EvidenceSource {
  return {
    latestMessages: (limit) => api.get<RoomMessage[]>(`/api/chatrooms/${roomId}/messages?limit=${limit}`),
    members: () => api.get(`/api/chatrooms/${roomId}/members`),
    agents: () => api.get(`/api/chatrooms/${roomId}/agents?limit=500`),
    activeActivation: () => api.get<Activation | null>(`/api/chatrooms/${roomId}/activity-activations/active`),
    proposals: async (activationId) =>
      (await api.get<{ items: Proposal[] }>(`/api/chatrooms/${roomId}/activity-proposals?activation_id=${activationId}`)).items,
    proposal: (id) => api.get<Proposal>(`/api/chatrooms/${roomId}/activity-proposals/${id}`),
    messagesPage: (before) =>
      api.get<RoomMessage[]>(`/api/chatrooms/${roomId}/messages?limit=200${before ? `&before=${before}` : ''}`),
    auditPage: () => {
      throw new Error('audit pages are read as the admin')
    },
  }
}

function adminAuditSource(admin: ApiClient, base: EvidenceSource): EvidenceSource {
  return {
    messagesPage: base.messagesPage,
    auditPage: ({ action, resourceId, from, to, cursor }) => {
      const q = new URLSearchParams({ action, resource_type: 'agent', resource_id: resourceId, from, to, limit: '200' })
      if (cursor !== null) q.set('cursor', String(cursor))
      return admin.get(`/api/admin/audit?${q.toString()}`)
    },
  }
}

function makeProvider(persona: Persona): { provider: Provider; model: string } {
  if (persona.provider === 'openai') {
    const model = persona.model ?? process.env.SMAP_SIM_OPENAI_MODEL
    if (!model) throw new Error('an OpenAI persona needs persona.model or SMAP_SIM_OPENAI_MODEL')
    return { provider: new OpenAIProvider(requireEnv('OPENAI_API_KEY'), process.env.SMAP_SIM_OPENAI_BASE_URL), model }
  }
  const model = persona.model ?? process.env.SMAP_SIM_ANTHROPIC_MODEL ?? 'claude-haiku-5-5'
  return { provider: new AnthropicProvider(requireEnv('ANTHROPIC_API_KEY'), process.env.SMAP_SIM_ANTHROPIC_BASE_URL), model }
}

interface ActorRuntime {
  actor: Actor
  participant: Participant | null
  persona: PersonaLoop | null
  busy: boolean
  /** The persona's message in progress, which a script step waits out before acting. */
  acting: Promise<void> | null
}

export interface RunOptions {
  headed: boolean
  roomOverride?: string
  facilitatorHuman: boolean
}

export interface RunResult {
  runDir: string
  roomId: string
  steps: StepRecord[]
  aborted: string | null
}

/** Applies `--facilitator human`: the facilitator's steps become printed cues for the operator. */
export function applyFacilitatorOverride(scenario: Scenario, human: boolean): { scenario: Scenario; cues: Step[] } {
  if (!human) return { scenario, cues: [] }
  const fac = scenario.actors.find((a) => a.role === 'facilitator') as Actor
  return {
    scenario: {
      ...scenario,
      actors: scenario.actors.map((a) => (a === fac ? { ...a, drive: 'human' as const } : a)),
      steps: scenario.steps.filter((s) => s.actor !== fac.name),
    },
    cues: scenario.steps.filter((s) => s.actor === fac.name),
  }
}

export async function runScenario(s: Session, original: Scenario, opts: RunOptions): Promise<RunResult & { restored: string }> {
  const { scenario, cues } = applyFacilitatorOverride(original, opts.facilitatorHuman)
  const tag = runTag()
  const runDir = join(s.simRoot, 'runs', `${tag}-${scenario.name}`)
  mkdirSync(join(runDir, 'screens'), { recursive: true })
  s.log.attachEventsFile(join(runDir, 'events.jsonl'))
  s.log.event('run.start', { scenario: scenario.name, target: s.target.name })

  const needsStudents = scenario.actors.some((a) => a.role === 'student')
  const secret = needsStudents ? requireEnv('SMAP_SIM_SECRET') : ''
  s.redactor.add(secret)
  const facilitatorActor = scenario.actors.find((a) => a.role === 'facilitator') as Actor
  const facilitatorPassword = facilitatorActor.drive === 'human' ? null : s.facilitatorPassword

  const { value, restored } = await withAuthLimit(s, async () => {
    const { accounts, groupId } = await provision(
      {
        target: s.target,
        admin: s.admin,
        facilitator: s.facilitator,
        redactor: s.redactor,
        log: s.log,
        secret,
        clientFor: () => new ApiClient(s.target.baseUrl, s.redactor),
      },
      scenario,
    )
    const roomOpts: Parameters<typeof setupRoom>[3] = { groupId, runTag: tag, log: s.log }
    if (opts.roomOverride) roomOpts.roomOverride = opts.roomOverride
    const room = await setupRoom(s.facilitator, s.target, scenario, roomOpts)
    s.log.info(`room ${room.roomId}${room.created ? ' (created)' : ''}; run directory ${runDir}`)
    const browser = await chromium.launch({ headless: !opts.headed })
    try {
      return await play(s, scenario, cues, room, accounts, facilitatorPassword, browser, runDir)
    } finally {
      await browser.close()
    }
  })
  // Written after the limit is restored, so the report states the restored value.
  await writeEvidence(s, { ...value.evidence, restored })
  return { ...value.result, restored }
}

async function play(
  s: Session,
  scenario: Scenario,
  cues: Step[],
  room: RoomSetup,
  accounts: Map<string, SimAccount>,
  facilitatorPassword: string | null,
  browser: Browser,
  runDir: string,
): Promise<{ result: RunResult; evidence: EvidenceInput }> {
  const startedAt = new Date().toISOString()
  const source = facilitatorSource(s.facilitator, room.roomId)
  const observer = new RoomObserver(source, (where, err) => s.log.event('observe.error', { where, error: String(err) }))
  const me = await s.facilitator.get<{ id: string }>('/api/auth/me')
  const budget = new Budget(scenario.budget.maxCalls, scenario.budget.maxOutputTokens)
  const runtimes = new Map<string, ActorRuntime>()
  const records: StepRecord[] = []
  const abort = new AbortController()
  let abortReason: string | null = null

  for (const actor of scenario.actors) {
    let participant: Participant | null = null
    const common = { baseUrl: s.target.baseUrl, roomId: room.roomId, name: actor.name, typingCps: scenario.typingCps, ignoreHttpsErrors: s.target.ignoreHttpsErrors }
    if (actor.drive !== 'human') {
      if (actor.role === 'facilitator') {
        participant = new Participant({ ...common, identity: { kind: 'account', email: s.target.facilitatorEmail, password: facilitatorPassword as string } })
      } else if (actor.role === 'student') {
        const acc = accounts.get(actor.name) as SimAccount
        participant = new Participant({ ...common, identity: { kind: 'account', email: acc.email, password: acc.password } })
      } else {
        if (!room.guestPath) throw new Error(`actor ${actor.name} is a guest but the room has no guest link`)
        participant = new Participant({ ...common, identity: { kind: 'guest', path: room.guestPath, displayName: actor.displayName as string } })
      }
    }
    if (actor.role === 'facilitator') {
      observer.actorBySender.set(me.id, actor.name)
      if (participant) participant.senderId = me.id
    } else if (actor.role === 'student') {
      const acc = accounts.get(actor.name) as SimAccount
      observer.actorBySender.set(acc.userId, actor.name)
      if (participant) participant.senderId = acc.userId
    }
    runtimes.set(actor.name, { actor, participant, persona: null, busy: false, acting: null })
  }

  for (const rt of runtimes.values()) {
    const persona = rt.actor.persona
    if (!persona || !rt.participant || (rt.actor.drive !== 'llm' && rt.actor.drive !== 'hybrid')) continue
    const { provider, model } = makeProvider(persona)
    const p = rt.participant
    rt.persona = new PersonaLoop(persona, provider, model, budget, {
      selfName: rt.actor.displayName ?? rt.actor.name,
      transcript: () => observer.transcript(p.senderId, 40),
      isBusy: () => rt.busy || !p.connected,
      act: (decision, signal) => {
        const work = (async () => {
          if (decision.action === 'say') {
            await p.type(decision.text)
            if (signal.aborted) return p.clearDraft().catch(() => undefined)
            await p.sendTyped(bubbleSnippet(decision.text))
          } else {
            await p.typePause(decision.text, Math.ceil(decision.text.length / 2), 4 + Math.random() * 6, 'send')
          }
          s.log.event('persona.said', { actor: rt.actor.name, action: decision.action })
        })()
        rt.acting = work.finally(() => {
          rt.acting = null
        })
        return rt.acting
      },
      log: (kind, fields) => s.log.event(kind, { actor: rt.actor.name, ...fields }),
    })
  }

  observer.onMessage((m) => {
    for (const rt of runtimes.values()) rt.persona?.onRoomMessage(!!m.sender_id && m.sender_id === rt.participant?.senderId)
  })

  const exec: StepExecutor = {
    begin: (name) => {
      const rt = runtimes.get(name) as ActorRuntime
      rt.busy = true
      rt.persona?.cancel()
    },
    end: (name) => {
      ;(runtimes.get(name) as ActorRuntime).busy = false
    },
    execute: async (step) => {
      const rt = runtimes.get(step.actor) as ActorRuntime
      if (rt.acting) await rt.acting.catch(() => undefined)
      await executeStep(s, step, rt, runtimes, room, browser)
      if (step.do === 'connect' && rt.participant?.senderId) observer.actorBySender.set(rt.participant.senderId, rt.actor.name)
    },
    onFailure: async (step, err) => {
      const rt = runtimes.get(step.actor) as ActorRuntime
      s.log.event('step.error', { actor: step.actor, do: step.do, error: String(err).split('\n')[0] })
      await rt.participant?.screenshot(join(runDir, 'screens', `${step.actor}-${step.do}-${Date.now()}.png`))
    },
  }

  // Actors without a connect step join at the start; a connect step makes a late joiner.
  const byActor = stepsByActor(scenario.steps)
  await Promise.all(
    [...runtimes.values()]
      .filter((rt) => rt.participant && !(byActor.get(rt.actor.name) ?? []).some((x) => x.step.do === 'connect'))
      .map(async (rt, i) => {
        await new Promise((r) => setTimeout(r, i * 400))
        await (rt.participant as Participant).connect(browser)
        if (rt.actor.role === 'guest' && rt.participant?.senderId) observer.actorBySender.set(rt.participant.senderId, rt.actor.name)
        s.log.event('actor.connected', { actor: rt.actor.name })
      }),
  )

  observer.start()
  const t0 = Date.now()
  s.log.event('run.t0', { t0: new Date(t0).toISOString() })
  const quietTimer = setInterval(() => {
    const last = observer.messages.at(-1)?.seenAt || t0
    for (const rt of runtimes.values()) rt.persona?.onQuietCheck(last)
  }, 5000)
  const cueTimers = cues.map((c) =>
    setTimeout(() => s.log.info(`[cue ${formatOffset(c.at ?? 0)}] facilitator: ${c.label ?? JSON.stringify({ ...c, actor: undefined })}`), (c.at ?? 0) * 1000),
  )

  const ctx = {
    t0,
    clock: realClock,
    signal: abort.signal,
    abort: (reason: string) => {
      abortReason = reason
      s.log.event('run.abort', { reason })
      abort.abort()
    },
    satisfied: (cond: Parameters<RoomObserver['satisfied']>[0], armedAt: number) => observer.satisfied(cond, armedAt),
    record: (rec: StepRecord) => {
      records.push(rec)
      s.log.event('step', { ...rec })
      s.log.info(`${formatOffset(rec.endedSec)} ${rec.actor} ${rec.label ?? rec.action}: ${rec.outcome}${rec.error ? ` (${rec.error})` : ''}`)
    },
  }
  await Promise.all([...byActor.entries()].map(([name, steps]) => runActor(name, steps, exec, ctx)))
  await realClock.sleep(t0 + scenario.durationSec * 1000 - Date.now(), abort.signal)

  clearInterval(quietTimer)
  for (const t of cueTimers) clearTimeout(t)
  for (const rt of runtimes.values()) rt.persona?.setEnabled(false)
  await Promise.all([...runtimes.values()].map((rt) => rt.acting?.catch(() => undefined)))
  observer.stop()
  await observer.pollProposals().catch(() => undefined)
  for (const rt of runtimes.values()) await rt.participant?.leave()
  const endedAt = new Date().toISOString()
  s.log.event('run.end', { aborted: abortReason })

  const evidence: EvidenceInput = {
    runDir,
    roomId: room.roomId,
    agentIds: [...room.agentIds.values()],
    from: startedAt,
    to: endedAt,
    steps: records,
    proposals: [...observer.proposals.values()],
    persona: budget.calls > 0 ? { calls: budget.calls, inputTokens: budget.inputTokens, outputTokens: budget.outputTokens } : null,
    scenario: scenario.name,
    notes: scenario.notes,
    aborted: abortReason,
  }
  return { result: { runDir, roomId: room.roomId, steps: records, aborted: abortReason }, evidence }
}

async function executeStep(
  s: Session,
  step: Step,
  rt: ActorRuntime,
  runtimes: Map<string, ActorRuntime>,
  room: RoomSetup,
  browser: Browser,
): Promise<void> {
  const p = rt.participant
  if (!p) throw new Error(`${rt.actor.name} has no browser`)
  switch (step.do) {
    case 'connect':
      return p.connect(browser)
    case 'leave':
      return p.leave()
    case 'say':
      return p.say(step.text)
    case 'typePause': {
      const pausing = p.typePause(step.text, step.typeChars, step.pauseSec, step.then)
      // While this actor pauses, record whether another participant sees the typing line (AC-9).
      const witness = [...runtimes.values()].find((o) => o !== rt && o.participant?.connected)?.participant
      if (witness) {
        const typed = Math.min(step.typeChars ?? step.text.length, step.text.length)
        await new Promise((r) => setTimeout(r, p.typingDurationMs(typed) + 1500))
        s.log.event('typing.observed', { actor: rt.actor.name, witness: witness.name, visible: await witness.typingVisible() })
      }
      return pausing
    }
    case 'silent':
    case 'wait':
      return new Promise((r) => setTimeout(r, step.sec * 1000))
    case 'openActivity':
      return p.openActivity()
    case 'submit':
      return p.submit(step.fields)
    case 'propose':
      return p.propose(step.fields, step.group)
    case 'vote':
      return p.vote(step.approve)
    case 'withdraw':
      return p.withdraw()
    case 'startActivity':
      return p.startActivity(resolveType(room.activityTypes, step.type).name)
    case 'endActivity':
      return p.endActivity()
    case 'releaseObservation': {
      const obs = await s.facilitator.get<Array<{ id: string; released_at: string | null }>>(`/api/chatrooms/${room.roomId}/observations?limit=100`)
      const latest = [...obs].reverse().find((o) => !o.released_at)
      if (!latest) throw new Error('no unreleased observation to release')
      await s.facilitator.post(`/api/chatrooms/${room.roomId}/observations/${latest.id}/release`, { target: 'room' })
      return
    }
    case 'persona':
      if (!rt.persona) throw new Error(`${rt.actor.name} has no persona`)
      rt.persona.setEnabled(step.enabled)
      return
  }
}

export interface EvidenceInput {
  runDir: string
  roomId: string
  agentIds: string[]
  from: string
  to: string
  steps: StepRecord[]
  proposals: Proposal[]
  persona: { calls: number; inputTokens: number; outputTokens: number } | null
  scenario: string
  notes: string[]
  aborted: string | null
  restored?: string
}

export async function writeEvidence(s: Session, e: EvidenceInput): Promise<void> {
  const fac = facilitatorSource(s.facilitator, e.roomId)
  const src = adminAuditSource(s.admin, fac)
  const transcript = await collectTranscript(src, e.from, e.to)
  const audit = await collectWakeAudit(src, e.roomId, e.agentIds, e.from, e.to)
  const agents = await fac.agents()
  const agentNames = new Map(agents.map((a) => [a.agent_id, a.name ?? a.agent_id]))
  writeJson(e.runDir, 'transcript.json', transcript)
  writeJson(e.runDir, 'audit.json', audit)
  writeJson(e.runDir, 'proposals.json', e.proposals)
  const report = buildReport({
    scenario: e.scenario,
    target: s.target.name,
    roomId: e.roomId,
    startedAt: e.from,
    endedAt: e.to,
    steps: e.steps,
    audit,
    agentNames,
    proposals: e.proposals,
    transcriptCount: transcript.length,
    persona: e.persona,
    restoredLimit: e.restored ?? 'not changed by this command',
    notes: e.notes,
    aborted: e.aborted,
  })
  writeFileSync(join(e.runDir, 'report.md'), s.log.redact(report))
  s.log.info(`evidence written to ${e.runDir}`)
}
