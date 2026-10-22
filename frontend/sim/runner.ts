import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium, type Browser } from '@playwright/test'
import { requireEnv, requireSimSecret } from './config/secrets.ts'
import { formatOffset } from './lib/time.ts'
import type { Actor, Persona, Scenario, Step } from './scenario/schema.ts'
import { applyFacilitatorOverride, facilitatorOf } from './scenario/load.ts'
import { provision, type SimAccount } from './setup/provision.ts'
import { resolveType, setupRoom, type RoomSetup } from './setup/room.ts'
import { Participant, bubbleSnippet } from './browser/participant.ts'
import { RoomObserver } from './observe/room.ts'
import { AnthropicProvider, OpenAIProvider, type Provider } from './llm/provider.ts'
import { Budget, PersonaLoop } from './llm/persona.ts'
import { realClock, runActor, stepsByActor, type RunContext, type StepExecutor, type StepRecord } from './scheduler/run.ts'
import { writeEvidence, type EvidenceInput } from './evidence/write.ts'
import { provisionDeps, withAuthLimit, type Session } from './session.ts'
import { roomApi, type RoomApi } from './sources.ts'

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
  /** Known before connecting for accounts; a guest's comes from its browser after connecting. */
  senderId: string | null
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

/** Everything one playthrough needs, fixed before the first participant connects. */
interface Stage {
  s: Session
  scenario: Scenario
  cues: Step[]
  room: RoomSetup
  api: RoomApi
  accounts: Map<string, SimAccount>
  facilitatorPassword: string | null
  browser: Browser
  runDir: string
}

function runTag(d = new Date()): string {
  return d.toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15)
}

export async function runScenario(s: Session, original: Scenario, opts: RunOptions): Promise<RunResult & { restored: string }> {
  const { scenario, cues } = applyFacilitatorOverride(original, opts.facilitatorHuman)
  const tag = runTag()
  const runDir = join(s.simRoot, 'runs', `${tag}-${scenario.name}`)
  mkdirSync(join(runDir, 'screens'), { recursive: true })
  s.log.attachEventsFile(join(runDir, 'events.jsonl'))
  s.log.event('run.start', { scenario: scenario.name, target: s.target.name })

  const secret = scenario.actors.some((a) => a.role === 'student') ? requireSimSecret() : ''
  s.redactor.add(secret)
  const facilitatorPassword = facilitatorOf(scenario).drive === 'human' ? null : s.facilitatorPassword

  const { value, restored } = await withAuthLimit(s, async () => {
    const { accounts, groupId } = await provision(provisionDeps(s, secret), scenario)
    const roomOpts: Parameters<typeof setupRoom>[3] = { groupId, runTag: tag, log: s.log, redactor: s.redactor }
    if (opts.roomOverride) roomOpts.roomOverride = opts.roomOverride
    const room = await setupRoom(s.facilitator, s.target, scenario, roomOpts)
    s.log.info(`room ${room.roomId}${room.created ? ' (created)' : ''}; run directory ${runDir}`)
    // Playwright's own signal handlers would close the browser and exit before the AUTH
    // limit is restored; withRaisedAuthLimit owns SIGINT/SIGTERM instead.
    const browser = await chromium.launch({ headless: !opts.headed, handleSIGINT: false, handleSIGTERM: false, handleSIGHUP: false })
    try {
      const api = roomApi(s.facilitator, room.roomId)
      return await play({ s, scenario, cues, room, api, accounts, facilitatorPassword, browser, runDir })
    } finally {
      await browser.close()
    }
  })
  // Written after the limit is restored, so the report states the restored value.
  await writeEvidence(s, { ...value.evidence, restored })
  return { ...value.result, restored }
}

function buildRuntimes(st: Stage, facilitatorId: string): Map<string, ActorRuntime> {
  const { s, scenario, room, accounts } = st
  const runtimes = new Map<string, ActorRuntime>()
  for (const actor of scenario.actors) {
    const common = { baseUrl: s.target.baseUrl, roomId: room.roomId, name: actor.name, typingCps: scenario.typingCps, ignoreHttpsErrors: s.target.ignoreHttpsErrors }
    const account = actor.role === 'student' ? (accounts.get(actor.name) as SimAccount) : null
    const senderId = actor.role === 'facilitator' ? facilitatorId : (account?.userId ?? null)
    let participant: Participant | null = null
    if (actor.drive !== 'human') {
      if (actor.role === 'facilitator') {
        participant = new Participant({ ...common, identity: { kind: 'account', email: s.target.facilitatorEmail, password: st.facilitatorPassword as string } })
      } else if (account) {
        participant = new Participant({ ...common, identity: { kind: 'account', email: account.email, password: account.password } })
      } else {
        if (!room.guestPath) throw new Error(`actor ${actor.name} is a guest but the room has no guest link`)
        participant = new Participant({ ...common, identity: { kind: 'guest', path: room.guestPath, displayName: actor.displayName as string } })
      }
      participant.senderId = senderId
    }
    runtimes.set(actor.name, { actor, participant, senderId, persona: null, busy: false, acting: null })
  }
  return runtimes
}

function wirePersonas(st: Stage, runtimes: Map<string, ActorRuntime>, observer: RoomObserver, budget: Budget): void {
  for (const rt of runtimes.values()) {
    const persona = rt.actor.persona
    const p = rt.participant
    if (!persona || !p || (rt.actor.drive !== 'llm' && rt.actor.drive !== 'hybrid')) continue
    const { provider, model } = makeProvider(persona)
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
          st.s.log.event('persona.said', { actor: rt.actor.name, action: decision.action })
        })()
        rt.acting = work.finally(() => {
          rt.acting = null
        })
        return rt.acting
      },
      log: (kind, fields) => st.s.log.event(kind, { actor: rt.actor.name, ...fields }),
    })
  }
  observer.onMessage((m) => {
    for (const rt of runtimes.values()) rt.persona?.onRoomMessage(!!m.sender_id && m.sender_id === rt.participant?.senderId)
  })
}

async function play(st: Stage): Promise<{ result: RunResult; evidence: EvidenceInput }> {
  const { s, scenario, room } = st
  const startedAt = new Date().toISOString()
  const observer = new RoomObserver(st.api, (where, err) => s.log.event('observe.error', { where, error: String(err) }))
  const me = await s.facilitator.get<{ id: string }>('/api/auth/me')
  const budget = new Budget(scenario.budget.maxCalls, scenario.budget.maxOutputTokens)
  const runtimes = buildRuntimes(st, me.id)
  const registerActor = (rt: ActorRuntime): void => {
    const id = rt.participant?.senderId ?? rt.senderId
    if (id) observer.actorBySender.set(id, rt.actor.name)
  }
  for (const rt of runtimes.values()) registerActor(rt)
  wirePersonas(st, runtimes, observer, budget)

  const records: StepRecord[] = []
  const abort = new AbortController()
  let abortReason: string | null = null
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
      await executeStep(st, step, rt, runtimes, abort.signal)
      if (step.do === 'connect') registerActor(rt)
    },
    onFailure: async (step, err) => {
      const rt = runtimes.get(step.actor) as ActorRuntime
      s.log.event('step.error', { actor: step.actor, do: step.do, error: String(err).split('\n')[0] })
      await rt.participant?.screenshot(join(st.runDir, 'screens', `${step.actor}-${step.do}-${Date.now()}.png`))
    },
  }

  // Actors without a connect step join at the start; a connect step makes a late joiner.
  const byActor = stepsByActor(scenario.steps)
  await Promise.all(
    [...runtimes.values()]
      .filter((rt) => rt.participant && !(byActor.get(rt.actor.name) ?? []).some((x) => x.step.do === 'connect'))
      .map(async (rt, i) => {
        await new Promise((r) => setTimeout(r, i * 400))
        await (rt.participant as Participant).connect(st.browser)
        registerActor(rt)
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
  const cueTimers = st.cues.map((c) =>
    setTimeout(() => s.log.info(`[cue ${formatOffset(c.at ?? 0)}] facilitator: ${c.label ?? JSON.stringify({ ...c, actor: undefined })}`), (c.at ?? 0) * 1000),
  )
  const stop = (reason: string): void => {
    abortReason ??= reason
    s.log.event('run.abort', { reason })
    abort.abort()
  }
  const ctx: RunContext = {
    t0,
    clock: realClock,
    signal: abort.signal,
    abort: stop,
    satisfied: (cond, armedAt) => observer.satisfied(cond, armedAt),
    record: (rec) => {
      records.push(rec)
      s.log.event('step', { ...rec })
      s.log.info(`${formatOffset(rec.endedSec)} ${rec.actor} ${rec.label ?? rec.action}: ${rec.outcome}${rec.error ? ` (${rec.error})` : ''}`)
    },
  }

  try {
    await Promise.all([...byActor.entries()].map(([name, steps]) => runActor(name, steps, exec, ctx)))
    await realClock.sleep(t0 + scenario.durationSec * 1000 - Date.now(), abort.signal)
  } catch (err) {
    // Past t0 the run has produced evidence worth keeping, so an error ends it rather than discarding it.
    stop(`run error: ${err instanceof Error ? err.message.split('\n')[0] : String(err)}`)
  } finally {
    abort.abort()
    clearInterval(quietTimer)
    for (const t of cueTimers) clearTimeout(t)
    for (const rt of runtimes.values()) rt.persona?.setEnabled(false)
    await Promise.all([...runtimes.values()].map((rt) => rt.acting?.catch(() => undefined)))
    observer.stop()
    await observer.pollProposals().catch((err: unknown) => s.log.event('observe.error', { where: 'final proposals', error: String(err) }))
    for (const rt of runtimes.values()) {
      // What each person saw at the end: presence list, badges, the last messages.
      if (rt.participant?.connected) {
        await rt.participant.showPeople().catch(() => undefined)
        await rt.participant.screenshot(join(st.runDir, 'screens', `end-${rt.actor.name}.png`))
      }
      await rt.participant?.leave().catch(() => undefined)
    }
  }
  const endedAt = new Date().toISOString()
  s.log.event('run.end', { aborted: abortReason })

  const evidence: EvidenceInput = {
    runDir: st.runDir,
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
  return { result: { runDir: st.runDir, roomId: room.roomId, steps: records, aborted: abortReason }, evidence }
}

/** While an actor pauses mid-draft, records whether another participant sees the typing line (AC-9). */
async function typePauseWitnessed(st: Stage, step: Extract<Step, { do: 'typePause' }>, rt: ActorRuntime, runtimes: Map<string, ActorRuntime>, signal: AbortSignal): Promise<void> {
  const p = rt.participant as Participant
  const pausing = p.typePause(step.text, step.typeChars, step.pauseSec, step.then)
  // Settled at once so a rejection is never unhandled while the witness runs.
  const outcome = pausing.then(
    () => null,
    (err: unknown) => err,
  )
  const witness = [...runtimes.values()].find((o) => o !== rt && o.participant?.connected)?.participant
  if (witness) {
    try {
      const typed = Math.min(step.typeChars ?? step.text.length, step.text.length)
      await realClock.sleep(p.typingDurationMs(typed) + 1500, signal)
      st.s.log.event('typing.observed', { actor: rt.actor.name, witness: witness.name, visible: await witness.typingVisible() })
    } catch (err) {
      st.s.log.event('typing.observed', { actor: rt.actor.name, witness: witness.name, error: String(err).split('\n')[0] })
    }
  }
  const err = await outcome
  if (err) throw err
}

async function executeStep(st: Stage, step: Step, rt: ActorRuntime, runtimes: Map<string, ActorRuntime>, signal: AbortSignal): Promise<void> {
  const p = rt.participant
  if (!p) throw new Error(`${rt.actor.name} has no browser`)
  switch (step.do) {
    case 'connect':
      return p.connect(st.browser)
    case 'leave':
      return p.leave()
    case 'say':
      return p.say(step.text)
    case 'typePause':
      return typePauseWitnessed(st, step, rt, runtimes, signal)
    case 'silent':
    case 'wait':
      return realClock.sleep(step.sec * 1000, signal)
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
      return p.startActivity(resolveType(st.room.activityTypes, step.type).name)
    case 'endActivity':
      return p.endActivity()
    case 'releaseObservation':
      return st.api.releaseLatestObservation()
    case 'persona':
      if (!rt.persona) throw new Error(`${rt.actor.name} has no persona`)
      rt.persona.setEnabled(step.enabled)
      return
    default: {
      const unhandled: never = step
      throw new Error(`no executor for step ${JSON.stringify(unhandled)}`)
    }
  }
}
