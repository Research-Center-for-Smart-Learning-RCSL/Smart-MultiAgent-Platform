import { describe, expect, it } from 'vitest'
import {
  authGuard,
  verifiedEmailGuard,
  roleGuard,
  runGuards,
  type GuardContext,
  type RouteMeta,
} from '../guards'

const authed: GuardContext = { isAuthenticated: true, isVerified: true, isAdmin: false }
const unauthed: GuardContext = { isAuthenticated: false, isVerified: false, isAdmin: false }
const admin: GuardContext = { isAuthenticated: true, isVerified: true, isAdmin: true }
const unverified: GuardContext = { isAuthenticated: true, isVerified: false, isAdmin: false }

describe('authGuard', () => {
  it('passes when no requiresAuth', () => {
    expect(authGuard({}, unauthed, '/')).toBe(true)
  })

  it('passes when authenticated', () => {
    expect(authGuard({ requiresAuth: true }, authed, '/x')).toBe(true)
  })

  it('redirects to login when unauthenticated', () => {
    const result = authGuard({ requiresAuth: true }, unauthed, '/dashboard')
    expect(result).toEqual({
      name: 'identity.login',
      query: { redirect: '/dashboard' },
    })
  })
})

describe('verifiedEmailGuard', () => {
  it('passes when no requiresVerifiedEmail', () => {
    expect(verifiedEmailGuard({}, unverified)).toBe(true)
  })

  it('passes when verified', () => {
    expect(verifiedEmailGuard({ requiresVerifiedEmail: true }, authed)).toBe(true)
  })

  it('redirects to verifyEmail when unverified', () => {
    expect(verifiedEmailGuard({ requiresVerifiedEmail: true }, unverified)).toEqual({
      name: 'identity.verifyEmail',
    })
  })
})

describe('roleGuard', () => {
  it('passes when no requiredRoles', () => {
    expect(roleGuard({}, authed)).toBe(true)
  })

  it('passes when admin for admin-required route', () => {
    expect(roleGuard({ requiredRoles: ['admin'] }, admin)).toBe(true)
  })

  it('redirects to root for non-admin', () => {
    expect(roleGuard({ requiredRoles: ['admin'] }, authed)).toEqual({
      name: 'root',
    })
  })
})

describe('runGuards', () => {
  it('runs all guards and returns first failure', () => {
    const meta: RouteMeta = {
      requiresAuth: true,
      requiresVerifiedEmail: true,
      requiredRoles: ['admin'],
    }
    const result = runGuards(meta, unauthed, '/admin')
    expect(result).toEqual({
      name: 'identity.login',
      query: { redirect: '/admin' },
    })
  })

  it('passes when all guards pass', () => {
    const meta: RouteMeta = {
      requiresAuth: true,
      requiresVerifiedEmail: true,
      requiredRoles: ['admin'],
    }
    expect(runGuards(meta, admin, '/admin')).toBe(true)
  })
})
