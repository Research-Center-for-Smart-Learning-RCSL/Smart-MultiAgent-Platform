import { z } from 'zod'

/** `90` or `"1:30"` both mean ninety seconds from the run start. */
const Seconds = z
  .union([z.number().nonnegative(), z.string().regex(/^\d+:\d{2}$/)])
  .transform((v) => {
    if (typeof v === 'number') return v
    const [m, s] = v.split(':').map(Number)
    return (m ?? 0) * 60 + (s ?? 0)
  })

const FieldValue = z.union([z.string(), z.number(), z.boolean(), z.array(z.string())])

export const ConditionSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('message'),
    /** An actor name, `agent`, `agent:<name>`, `human`, `system` or `anyone` (which excludes system). */
    from: z.string().default('anyone'),
    /** A regular expression the message text must match. */
    match: z
      .string()
      .refine((s) => {
        try {
          new RegExp(s)
          return true
        } catch {
          return false
        }
      }, 'match is not a valid regular expression')
      .optional(),
    /** Only messages created after the step was armed count. */
    sinceArmed: z.boolean().default(true),
    /** How many matching messages must exist; the second `ended` notice marks round 2's end. */
    count: z.number().int().min(1).optional(),
  }),
  z.object({ type: z.literal('activation'), state: z.enum(['active', 'ended']) }),
  z.object({
    type: z.literal('proposal'),
    status: z.enum(['open', 'accepted', 'rejected', 'withdrawn', 'expired']),
  }),
])

const ActionSchema = z.discriminatedUnion('do', [
  z.object({ do: z.literal('connect') }),
  z.object({ do: z.literal('leave') }),
  z.object({ do: z.literal('say'), text: z.string().min(1).max(4000) }),
  z.object({
    do: z.literal('typePause'),
    text: z.string().min(1).max(4000),
    /** How many characters to type before pausing; the whole text when omitted. */
    typeChars: z.number().int().positive().optional(),
    pauseSec: z.number().positive(),
    then: z.enum(['send', 'clear', 'keep']).default('send'),
  }),
  z.object({ do: z.literal('silent'), sec: z.number().positive() }),
  z.object({ do: z.literal('wait'), sec: z.number().positive() }),
  z.object({ do: z.literal('openActivity') }),
  z.object({ do: z.literal('submit'), fields: z.record(z.string(), FieldValue) }),
  z.object({ do: z.literal('propose'), fields: z.record(z.string(), FieldValue), group: z.string().optional() }),
  z.object({ do: z.literal('vote'), approve: z.boolean() }),
  z.object({ do: z.literal('withdraw') }),
  /** By activity type key or name. */
  z.object({ do: z.literal('startActivity'), type: z.string().min(1) }),
  z.object({ do: z.literal('endActivity') }),
  z.object({ do: z.literal('releaseObservation'), target: z.literal('room').default('room') }),
  z.object({ do: z.literal('persona'), enabled: z.boolean() }),
])

export const StepSchema = z.intersection(
  ActionSchema,
  z.object({
    actor: z.string().min(1),
    at: Seconds.optional(),
    when: ConditionSchema.optional(),
    timeoutSec: z.number().positive().default(300),
    onTimeout: z.enum(['skip', 'abort']).default('skip'),
    label: z.string().optional(),
  }),
)

export const PersonaSchema = z.object({
  provider: z.enum(['openai', 'anthropic']),
  /** Falls back to SMAP_SIM_OPENAI_MODEL / SMAP_SIM_ANTHROPIC_MODEL. */
  model: z.string().optional(),
  prompt: z.string().min(1),
  language: z.string().default('zh-TW'),
  minIntervalSec: z.number().nonnegative().default(20),
  maxTurns: z.number().int().positive().default(20),
  maxChars: z.number().int().positive().max(1000).default(120),
  readDelaySec: z.tuple([z.number().nonnegative(), z.number().nonnegative()]).default([3, 8]),
  /** Consider speaking after this long with no room message; never when omitted. */
  speakOnQuietSec: z.number().positive().optional(),
})

export const ActorSchema = z
  .object({
    name: z.string().regex(/^[A-Za-z][A-Za-z0-9_-]*$/),
    role: z.enum(['facilitator', 'student', 'guest']),
    drive: z.enum(['script', 'llm', 'hybrid', 'human']).default('script'),
    /** Simulated account number for a student: `<prefix>-<n>@<domain>`. */
    account: z.number().int().positive().optional(),
    /** A guest's entered name; a student's account display name on creation. */
    displayName: z.string().min(1).max(50).optional(),
    persona: PersonaSchema.optional(),
  })
  .superRefine((a, ctx) => {
    if ((a.drive === 'llm' || a.drive === 'hybrid') && !a.persona) {
      ctx.addIssue({ code: 'custom', message: `actor ${a.name}: drive ${a.drive} needs a persona` })
    }
    if (a.role === 'student' && a.account === undefined) {
      ctx.addIssue({ code: 'custom', message: `actor ${a.name}: a student needs an account number` })
    }
    if (a.role === 'guest' && !a.displayName) {
      ctx.addIssue({ code: 'custom', message: `actor ${a.name}: a guest needs a displayName` })
    }
  })

const AgentBindingSchema = z.object({
  name: z.string().min(1),
  role: z.enum(['normal', 'observer']).default('normal'),
  /** Activity type keys (or names) this agent may start and end. */
  grantActivityTypes: z.array(z.string()).default([]),
})

/** The room's class-start setting (phase 4); the class starts on the first of the two. */
const ClassStartSchema = z.object({
  minParticipants: z.number().int().min(1).max(100),
  maxWaitMinutes: z.number().int().min(1).max(60),
})

/** Per activity type key: minutes, or null for an explicit "no limit". Absent types use the type default. */
const TimeLimitsSchema = z.record(z.string(), z.number().int().min(1).max(180).nullable()).default({})

export const RoomSchema = z.discriminatedUnion('mode', [
  z.object({
    mode: z.literal('create'),
    /** `{ts}` is replaced with the run timestamp. */
    name: z.string().min(1).max(150),
    access: z.enum(['memberGroups', 'projectMembers']).default('projectMembers'),
    guestLinks: z.boolean().default(false),
    agents: z.array(AgentBindingSchema).default([]),
    classStart: ClassStartSchema.optional(),
    timeLimits: TimeLimitsSchema,
  }),
  z.object({
    mode: z.literal('existing'),
    /** May be supplied with `--room` instead. */
    id: z.guid().optional(),
    agents: z.array(AgentBindingSchema).default([]),
    guestLinks: z.boolean().default(false),
    classStart: ClassStartSchema.optional(),
    timeLimits: TimeLimitsSchema,
  }),
])

export const ScenarioSchema = z
  .object({
    name: z.string().regex(/^[a-z0-9-]+$/),
    description: z.string().default(''),
    durationSec: z.number().positive(),
    room: RoomSchema,
    memberGroup: z.object({ name: z.string().min(1).max(200), members: z.array(z.string()) }).optional(),
    actors: z.array(ActorSchema).min(1),
    steps: z.array(StepSchema),
    budget: z
      .object({ maxCalls: z.number().int().positive().default(200), maxOutputTokens: z.number().int().positive().default(60_000) })
      .prefault({}),
    typingCps: z.number().positive().default(6),
    /** Optional notes printed in the report, such as the protocol's pass criteria. */
    notes: z.array(z.string()).default([]),
  })
  .superRefine((s, ctx) => {
    const names = new Set<string>()
    for (const a of s.actors) {
      if (names.has(a.name)) ctx.addIssue({ code: 'custom', message: `duplicate actor ${a.name}` })
      names.add(a.name)
    }
    const facilitators = s.actors.filter((a) => a.role === 'facilitator')
    if (facilitators.length !== 1) ctx.addIssue({ code: 'custom', message: 'exactly one facilitator actor is required' })
    const accounts = s.actors.flatMap((a) => (a.account === undefined ? [] : [a.account]))
    if (new Set(accounts).size !== accounts.length) ctx.addIssue({ code: 'custom', message: 'account numbers must be unique' })
    const byName = new Map(s.actors.map((a) => [a.name, a]))
    for (const [i, step] of s.steps.entries()) {
      const actor = byName.get(step.actor)
      if (!actor) {
        ctx.addIssue({ code: 'custom', message: `step ${i}: unknown actor ${step.actor}` })
        continue
      }
      if (actor.drive === 'human') ctx.addIssue({ code: 'custom', message: `step ${i}: actor ${actor.name} is human and takes no steps` })
      if (step.at === undefined && step.when === undefined) {
        ctx.addIssue({ code: 'custom', message: `step ${i}: needs at, when, or both` })
      }
      const facilitatorOnly = ['startActivity', 'endActivity', 'releaseObservation']
      if (facilitatorOnly.includes(step.do) && actor.role !== 'facilitator') {
        ctx.addIssue({ code: 'custom', message: `step ${i}: ${step.do} is a facilitator action` })
      }
      if ((step.do === 'vote' || step.do === 'propose' || step.do === 'withdraw') && actor.role !== 'student') {
        ctx.addIssue({ code: 'custom', message: `step ${i}: ${step.do} needs a registered student (guests cannot join a group)` })
      }
      if (step.do === 'persona' && actor.drive !== 'hybrid' && actor.drive !== 'llm') {
        ctx.addIssue({ code: 'custom', message: `step ${i}: actor ${actor.name} has no persona` })
      }
    }
    for (const m of s.memberGroup?.members ?? []) {
      const a = byName.get(m)
      if (!a || a.role !== 'student') ctx.addIssue({ code: 'custom', message: `member group lists ${m}, which is not a student actor` })
    }
  })

export type ScenarioInput = z.input<typeof ScenarioSchema>
export type Scenario = z.output<typeof ScenarioSchema>
export type Actor = Scenario['actors'][number]
export type Step = Scenario['steps'][number]
export type Condition = z.output<typeof ConditionSchema>
export type Persona = z.output<typeof PersonaSchema>

/** Identity function that gives a scenario file type checking and completion. */
export function defineScenario(input: ScenarioInput): ScenarioInput {
  return input
}
