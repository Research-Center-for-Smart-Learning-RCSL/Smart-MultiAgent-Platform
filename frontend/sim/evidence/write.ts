import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Proposal } from '../observe/room.ts'
import type { StepRecord } from '../scheduler/run.ts'
import type { Session } from '../session.ts'
import { auditApi, roomApi } from '../sources.ts'
import { buildReport, collectTranscript, collectWakeAudit } from './collect.ts'

export interface EvidenceInput {
  runDir: string
  roomId: string
  agentIds: string[]
  from: string
  to: string
  steps: StepRecord[]
  proposals: Proposal[]
  persona: { calls: number; inputTokens: number; outputTokens: number } | null
  scenario: string
  notes: string[]
  aborted: string | null
  restored?: string
}

/** Collects the room's evidence for the window and writes it, redacted, to the run directory. */
export async function writeEvidence(s: Session, e: EvidenceInput): Promise<void> {
  const room = roomApi(s.facilitator, e.roomId)
  const transcript = await collectTranscript(room, e.from, e.to)
  const audit = await collectWakeAudit(auditApi(s.admin), e.roomId, e.agentIds, e.from, e.to)
  const agents = await room.agents()
  const agentNames = new Map(agents.map((a) => [a.agent_id, a.name ?? a.agent_id]))
  const write = (name: string, text: string): void => writeFileSync(join(e.runDir, name), s.log.redact(text))
  write('transcript.json', JSON.stringify(transcript.rows, null, 2))
  write('audit.json', JSON.stringify(audit.rows, null, 2))
  write('proposals.json', JSON.stringify(e.proposals, null, 2))
  const truncated = [...(transcript.truncated ? ['transcript'] : []), ...(audit.truncated ? ['audit'] : [])]
  write(
    'report.md',
    buildReport({
      scenario: e.scenario,
      target: s.target.name,
      roomId: e.roomId,
      startedAt: e.from,
      endedAt: e.to,
      steps: e.steps,
      audit: audit.rows,
      agentNames,
      proposals: e.proposals,
      transcriptCount: transcript.rows.length,
      truncated,
      persona: e.persona,
      restoredLimit: e.restored ?? 'not changed by this command',
      notes: e.notes,
      aborted: e.aborted,
    }),
  )
  if (truncated.length > 0) s.log.warn(`evidence incomplete: ${truncated.join(', ')} hit a page cap`)
  s.log.info(`evidence written to ${e.runDir}`)
}
