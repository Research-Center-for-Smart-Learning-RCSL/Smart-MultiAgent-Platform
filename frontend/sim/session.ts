import { join } from 'node:path'
import type { Target } from './config/target.ts'
import { assertRateLimitChangeAllowed } from './config/target.ts'
import { secretFromEnvOrPrompt } from './config/secrets.ts'
import { ApiClient } from './lib/http.ts'
import { Logger, type Redactor } from './lib/log.ts'
import {
  AuthLimitGuard,
  describeOutcome,
  withRaisedAuthLimit,
  type RateLimitAdmin,
  type RateLimitPolicy,
} from './limits/authLimit.ts'
import type { ProvisionDeps } from './setup/provision.ts'

/** Every environment variable whose value must never be printed. */
export const SECRET_ENV = [
  'SMAP_SIM_ADMIN_PASSWORD',
  'SMAP_SIM_FACILITATOR_PASSWORD',
  'SMAP_SIM_SECRET',
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
] as const

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
  const holder = guard.heldByOther()
  if (holder !== null) log.warn(`process ${holder} is running against target ${target.name} and holds the raised AUTH limit`)
  const leftover = await guard.restoreLeftover()
  if (leftover.kind !== 'none') log.warn(`an earlier run left the AUTH limit raised; ${describeOutcome(leftover)}`)
  return { target, redactor, log, admin, facilitator, guard, simRoot, facilitatorPassword }
}

export async function withAuthLimit<T>(s: Session, body: () => Promise<T>): Promise<{ value: T; restored: string }> {
  assertRateLimitChangeAllowed(s.target)
  let restored = 'unknown'
  const value = await withRaisedAuthLimit(s.guard, s.target.name, s.target.authLimitPerMin, body, {
    restored: (o) => {
      restored = describeOutcome(o)
      s.log.info(`AUTH limit ${restored}`)
    },
    failed: (message) => s.log.warn(message),
  })
  return { value, restored }
}

export function provisionDeps(s: Session, secret: string): ProvisionDeps {
  return {
    target: s.target,
    admin: s.admin,
    facilitator: s.facilitator,
    redactor: s.redactor,
    log: s.log,
    secret,
    clientFor: () => new ApiClient(s.target.baseUrl, s.redactor),
  }
}
