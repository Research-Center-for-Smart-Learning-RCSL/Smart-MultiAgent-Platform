import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { loadTarget } from './config/target.ts'
import { requireSimSecret } from './config/secrets.ts'
import { loadScenario, scheduleLines } from './scenario/load.ts'
import { provision } from './setup/provision.ts'
import { Redactor } from './lib/log.ts'
import { describeOutcome } from './limits/authLimit.ts'
import { SECRET_ENV, openSession, provisionDeps, withAuthLimit } from './session.ts'
import { roomApi } from './sources.ts'
import { runScenario } from './runner.ts'
import { writeEvidence } from './evidence/write.ts'

const SIM_ROOT = dirname(fileURLToPath(import.meta.url))
const redactor = new Redactor()
for (const name of SECRET_ENV) redactor.add(process.env[name])

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const USAGE = `Classroom simulator (docs/tasks/2026-10-22-classroom-simulator/spec.md, frontend/sim/README.md)

  pnpm sim dry-run --scenario <file>
  pnpm sim provision --target <name> --scenario <file>
  pnpm sim run --target <name> --scenario <file> [--room <id>] [--headed] [--facilitator human]
  pnpm sim collect --target <name> --room <id> --from <iso> --to <iso>
  pnpm sim restore-limits --target <name>

A target is frontend/sim/targets/<name>.json. Exit codes: 0 done, 1 a step or check failed, 2 error.`

function fail(err: unknown): never {
  process.stderr.write(redactor.redact(`[sim] error: ${err instanceof Error ? err.message : String(err)}`) + '\n')
  process.exit(2)
}

// A stray library rejection would otherwise print natively, unredacted.
process.on('unhandledRejection', fail)
process.on('uncaughtException', fail)

function targetPath(name: string): string {
  if (!/^[a-z0-9-]+$/.test(name)) throw new Error('--target must be a target file name like "local"')
  return join(SIM_ROOT, 'targets', `${name}.json`)
}

function roomId(value: string | undefined): string | undefined {
  if (value !== undefined && !UUID.test(value)) throw new Error('--room must be a room id (UUID)')
  return value
}

async function main(): Promise<number> {
  const { positionals, values } = parseArgs({
    allowPositionals: true,
    options: {
      target: { type: 'string' },
      scenario: { type: 'string' },
      room: { type: 'string' },
      from: { type: 'string' },
      to: { type: 'string' },
      headed: { type: 'boolean', default: false },
      facilitator: { type: 'string' },
      help: { type: 'boolean', default: false },
    },
  })
  const command = positionals[0]
  if (!command || values.help) {
    process.stdout.write(USAGE + '\n')
    return command ? 0 : 2
  }
  const room = roomId(values.room)

  if (command === 'dry-run') {
    if (!values.scenario) throw new Error('--scenario is required')
    const scenario = await loadScenario(values.scenario)
    process.stdout.write(`${scenario.name}: ${scenario.description}\n`)
    process.stdout.write(`Room: ${JSON.stringify(scenario.room)}\n`)
    if (scenario.memberGroup) process.stdout.write(`Member group: ${JSON.stringify(scenario.memberGroup)}\n`)
    for (const a of scenario.actors) {
      process.stdout.write(`Actor ${a.name}: ${a.role}, ${a.drive}${a.account ? `, account ${a.account}` : ''}${a.displayName ? `, "${a.displayName}"` : ''}\n`)
    }
    process.stdout.write(`\n${scheduleLines(scenario).join('\n')}\n`)
    return 0
  }

  if (!values.target) throw new Error('--target is required; there is no default target')
  const target = loadTarget(targetPath(values.target))
  const session = await openSession(target, SIM_ROOT, redactor)

  switch (command) {
    case 'restore-limits': {
      session.log.info(`AUTH limit ${describeOutcome(await session.guard.restore())}`)
      return 0
    }
    case 'provision': {
      if (!values.scenario) throw new Error('--scenario is required')
      const scenario = await loadScenario(values.scenario)
      const secret = requireSimSecret()
      session.redactor.add(secret)
      const { value } = await withAuthLimit(session, () => provision(provisionDeps(session, secret), scenario))
      for (const [actor, acc] of value.accounts) session.log.info(`${actor}: ${acc.email} (${acc.userId})`)
      if (value.groupId) session.log.info(`member group ${value.groupId}`)
      return 0
    }
    case 'run': {
      if (!values.scenario) throw new Error('--scenario is required')
      if (values.facilitator && values.facilitator !== 'human') throw new Error('--facilitator only accepts "human"')
      const scenario = await loadScenario(values.scenario)
      const opts: Parameters<typeof runScenario>[2] = { headed: values.headed, facilitatorHuman: values.facilitator === 'human' }
      if (room) opts.roomOverride = room
      const result = await runScenario(session, scenario, opts)
      const failed = result.steps.filter((r) => r.outcome !== 'ok').length
      session.log.info(`run finished: ${result.steps.length} steps, ${failed} not ok${result.aborted ? `, aborted: ${result.aborted}` : ''}`)
      session.log.info(`evidence: ${result.runDir}`)
      return failed > 0 || result.aborted ? 1 : 0
    }
    case 'collect': {
      if (!room || !values.from || !values.to) throw new Error('collect needs --room, --from and --to')
      const runDir = join(SIM_ROOT, 'runs', `collect-${room.slice(0, 8)}-${Date.now()}`)
      mkdirSync(runDir, { recursive: true })
      const agents = await roomApi(session.facilitator, room).agents()
      await writeEvidence(session, {
        runDir,
        roomId: room,
        agentIds: agents.map((a) => a.agent_id),
        from: new Date(values.from).toISOString(),
        to: new Date(values.to).toISOString(),
        steps: [],
        proposals: [],
        persona: null,
        scenario: 'collect',
        notes: [],
        aborted: null,
      })
      return 0
    }
    default:
      process.stderr.write(`unknown command ${command}\n\n${USAGE}\n`)
      return 2
  }
}

main().then((code) => process.exit(code), fail)
