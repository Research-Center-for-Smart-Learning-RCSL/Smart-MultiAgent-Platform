import { readFileSync } from 'node:fs'
import { z } from 'zod'

export const AUTH_LIMIT_CEILING = 2000

export const TargetSchema = z.object({
  name: z.string().min(1),
  baseUrl: z.string().url(),
  /** The base URL's host must equal this; a guard against pointing the tool at the wrong deployment. */
  allowedHost: z.string().min(1),
  adminEmail: z.string().email(),
  facilitatorEmail: z.string().email(),
  projectId: z.string().uuid(),
  workspaceId: z.string().uuid(),
  simEmailPrefix: z.string().regex(/^[a-z0-9-]+$/).default('sim'),
  simEmailDomain: z.string().min(3).default('example.com'),
  /** Whether the tool may raise the platform-wide AUTH rate limit for a run (spec §8). */
  allowRateLimitChange: z.boolean().default(false),
  authLimitPerMin: z.number().int().min(10).max(AUTH_LIMIT_CEILING).default(600),
  ignoreHttpsErrors: z.boolean().default(false),
})

export type Target = z.infer<typeof TargetSchema>

export function parseTarget(raw: unknown): Target {
  const target = TargetSchema.parse(raw)
  assertHostAllowed(target)
  return target
}

export function assertHostAllowed(target: Target): void {
  const host = new URL(target.baseUrl).host
  if (host !== target.allowedHost) {
    throw new Error(`target ${target.name}: base URL host ${host} is not the allowed host ${target.allowedHost}`)
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
