import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { Logger, Redactor } from '../lib/log.ts'

describe('Redactor (AC-2)', () => {
  it('removes registered secret values wherever they appear', () => {
    const r = new Redactor()
    r.add('hunter2-password')
    expect(r.redact('login with hunter2-password failed; hunter2-password again')).toBe(
      'login with [redacted] failed; [redacted] again',
    )
  })

  it('ignores empty and very short values, which would shred ordinary text', () => {
    const r = new Redactor()
    r.add('')
    r.add(undefined)
    r.add('abc')
    expect(r.redact('abc def')).toBe('abc def')
  })

  it('removes token and password fields from JSON text', () => {
    const r = new Redactor()
    const out = r.redact('{"access_token":"eyJhbGciOi.x.y","refresh_token":"r1","password":"p","new_password":"q","guest_token":"g","email":"a@b.c"}')
    expect(out).not.toMatch(/eyJhbGciOi|"r1"|"p"|"q"|"g"/)
    expect(out).toContain('"email":"a@b.c"')
  })

  it('removes bearer tokens and fragment tokens', () => {
    const r = new Redactor()
    expect(r.redact('Authorization: Bearer abc.def-ghi')).toBe('Authorization: Bearer [redacted]')
    expect(r.redact('https://x/reset#token=abcdef123&next=1')).toBe('https://x/reset#token=[redacted]&next=1')
  })
})

describe('Logger', () => {
  it('writes redacted JSONL events', () => {
    const dir = mkdtempSync(join(tmpdir(), 'sim-log-'))
    const r = new Redactor()
    r.add('sk-live-1234567890')
    const log = new Logger(r)
    log.attachEventsFile(join(dir, 'events.jsonl'))
    log.event('step', { error: 'provider rejected key sk-live-1234567890' })
    const line = readFileSync(join(dir, 'events.jsonl'), 'utf8')
    expect(line).not.toContain('sk-live-1234567890')
    expect(JSON.parse(line)).toMatchObject({ kind: 'step', error: 'provider rejected key [redacted]' })
  })
})
