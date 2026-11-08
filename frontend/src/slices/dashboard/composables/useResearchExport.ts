// Research export job state for the dashboard's export dialog: one job at a
// time, found again after the dialog or the page that started it is gone
// (docs/tasks/2026-11-08-research-data-integrity, AC-2 and AC-3). Modelled on
// conversation's useChatroomExport.

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePolling, useToast } from '@shared/composables'
import { ApiError, RateLimitError } from '@shared/errors'
import { isProblemWithType } from '@shared/transport'
import { downloadFresh } from '@shared/utils/download'
import {
  createResearchExport,
  getLatestResearchExport,
  getResearchExportStatus,
  type ResearchExportCreateIn,
} from '../api/researchExport'

export type ResearchExportPhase =
  | { kind: 'idle' }
  | { kind: 'starting' }
  // `attaching`: following a job found on the server, whose state is not yet
  // known, so a ready result is not announced as news.
  | { kind: 'attaching'; jobId: string }
  | { kind: 'running'; jobId: string; status: 'queued' | 'running' }
  | { kind: 'stalled'; jobId: string }
  | { kind: 'ready'; jobId: string }

// How the followed job came to be shown. A job the dialog looked up on its own
// (`found`) that turns out failed or gone on the first look goes quietly back
// to the form; any other outcome is reported.
type Origin = 'started' | 'opened' | 'found'

// About 15 minutes, the worker's timeout for this task.
const POLL_ATTEMPTS = 120
function pollDelay(attempt: number): number {
  return attempt <= 20 ? 3000 : 10_000
}
// The job is gone (404, 410) or no longer ours (403); asking again cannot help.
const FATAL_STATUS = new Set([403, 404, 410])

// The one export this owner already has in the workspace (409).
function runningJob(e: unknown): string | null {
  if (!(e instanceof ApiError) || !isProblemWithType(e, '/conversation/research-export-in-progress')) return null
  return typeof e.extra.job_id === 'string' ? e.extra.job_id : null
}

export function useResearchExport(workspaceId: () => string) {
  const { t } = useI18n()
  const toasts = useToast()

  const phase = ref<ResearchExportPhase>({ kind: 'idle' })
  // `phase` describes one job, so only its key may write to it.
  let activeJobId: string | null = null
  let origin: Origin = 'started'
  // A create or latest lookup has no job id yet; a response carrying an older
  // token than the current one was superseded and is dropped.
  let requestToken = 0

  // A ready export does not hold the workspace's export slot, so a new one may start.
  const canExport = computed(() => phase.value.kind === 'idle' || phase.value.kind === 'ready')

  function fail(message: string): void {
    const quiet = origin === 'found' && phase.value.kind === 'attaching'
    supersede()
    if (!quiet) toasts.error(t(message))
  }

  function settle(jobId: string, status: string): void {
    if (jobId !== activeJobId) return
    const announce = phase.value.kind === 'running'
    if (status === 'ready') {
      // No download here: this runs long after the click, outside the user
      // gesture. The Download button fetches a fresh link instead.
      phase.value = { kind: 'ready', jobId }
      if (announce) toasts.success(t('dashboard.researchExport.ready'))
    } else if (status === 'failed') {
      fail('dashboard.researchExport.error')
    } else {
      phase.value = { kind: 'running', jobId, status: status === 'running' ? 'running' : 'queued' }
    }
  }

  const poll = usePolling(getResearchExportStatus, {
    intervalMs: pollDelay,
    maxAttempts: POLL_ATTEMPTS,
    isTerminal: (s) => s.status === 'ready' || s.status === 'failed',
    isFatal: (e) => e instanceof ApiError && FATAL_STATUS.has(e.status),
    retryAfterMs: (e) => (e instanceof RateLimitError ? e.retryAfterMs : null),
    onResult: (jobId, s) => settle(jobId, s.status),
    onError: (jobId, e) => {
      if (jobId !== activeJobId) return
      const gone = e instanceof ApiError && e.status !== 403
      fail(gone ? 'dashboard.researchExport.expired' : 'dashboard.researchExport.error')
    },
    onExhausted: (jobId) => {
      if (jobId !== activeJobId) return
      phase.value = { kind: 'stalled', jobId }
    },
  })

  function follow(jobId: string, from: Origin): void {
    activeJobId = jobId
    origin = from
    phase.value = from === 'started' ? { kind: 'running', jobId, status: 'queued' } : { kind: 'attaching', jobId }
    poll.start(jobId)
  }

  function supersede(): number {
    if (activeJobId) poll.cancel(activeJobId)
    activeJobId = null
    phase.value = { kind: 'idle' }
    return ++requestToken
  }

  // The dialog may have been closed, the page reloaded, or polling may have
  // given up; the server's latest job for this workspace is the one to show.
  // A failed lookup leaves the form, from which a 409 still finds the job.
  async function reattach(): Promise<void> {
    const token = supersede()
    try {
      const latest = await getLatestResearchExport(workspaceId())
      if (token === requestToken && latest && latest.status !== 'failed') follow(latest.job_id, 'found')
    } catch {
      // Intentionally quiet: see above.
    }
  }

  // Re-attach unless a job is already being followed or offered.
  function refresh(): void {
    if (phase.value.kind === 'idle' || phase.value.kind === 'stalled') void reattach()
  }

  function open(jobId: string): void {
    supersede()
    follow(jobId, 'opened')
  }

  async function start(body: ResearchExportCreateIn): Promise<void> {
    const token = supersede()
    phase.value = { kind: 'starting' }
    try {
      const { job_id } = await createResearchExport(workspaceId(), body)
      if (token === requestToken) follow(job_id, 'started')
    } catch (e) {
      if (token !== requestToken) return
      const running = runningJob(e)
      if (running) {
        follow(running, 'started')
        return
      }
      phase.value = { kind: 'idle' }
      toasts.error(t('dashboard.researchExport.error'))
    }
  }

  function checkAgain(): void {
    if (phase.value.kind !== 'stalled') return
    follow(phase.value.jobId, 'started')
  }

  async function download(): Promise<void> {
    if (phase.value.kind !== 'ready') return
    const { jobId } = phase.value
    const result = await downloadFresh(() => getResearchExportStatus(jobId))
    if (result === 'ok') return
    if (result === 'expired') {
      if (jobId === activeJobId) supersede()
      toasts.error(t('dashboard.researchExport.expired'))
    } else {
      toasts.error(t('dashboard.researchExport.error'))
    }
  }

  return { phase, canExport, refresh, open, start, checkAgain, download }
}
