// Composable: chatroom export job creation and status polling.
// Extracted from ChatroomView.vue (C4 SoC fix). State and terminal outcomes:
// docs/tasks/2026-10-11-chat-export-modal-states/spec.md §7.

import { ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { usePolling } from '@shared/composables'
import { ApiError, RateLimitError, ValidationError } from '@shared/errors'
import { createExport, getExport, type ExportOptions } from '../api'
import type { ExportStatus } from '../types'
import { downloadFresh } from '@shared/utils/download'

export type ExportPhase =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'running'; jobId: string }
  // `unreachable`: no status request succeeded in the whole window, so the
  // job's state is unknown rather than slow.
  | { kind: 'stalled'; jobId: string; reason: 'slow' | 'unreachable' }
  | { kind: 'ready'; jobId: string }
  | { kind: 'start-failed'; opts: ExportOptions }
  | { kind: 'job-failed' }
  // `pdf`: the window holds more messages than a PDF export allows.
  | { kind: 'invalid'; field: 'start' | 'end' | null; pdf: { count: number; limit: number } | null }

// About 12 minutes, enough for the worker's 600-second timeout plus queue
// wait: 3 s for the first minute, 6 s to minute 4, then 10 s.
const POLL_ATTEMPTS = 98
function pollDelay(attempt: number): number {
  if (attempt <= 20) return 3000
  if (attempt <= 50) return 6000
  return 10_000
}

// Status responses that cannot change on retry: the job is gone or not ours.
const FATAL_STATUS = new Set([403, 404, 410])

function problemSlug(e: ApiError): string {
  return e.type.split('/problems/').pop() ?? ''
}

function pdfLimit(e: ValidationError): { count: number; limit: number } | null {
  const { message_count: count, limit } = e.extra
  return problemSlug(e) === 'conversation/export-too-large-for-pdf' &&
    typeof count === 'number' &&
    typeof limit === 'number'
    ? { count, limit }
    : null
}

// The one active export this user already has in the room (409).
function runningJob(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  if (problemSlug(e) !== 'conversation/export-in-progress') return null
  return typeof e.extra.job_id === 'string' ? e.extra.job_id : null
}

// Only body errors are about the form's inputs (see ValidationFieldError).
function invalidField(error: ValidationError): 'start' | 'end' | null {
  for (const { location, path } of error.fieldErrors) {
    if (location !== 'body') continue
    const leaf = path.split('.').pop()
    if (leaf === 'start' || leaf === 'end') return leaf
  }
  return null
}

export function useChatroomExport(chatroomId: string) {
  const { t } = useI18n()
  const toast = useToast()

  const phase = ref<ExportPhase>({ kind: 'idle' })
  // The single-slot consumer: `phase` describes one job at a time, so the
  // poller must feed it from exactly one key. Ticks from any other job are
  // superseded and ignored.
  let activeJobId: string | null = null
  // A job id does not exist until the create returns, so supersession of a
  // pending create is tracked by this token instead: every runExport and
  // reset advances it, and a create response carrying an older one is dropped.
  let requestToken = 0
  // Whether any status request in the current polling window got an answer.
  let answered = false

  async function fetchStatus(jobId: string): Promise<ExportStatus> {
    const s = await getExport(jobId)
    if (jobId === activeJobId) answered = true
    return s
  }

  const exportPoll = usePolling<ExportStatus>(fetchStatus, {
    intervalMs: pollDelay,
    maxAttempts: POLL_ATTEMPTS,
    isTerminal: (s) => s.status === 'ready' || s.status === 'failed',
    isFatal: (e) => e instanceof ApiError && FATAL_STATUS.has(e.status),
    retryAfterMs: (e) => (e instanceof RateLimitError ? e.retryAfterMs : null),
    onResult: (jobId, s) => {
      if (jobId !== activeJobId) return
      if (s.status === 'ready') phase.value = { kind: 'ready', jobId }
      else if (s.status === 'failed') phase.value = { kind: 'job-failed' }
      else phase.value = { kind: 'running', jobId }
    },
    onError: (jobId) => {
      if (jobId !== activeJobId) return
      phase.value = { kind: 'job-failed' }
    },
    onExhausted: (jobId) => {
      if (jobId !== activeJobId) return
      phase.value = { kind: 'stalled', jobId, reason: answered ? 'slow' : 'unreachable' }
    },
  })

  function poll(jobId: string): void {
    answered = false
    exportPoll.start(jobId)
  }

  function supersede(): number {
    if (activeJobId) exportPoll.cancel(activeJobId)
    activeJobId = null
    return ++requestToken
  }

  async function runExport(opts: ExportOptions = {}): Promise<void> {
    const token = supersede()
    phase.value = { kind: 'starting' }
    try {
      const { job_id } = await createExport(chatroomId, opts)
      if (token !== requestToken) return
      activeJobId = job_id
      phase.value = { kind: 'running', jobId: job_id }
      poll(job_id)
    } catch (e) {
      if (token !== requestToken) return
      const running = runningJob(e)
      if (running) {
        // The server allows one export per room at a time; follow that one.
        activeJobId = running
        phase.value = { kind: 'running', jobId: running }
        poll(running)
        return
      }
      phase.value =
        e instanceof ValidationError
          ? { kind: 'invalid', field: invalidField(e), pdf: pdfLimit(e) }
          : { kind: 'start-failed', opts }
    }
  }

  function checkAgain(): void {
    if (phase.value.kind !== 'stalled') return
    const { jobId } = phase.value
    phase.value = { kind: 'running', jobId }
    poll(jobId)
  }

  // A start failure is retried as is. A job failure goes back to the form,
  // because the range or format may be what failed (Q-4).
  async function retry(): Promise<void> {
    const current = phase.value
    if (current.kind === 'start-failed') await runExport(current.opts)
    else if (current.kind === 'job-failed') reset()
  }

  // A link held since the job became ready may have expired; fetch one now.
  async function download(): Promise<void> {
    if (phase.value.kind !== 'ready') return
    const { jobId } = phase.value
    const result = await downloadFresh(() => getExport(jobId))
    if (result === 'expired') toast.error(t('conversation.chatroom.exportExpired'))
    else if (result === 'failed') toast.error(t('conversation.chatroom.exportDownloadFailed'))
  }

  function reset(): void {
    supersede()
    phase.value = { kind: 'idle' }
  }

  return {
    phase,
    runExport,
    checkAgain,
    retry,
    download,
    reset,
  }
}
