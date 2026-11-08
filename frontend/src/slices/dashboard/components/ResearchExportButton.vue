<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDownTrayIcon } from '@heroicons/vue/24/outline'
import SCard from '@shared/ui/SCard.vue'
import { usePolling, useToast } from '@shared/composables'
import { ApiError, RateLimitError } from '@shared/errors'
import { isProblemWithType } from '@shared/transport'
import { downloadFresh } from '@shared/utils/download'
import {
  createResearchExport,
  getLatestResearchExport,
  getResearchExportStatus,
} from '../api/researchExport'

const props = defineProps<{
  workspaceId: string
  // A job to open on arrival, e.g. from the `export.ready` notification.
  openJobId?: string | null
}>()

const { t } = useI18n()
const toasts = useToast()

type Phase =
  | { kind: 'idle' }
  | { kind: 'starting' }
  // `attaching`: following a job found on the server, whose state is not yet
  // known, so a ready result is not announced as news.
  | { kind: 'attaching'; jobId: string }
  | { kind: 'running'; jobId: string; status: 'queued' | 'running' }
  | { kind: 'stalled'; jobId: string }
  | { kind: 'ready'; jobId: string }

// About 15 minutes, the worker's timeout for this task.
const POLL_ATTEMPTS = 120
function pollDelay(attempt: number): number {
  return attempt <= 20 ? 3000 : 10_000
}
const FATAL_STATUS = new Set([403, 404, 410])

const isOpen = ref(false)
const phase = ref<Phase>({ kind: 'idle' })
const dateAfter = ref<string>('')
const dateBefore = ref<string>('')

// `phase` describes one job, so only its key may write to it.
let activeJobId: string | null = null
// A create or latest lookup has no job id yet; a response carrying an older
// token than the current one was superseded and is dropped.
let requestToken = 0

// A ready export does not hold the workspace's export slot, so a new one may start.
const canExport = computed(() => phase.value.kind === 'idle' || phase.value.kind === 'ready')
const statusLabel = computed(() => {
  const p = phase.value
  if (p.kind === 'running') return t(`dashboard.researchExport.status.${p.status}`)
  if (p.kind === 'starting' || p.kind === 'attaching') return t('dashboard.researchExport.status.queued')
  return null
})

function settle(jobId: string, status: string): void {
  if (jobId !== activeJobId) return
  const announce = phase.value.kind === 'running'
  if (status === 'ready') {
    // No download here: this runs long after the click, outside the user
    // gesture. The Download button fetches a fresh link instead.
    phase.value = { kind: 'ready', jobId }
    if (announce) toasts.success(t('dashboard.researchExport.ready'))
  } else if (status === 'failed') {
    endFollowing()
    toasts.error(t('dashboard.researchExport.error'))
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
    endFollowing()
    const gone = e instanceof ApiError && e.status !== 403
    toasts.error(t(gone ? 'dashboard.researchExport.expired' : 'dashboard.researchExport.error'))
  },
  onExhausted: (jobId) => {
    if (jobId !== activeJobId) return
    phase.value = { kind: 'stalled', jobId }
  },
})

function follow(jobId: string, attached: boolean): void {
  activeJobId = jobId
  phase.value = attached ? { kind: 'attaching', jobId } : { kind: 'running', jobId, status: 'queued' }
  poll.start(jobId)
}

function endFollowing(): number {
  if (activeJobId) poll.cancel(activeJobId)
  activeJobId = null
  phase.value = { kind: 'idle' }
  return ++requestToken
}

// The one export this owner already has in the workspace (409).
function runningJob(e: unknown): string | null {
  if (!(e instanceof ApiError) || !isProblemWithType(e, '/conversation/research-export-in-progress')) return null
  return typeof e.extra.job_id === 'string' ? e.extra.job_id : null
}

// The dialog may have been closed, the page reloaded, or polling may have given
// up; the server's latest job for this workspace is the one to show.
async function reattach(): Promise<void> {
  const token = endFollowing()
  try {
    const latest = await getLatestResearchExport(props.workspaceId)
    if (token !== requestToken) return
    if (latest && latest.status !== 'failed') follow(latest.job_id, true)
    else phase.value = { kind: 'idle' }
  } catch {
    if (token === requestToken) phase.value = { kind: 'idle' }
  }
}

function openDialog(): void {
  isOpen.value = true
  if (phase.value.kind === 'idle' || phase.value.kind === 'stalled') void reattach()
}

async function startExport(): Promise<void> {
  const token = endFollowing()
  phase.value = { kind: 'starting' }
  const body: Record<string, string> = {}
  if (dateAfter.value) body.created_after = dateAfter.value
  if (dateBefore.value) body.created_before = dateBefore.value
  try {
    const { job_id } = await createResearchExport(props.workspaceId, body)
    if (token === requestToken) follow(job_id, false)
  } catch (e) {
    if (token !== requestToken) return
    const running = runningJob(e)
    if (running) {
      follow(running, false)
      return
    }
    phase.value = { kind: 'idle' }
    toasts.error(t('dashboard.researchExport.error'))
  }
}

function checkAgain(): void {
  if (phase.value.kind !== 'stalled') return
  follow(phase.value.jobId, false)
}

async function downloadReady(): Promise<void> {
  if (phase.value.kind !== 'ready') return
  const { jobId } = phase.value
  const result = await downloadFresh(() => getResearchExportStatus(jobId))
  if (result === 'ok') return
  if (result === 'expired') {
    if (jobId === activeJobId) endFollowing()
    toasts.error(t('dashboard.researchExport.expired'))
  } else {
    toasts.error(t('dashboard.researchExport.error'))
  }
}

// Closing keeps following the job, so its completion still raises a toast.
function closeDialog(): void {
  isOpen.value = false
}

watch(
  () => props.openJobId,
  (jobId) => {
    if (!jobId) return
    endFollowing()
    isOpen.value = true
    follow(jobId, true)
  },
  { immediate: true },
)
</script>

<template>
  <div class="relative">
    <button
      type="button"
      class="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5
             text-sm font-medium
             bg-[var(--color-canvas)] text-[var(--color-fg)]
             border border-[var(--color-border)]
             hover:bg-[var(--color-surface-hover)]
             transition-colors"
      data-testid="research-export-open"
      @click="openDialog"
    >
      <ArrowDownTrayIcon class="w-4 h-4" />
      {{ t('dashboard.researchExport.button') }}
    </button>

    <Teleport to="body">
      <div
        v-if="isOpen"
        class="fixed inset-0 z-50 flex items-center justify-center"
      >
        <div
          role="button"
          tabindex="-1"
          :aria-label="t('dashboard.researchExport.cancel')"
          class="fixed inset-0 bg-black/40"
          @click="closeDialog"
          @keydown.escape="closeDialog"
        />
        <SCard class="relative z-10 w-full max-w-md p-6 mx-4">
          <h3 class="text-lg font-semibold text-[var(--color-fg)] mb-4">
            {{ t('dashboard.researchExport.dialogTitle') }}
          </h3>
          <p class="text-sm text-[var(--color-muted)] mb-4">
            {{ t('dashboard.researchExport.dialogDescription') }}
          </p>

          <div class="space-y-3 mb-6">
            <div>
              <label
                for="research-export-date-after"
                class="block text-sm font-medium text-[var(--color-fg)] mb-1"
              >
                {{ t('dashboard.researchExport.dateAfter') }}
              </label>
              <input
                id="research-export-date-after"
                v-model="dateAfter"
                type="date"
                class="w-full rounded-md border border-[var(--color-border)]
                       bg-[var(--color-canvas)] text-[var(--color-fg)]
                       px-3 py-1.5 text-sm"
                :disabled="!canExport"
              >
            </div>
            <div>
              <label
                for="research-export-date-before"
                class="block text-sm font-medium text-[var(--color-fg)] mb-1"
              >
                {{ t('dashboard.researchExport.dateBefore') }}
              </label>
              <input
                id="research-export-date-before"
                v-model="dateBefore"
                type="date"
                class="w-full rounded-md border border-[var(--color-border)]
                       bg-[var(--color-canvas)] text-[var(--color-fg)]
                       px-3 py-1.5 text-sm"
                :disabled="!canExport"
              >
            </div>
          </div>

          <div
            v-if="statusLabel"
            data-testid="research-export-progress"
            class="mb-4 flex items-center gap-2 text-sm text-[var(--color-muted)]"
          >
            <svg
              class="animate-spin w-4 h-4"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
                class="opacity-25"
              />
              <path
                fill="currentColor"
                d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                class="opacity-75"
              />
            </svg>
            {{ statusLabel }}
          </div>

          <div
            v-if="phase.kind === 'stalled'"
            data-testid="research-export-stalled"
            class="mb-4 text-sm text-[var(--color-muted)]"
          >
            {{ t('dashboard.researchExport.timeout') }}
          </div>

          <div
            v-if="phase.kind === 'ready'"
            class="mb-4 text-sm text-[var(--color-fg)]"
          >
            {{ t('dashboard.researchExport.status.ready') }}
          </div>

          <div class="flex justify-end gap-2">
            <button
              v-if="phase.kind === 'stalled'"
              type="button"
              data-testid="research-export-check-again"
              class="rounded-md px-3 py-1.5 text-sm font-medium
                     text-[var(--color-fg)]
                     hover:bg-[var(--color-surface-hover)]
                     transition-colors"
              @click="checkAgain"
            >
              {{ t('dashboard.researchExport.checkAgain') }}
            </button>
            <button
              v-if="phase.kind === 'ready'"
              type="button"
              data-testid="research-export-download"
              class="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium
                     bg-[var(--color-accent)] text-white
                     hover:opacity-90 transition-opacity"
              @click="downloadReady"
            >
              <ArrowDownTrayIcon class="w-4 h-4" />
              {{ t('dashboard.researchExport.download') }}
            </button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-sm font-medium
                     text-[var(--color-fg)]
                     hover:bg-[var(--color-surface-hover)]
                     transition-colors"
              data-testid="research-export-close"
              @click="closeDialog"
            >
              {{ phase.kind === 'idle' ? t('dashboard.researchExport.cancel') : t('dashboard.researchExport.close') }}
            </button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-sm font-medium
                     bg-[var(--color-accent)] text-white
                     hover:opacity-90 transition-opacity
                     disabled:opacity-50"
              data-testid="research-export-start"
              :disabled="!canExport"
              @click="startExport"
            >
              {{ t('dashboard.researchExport.export') }}
            </button>
          </div>
        </SCard>
      </div>
    </Teleport>
  </div>
</template>
