import { describe, expect, it } from 'vitest'
import { assertRateLimitChangeAllowed, parseTarget, simEmail } from '../config/target.ts'
import { derivePassword } from '../config/secrets.ts'

const base = {
  name: 'local',
  baseUrl: 'http://localhost:5173',
  allowedHost: 'localhost:5173',
  adminEmail: 'admin@example.com',
  facilitatorEmail: 'teacher@example.com',
  projectId: '11111111-1111-4111-8111-111111111111',
  workspaceId: '22222222-2222-4222-8222-222222222222',
}

describe('target guards (AC-3)', () => {
  it('accepts a target whose base URL host is the allowed host', () => {
    const t = parseTarget(base)
    expect(t.allowRateLimitChange).toBe(false)
    expect(t.authLimitPerMin).toBe(600)
  })

  it('refuses a base URL whose host differs from allowedHost', () => {
    expect(() => parseTarget({ ...base, baseUrl: 'https://smap.example.org' })).toThrow(/not the allowed host/)
  })

  it('treats a different port as a different host', () => {
    expect(() => parseTarget({ ...base, baseUrl: 'http://localhost:8080' })).toThrow(/not the allowed host/)
  })

  it('requires https for a non-local host', () => {
    expect(() => parseTarget({ ...base, baseUrl: 'http://smap.example.org', allowedHost: 'smap.example.org' })).toThrow(/https/)
    expect(() => parseTarget({ ...base, baseUrl: 'https://smap.example.org', allowedHost: 'smap.example.org' })).not.toThrow()
    expect(() => parseTarget({ ...base, baseUrl: 'http://127.0.0.1:5173', allowedHost: '127.0.0.1:5173' })).not.toThrow()
  })

  it('refuses a real email domain unless the operator controls it', () => {
    expect(() => parseTarget({ ...base, simEmailDomain: 'school.edu.tw' })).toThrow(/real domain/)
    expect(() => parseTarget({ ...base, simEmailDomain: 'school.edu.tw', simDomainIsOperatorControlled: true })).not.toThrow()
    for (const d of ['example.com', 'example.org', 'sim.test', 'classroom.invalid']) {
      expect(() => parseTarget({ ...base, simEmailDomain: d }), d).not.toThrow()
    }
    expect(() => parseTarget({ ...base, simEmailDomain: 'notexample.com' })).toThrow(/real domain/)
  })

  it('accepts only a file-safe target name', () => {
    expect(() => parseTarget({ ...base, name: '../etc' })).toThrow()
  })

  it('refuses to raise the AUTH limit unless the target opts in', () => {
    expect(() => assertRateLimitChangeAllowed(parseTarget(base))).toThrow(/allowRateLimitChange/)
    expect(() => assertRateLimitChangeAllowed(parseTarget({ ...base, allowRateLimitChange: true }))).not.toThrow()
  })

  it('caps the raised limit', () => {
    expect(() => parseTarget({ ...base, authLimitPerMin: 5000 })).toThrow()
  })

  it('builds simulated emails from prefix and domain', () => {
    expect(simEmail(parseTarget({ ...base, simEmailPrefix: 'cls', simEmailDomain: 'school.test' }), 3)).toBe('cls-3@school.test')
  })
})

describe('derivePassword', () => {
  it('is stable for one secret and email, case-insensitive on the email', () => {
    expect(derivePassword('s3cret-value', 'sim-1@example.com')).toBe(derivePassword('s3cret-value', 'SIM-1@example.com'))
  })

  it('differs per email and per secret', () => {
    const a = derivePassword('s3cret-value', 'sim-1@example.com')
    expect(derivePassword('s3cret-value', 'sim-2@example.com')).not.toBe(a)
    expect(derivePassword('other-secret', 'sim-1@example.com')).not.toBe(a)
  })

  it('meets the password policy: length, letter, digit, symbol', () => {
    const p = derivePassword('s3cret-value', 'sim-1@example.com')
    expect(p.length).toBeGreaterThanOrEqual(10)
    expect(p).toMatch(/[A-Za-z]/)
    expect(p).toMatch(/\d/)
    expect(p).toMatch(/[^A-Za-z0-9]/)
  })
})
