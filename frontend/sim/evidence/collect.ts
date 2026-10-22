import type { RoomMessage, Proposal } from '../observe/room.ts'
import type { StepRecord } from '../scheduler/run.ts'
import { formatOffset } from '../lib/time.ts'

export interface AuditRow {
  id: number
  action: string
  resource_type: string | null
  resource_id: string | null
  metadata: Record<string, unknown>
  created_at: string
}

/** Read as the facilitator, who can read the room. */
export interface TranscriptSource {
  messagesPage(before: string | null): Promise<RoomMessage[]>
}

/** Read as the admin, who can read the audit log. */
export interface AuditSource {
  auditPage(params: { action: string; resourceId: string; from: string; to: string; cursor: number | null }): Promise<{
    items: AuditRow[]
    next_cursor: number | null
  }>
}

export const WAKE_ACTIONS = ['wakeup.fired', 'wakeup.skipped', 'wakeup.failed'] as const

const MAX_MESSAGE_PAGES = 500
const MAX_AUDIT_PAGES = 200

export interface Collected<T> {
  rows: T[]
  /** True when a page cap stopped collection, so the rows are incomplete. */
  truncated: boolean
}

/** Every room message created inside the window, oldest first. */
export async function collectTranscript(src: TranscriptSource, fromIso: string, toIso: string): Promise<Collected<RoomMessage>> {
  const from = Date.parse(fromIso)
  const to = Date.parse(toIso)
  const out: RoomMessage[] = []
  let before: string | null = null
  for (let page = 0; page < MAX_MESSAGE_PAGES; page++) {
    const rows = await src.messagesPage(before)
    if (rows.length === 0) return { rows: out.reverse(), truncated: false }
    for (const m of rows) {
      const t = m.created_at ? Date.parse(m.created_at) : NaN
      if (t >= from && t <= to) out.push(m)
    }
    const oldest = rows[rows.length - 1] as RoomMessage
    if (!oldest.created_at || Date.parse(oldest.created_at) < from) return { rows: out.reverse(), truncated: false }
    before = oldest.id
  }
  return { rows: out.reverse(), truncated: true }
}

/**
 * Wake-up audit rows of the room's agents in the window. The audit API filters by exact
 * action and resource but not by room, so the room filter is applied here (FU-3).
 */
export async function collectWakeAudit(
  src: AuditSource,
  roomId: string,
  agentIds: string[],
  fromIso: string,
  toIso: string,
): Promise<Collected<AuditRow>> {
  const rows: AuditRow[] = []
  let truncated = false
  for (const agentId of agentIds) {
    for (const action of WAKE_ACTIONS) {
      let cursor: number | null = null
      let done = false
      for (let page = 0; page < MAX_AUDIT_PAGES && !done; page++) {
        const res = await src.auditPage({ action, resourceId: agentId, from: fromIso, to: toIso, cursor })
        rows.push(...res.items.filter((r) => String(r.metadata.room_id ?? '').toLowerCase() === roomId.toLowerCase()))
        done = res.next_cursor === null
        cursor = res.next_cursor
      }
      if (!done) truncated = true
    }
  }
  return { rows: rows.sort((a, b) => a.created_at.localeCompare(b.created_at)), truncated }
}

export interface ReportInput {
  scenario: string
  target: string
  roomId: string
  startedAt: string
  endedAt: string
  steps: StepRecord[]
  audit: AuditRow[]
  agentNames: Map<string, string>
  proposals: Proposal[]
  transcriptCount: number
  /** Which collections hit a page cap and are incomplete. */
  truncated: string[]
  persona: { calls: number; inputTokens: number; outputTokens: number } | null
  restoredLimit: string
  notes: string[]
  aborted: string | null
}

function fmt(sec: number | null): string {
  return sec === null ? '--:--' : formatOffset(sec)
}

export function buildReport(r: ReportInput): string {
  const lines: string[] = []
  lines.push(`# Simulator run: ${r.scenario}`, '')
  lines.push(`Target ${r.target}, room \`${r.roomId}\`, from ${r.startedAt} to ${r.endedAt}.`)
  if (r.aborted) lines.push('', `**The run was aborted:** ${r.aborted}`)
  if (r.truncated.length > 0) lines.push('', `**Incomplete evidence:** ${r.truncated.join(', ')} hit a page cap.`)
  lines.push('', '## Timeline', '', '| # | Actor | Action | Planned | Armed | Started | Ended | Outcome |', '|---|---|---|---|---|---|---|---|')
  for (const s of [...r.steps].sort((a, b) => a.index - b.index)) {
    const outcome = s.error ? `${s.outcome}: ${s.error.replace(/\|/g, '/')}` : s.outcome
    lines.push(
      `| ${s.index} | ${s.actor} | ${s.label ?? s.action} | ${fmt(s.plannedSec)} | ${fmt(s.armedSec)} | ${fmt(s.startedSec)} | ${fmt(s.endedSec)} | ${outcome} |`,
    )
  }

  lines.push('', '## Wake-ups', '')
  const counts = new Map<string, number>()
  for (const a of r.audit) {
    const key = `${r.agentNames.get(a.resource_id ?? '') ?? a.resource_id} | ${String(a.metadata.trigger ?? '')} | ${a.action}`
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  if (counts.size === 0) lines.push('No wake-up audit rows in the window.')
  else {
    lines.push('| Agent | Trigger | Action | Count |', '|---|---|---|---|')
    for (const [k, n] of [...counts.entries()].sort()) lines.push(`| ${k} | ${n} |`)
  }
  const rateLimited = r.audit.filter((a) => String(a.metadata.reason ?? '').includes('rate_limit'))
  lines.push('', `Skips with a rate-limit reason: ${rateLimited.length}.`)
  for (const a of rateLimited) lines.push(`- ${a.created_at} ${r.agentNames.get(a.resource_id ?? '') ?? a.resource_id} ${String(a.metadata.trigger)}`)

  lines.push('', '## Group proposals', '')
  if (r.proposals.length === 0) lines.push('None observed.')
  for (const p of r.proposals) {
    lines.push(`- ${p.created_at ?? ''} ${p.status}, ${p.approvals}/${p.required_approvals} approvals, ${p.rejections} rejections`)
  }

  lines.push('', '## Other facts', '')
  lines.push(`- Room messages in the window: ${r.transcriptCount}.`)
  if (r.persona) {
    lines.push(`- Persona usage: ${r.persona.calls} calls, ${r.persona.inputTokens} input and ${r.persona.outputTokens} output tokens.`)
  }
  lines.push(`- AUTH rate limit: ${r.restoredLimit}.`)
  lines.push(
    '- Provider 429s in the worker log have no HTTP surface; check them on the server:',
    '  `$COMPOSE logs backend-worker --since 45m | grep -iE "rate.?limit|429" | tail -20`',
  )
  if (r.notes.length > 0) {
    lines.push('', '## Scenario notes', '')
    for (const n of r.notes) lines.push(`- ${n}`)
  }
  return lines.join('\n') + '\n'
}
