// Pure-function route guards — testable in isolation (R24.19).

export interface GuardContext {
  isAuthenticated: boolean
  isVerified: boolean
  isAdmin: boolean
}

export interface RouteMeta {
  requiresAuth?: boolean
  requiresVerifiedEmail?: boolean
  requiredRoles?: string[]
}

export type GuardResult =
  | true
  | { name: string; query?: Record<string, string> }

export function authGuard(
  meta: RouteMeta,
  ctx: GuardContext,
  fullPath: string,
): GuardResult {
  if (meta.requiresAuth && !ctx.isAuthenticated) {
    return { name: 'identity.login', query: { redirect: fullPath } }
  }
  return true
}

export function verifiedEmailGuard(
  meta: RouteMeta,
  ctx: GuardContext,
): GuardResult {
  if (meta.requiresVerifiedEmail && !ctx.isVerified) {
    return { name: 'identity.verifyEmail' }
  }
  return true
}

export function roleGuard(
  meta: RouteMeta,
  ctx: GuardContext,
): GuardResult {
  const roles = meta.requiredRoles
  if (roles?.includes('admin') && !ctx.isAdmin) {
    return { name: 'root' }
  }
  return true
}

export function runGuards(
  meta: RouteMeta,
  ctx: GuardContext,
  fullPath: string,
): GuardResult {
  const checks = [
    authGuard(meta, ctx, fullPath),
    verifiedEmailGuard(meta, ctx),
    roleGuard(meta, ctx),
  ]
  for (const result of checks) {
    if (result !== true) return result
  }
  return true
}
