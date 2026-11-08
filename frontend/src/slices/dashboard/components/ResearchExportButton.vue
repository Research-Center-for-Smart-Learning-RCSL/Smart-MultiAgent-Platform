<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDownTrayIcon } from '@heroicons/vue/24/outline'
import SCard from '@shared/ui/SCard.vue'
import type { ResearchExportCreateIn } from '../api/researchExport'
import { useResearchExport } from '../composables/useResearchExport'

const props = defineProps<{
  workspaceId: string
  // A job to open, e.g. from the `export.ready` notification. A new object
  // opens the job again even when its id is unchanged.
  openRequest?: { jobId: string } | null
}>()

const { t } = useI18n()
const { phase, canExport, refresh, open, start, checkAgain, download } = useResearchExport(
  () => props.workspaceId,
)

const isOpen = ref(false)
const dateAfter = ref<string>('')
const dateBefore = ref<string>('')

const statusLabel = computed(() => {
  const p = phase.value
  if (p.kind === 'running') return t(`dashboard.researchExport.status.${p.status}`)
  if (p.kind === 'starting' || p.kind === 'attaching') return t('dashboard.researchExport.status.queued')
  return null
})

function openDialog(): void {
  isOpen.value = true
  refresh()
}

function startExport(): void {
  const body: ResearchExportCreateIn = {}
  if (dateAfter.value) body.created_after = dateAfter.value
  if (dateBefore.value) body.created_before = dateBefore.value
  void start(body)
}

// Closing keeps following the job, so its completion still raises a toast.
function closeDialog(): void {
  isOpen.value = false
}

watch(
  () => props.openRequest,
  (request) => {
    if (!request) return
    isOpen.value = true
    open(request.jobId)
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
              @click="download"
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
