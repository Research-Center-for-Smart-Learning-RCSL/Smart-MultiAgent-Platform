import { describe, expect, it } from 'vitest'
import { buildReport, collectTranscript, collectWakeAudit, type AuditRow, type AuditSource, type TranscriptSource } from '../evidence/collect.ts'
import type { RoomMessage } from '../observe/room.ts'

const ROOM = 'aaaaaaaa-0000-4000-8000-000000000001'
const OTHER_ROOM = 'bbbbbbbb-0000-4000-8000-000000000002'

function msg(id: string, at: string): RoomMessage {
  return { id, sender_type: 'user', sender_id: 'u1', content_md: id, metadata: {}, created_at: at }
}

function audit(id: number, action: string, agent: string, room: string, at: string, meta: Record<string, unknown> = {}): AuditRow {
  return { id, action, resource_type: 'agent', resource_id: agent, metadata: { room_id: room, ...meta }, created_at: at }
}

describe('collectTranscript (AC-14 logic)', () => {
  it('pages backwards with `before` until it passes the window start, and returns oldest first', async () => {
    // Newest first, as the API returns them.
    const pages: Record<string, RoomMessage[]> = {
      first: [msg('m5', '2026-10-22T10:40:00Z'), msg('m4', '2026-10-22T10:20:00Z')],
      m4: [msg('m3', '2026-10-22T10:10:00Z'), msg('m2', '2026-10-22T09:50:00Z')],
      m2: [msg('m1', '2026-10-22T09:00:00Z')],
    }
    const asked: Array<string | null> = []
    const src: TranscriptSource = {
      messagesPage: async (before) => {
        asked.push(before)
        return pages[before ?? 'first'] ?? []
      },
    }
    const out = await collectTranscript(src, '2026-10-22T10:00:00Z', '2026-10-22T10:30:00Z')
    expect(out.rows.map((m) => m.id)).toEqual(['m3', 'm4'])
    expect(out.truncated).toBe(false)
    expect(asked).toEqual([null, 'm4'])
  })
})

describe('collectWakeAudit (AC-14 logic)', () => {
  it('reads every wake action for every agent, follows cursors, and keeps this room only', async () => {
    const calls: string[] = []
    const src: AuditSource = {
      auditPage: async ({ action, resourceId, cursor }) => {
        calls.push(`${resourceId}:${action}:${cursor}`)
        if (resourceId === 'ta' && action === 'wakeup.fired') {
          return cursor === null
            ? { items: [audit(2, action, 'ta', ROOM, '2026-10-22T10:05:00Z', { trigger: 'message' })], next_cursor: 2 }
            : { items: [audit(1, action, 'ta', OTHER_ROOM, '2026-10-22T10:01:00Z')], next_cursor: null }
        }
        if (resourceId === 'sa' && action === 'wakeup.skipped') {
          return { items: [audit(3, action, 'sa', ROOM.toUpperCase(), '2026-10-22T10:02:00Z', { reason: 'rate_limited' })], next_cursor: null }
        }
        return { items: [], next_cursor: null }
      },
    }
    const rows = await collectWakeAudit(src, ROOM, ['ta', 'sa'], 'f', 't')
    expect(rows.rows.map((r) => r.id)).toEqual([3, 2])
    expect(rows.truncated).toBe(false)
    expect(calls).toHaveLength(7)
    expect(calls).toContain('ta:wakeup.fired:2')
  })
})

describe('buildReport (AC-14)', () => {
  it('states the timeline, wake counts, rate-limit skips, proposals, persona usage, restored limit and worker-log command', () => {
    const report = buildReport({
      scenario: 'crat-ac20',
      target: 'staging',
      roomId: ROOM,
      startedAt: '2026-10-22T10:00:00Z',
      endedAt: '2026-10-22T10:32:00Z',
      steps: [
        { actor: 'P2', index: 1, action: 'say', plannedSec: 305, armedSec: 305, startedSec: 305.4, endedSec: 309, outcome: 'ok' },
        { actor: 'P1', index: 0, action: 'vote', label: 'first | vote', plannedSec: 300, armedSec: 300, startedSec: 300, endedSec: 301, outcome: 'failed', error: 'a | b' },
        { actor: 'F', index: 2, action: 'endActivity', plannedSec: 1860, armedSec: 1860, startedSec: null, endedSec: 1865, outcome: 'timeout' },
      ],
      audit: [
        audit(1, 'wakeup.fired', 'ta', ROOM, '2026-10-22T10:01:00Z', { trigger: 'message' }),
        audit(2, 'wakeup.fired', 'ta', ROOM, '2026-10-22T10:02:00Z', { trigger: 'message' }),
        audit(3, 'wakeup.skipped', 'sa', ROOM, '2026-10-22T10:03:00Z', { trigger: 'message', reason: 'rate_limited' }),
      ],
      agentNames: new Map([
        ['ta', 'TA'],
        ['sa', 'SA'],
      ]),
      proposals: [
        { id: 'p1', activation_id: 'a', member_group_id: 'g', status: 'rejected', required_approvals: 2, approvals: 1, rejections: 2, undecided: 0, created_at: 'x', resolved_at: 'y', submission_id: null },
      ],
      transcriptCount: 42,
      truncated: [],
      persona: { calls: 5, inputTokens: 1000, outputTokens: 50 },
      restoredLimit: 'restored to 10 per 60s',
      notes: ['Pass criteria: protocol §4.'],
      aborted: null,
    })
    expect(report).toMatch(/\| 0 \| P1 \| first \| vote \| 05:00 .*failed: a \/ b \|/)
    expect(report.indexOf('| 0 | P1')).toBeLessThan(report.indexOf('| 1 | P2'))
    expect(report).toContain('| 2 | F | endActivity | 31:00 | 31:00 | --:-- | 31:05 | timeout |')
    expect(report).toContain('| TA | message | wakeup.fired | 2 |')
    expect(report).toContain('Skips with a rate-limit reason: 1.')
    expect(report).toContain('rejected, 1/2 approvals, 2 rejections')
    expect(report).toContain('Room messages in the window: 42.')
    expect(report).toContain('5 calls, 1000 input and 50 output tokens')
    expect(report).toContain('AUTH rate limit: restored to 10 per 60s.')
    expect(report).toContain('logs backend-worker')
    expect(report).toContain('Pass criteria: protocol §4.')
  })

  it('names an aborted run at the top', () => {
    const report = buildReport({
      scenario: 's', target: 't', roomId: ROOM, startedAt: 'a', endedAt: 'b', steps: [], audit: [], agentNames: new Map(),
      proposals: [], transcriptCount: 0, truncated: ['audit'], persona: null, restoredLimit: 'x', notes: [], aborted: 'step 3 timed out',
    })
    expect(report.split('\n').slice(0, 6).join('\n')).toContain('aborted:** step 3 timed out')
    expect(report).toContain('No wake-up audit rows')
    expect(report).toContain('**Incomplete evidence:** audit hit a page cap.')
    expect(report).not.toContain('Persona usage')
  })
})
