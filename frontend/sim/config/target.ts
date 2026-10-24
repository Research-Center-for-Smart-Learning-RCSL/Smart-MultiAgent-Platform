import { readFileSync } from 'node:fs'
import { z } from 'zod'

export const AUTH_LIMIT_CEILING = 2000

/**
 * Domains nobody can receive mail for (RFC 2606/6761). A simulated account's password is
 * derived, not secret from its mailbox owner: whoever reads `sim-1@<domain>` can reset it.
 */
const RESERVED_DOMAIN = /(^|\.)(example\.(com|net|org)|test|invalid|example|localhost)$/i

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])

export const TargetSchema = z.object({
  name: z.string().regex(/^[a-z0-9-]+$/),
  baseUrl: z.string().url(),
  /** The base URL's host must equal this; a guard against pointing the tool at the wrong deployment. */
  allowedHost: z.string().min(1),
  adminEmail: z.string().email(),
  facilitatorEmail: z.string().email(),
  projectId: z.guid(),
  workspaceId: z.guid(),
  simEmailPrefix: z.string().regex(/^[a-z0-9-]+$/).default('sim'),
  simEmailDomain: z.string().min(3).default('example.com'),
  /** Allows a non-reserved simEmailDomain; set only when the operator controls every mailbox in it. */
  simDomainIsOperatorControlled: z.boolean().default(false),
  /** Whether the tool may raise the platform-wide AUTH rate limit for a run (spec §8). */
  allowRateLimitChange: z.boolean().default(false),
  authLimitPerMin: z.number().int().min(10).max(AUTH_LIMIT_CEILING).default(600),
  ignoreHttpsErrors: z.boolean().default(false),
})

export type Target = z.infer<typeof TargetSchema>

export function parseTarget(raw: unknown): Target {
  const target = TargetSchema.parse(raw)
  assertHostAllowed(target)
  assertSafeEmailDomain(target)
  return target
}

export function assertHostAllowed(target: Target): void {
  const url = new URL(target.baseUrl)
  if (url.host !== target.allowedHost) {
    throw new Error(`target ${target.name}: base URL host ${url.host} is not the allowed host ${target.allowedHost}`)
  }
  if (url.protocol !== 'https:' && !LOCAL_HOSTS.has(url.hostname)) {
    throw new Error(`target ${target.name}: a non-local base URL must use https, since passwords are sent to it`)
  }
}

export function assertSafeEmailDomain(target: Target): void {
  if (!RESERVED_DOMAIN.test(target.simEmailDomain) && !target.simDomainIsOperatorControlled) {
    throw new Error(
      `target ${target.name}: simEmailDomain ${target.simEmailDomain} is a real domain, so its mailbox owners could reset ` +
        'the simulated accounts. Use a reserved domain (example.com, *.test), or set simDomainIsOperatorControlled: true ' +
        'if every mailbox in it is yours.',
    )
  }
}

export function assertRateLimitChangeAllowed(target: Target): void {
  if (!target.allowRateLimitChange) {
    throw new Error(
      `target ${target.name} does not set allowRateLimitChange: true, so the AUTH limit cannot be raised. ` +
        'Set it in the target file only for a deployment where a temporarily raised login limit is acceptable.',
    )
  }
}

export function loadTarget(path: string): Target {
  return parseTarget(JSON.parse(readFileSync(path, 'utf8')))
}

export function simEmail(target: Target, n: number): string {
  return `${target.simEmailPrefix}-${n}@${target.simEmailDomain}`
}
