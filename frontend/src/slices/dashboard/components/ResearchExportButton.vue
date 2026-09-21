<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDownTrayIcon } from '@heroicons/vue/24/outline'
import SCard from '@shared/ui/SCard.vue'
import { useToast } from '@shared/composables'
import {
  createResearchExport,
  getResearchExportStatus,
} from '../api/researchExport'

const props = defineProps<{
  workspaceId: string
}>()

const { t } = useI18n()
const { toast } = useToast()

const isOpen = ref(false)
const isExporting = ref(false)
const exportStatus = ref<string | null>(null)
const dateAfter = ref<string>('')
const dateBefore = ref<string>('')

const canExport = computed(() => !isExporting.value)

async function startExport() {
  isExporting.value = true
  exportStatus.value = 'queued'

  try {
    const body: Record<string, string | null> = {}
    if (dateAfter.value) body.created_after = dateAfter.value
    if (dateBefore.value) body.created_before = dateBefore.value

    const result = await createResearchExport(props.workspaceId, body)
    exportStatus.value = result.status
    await pollForCompletion(result.job_id)
  } catch {
    exportStatus.value = 'failed'
    toast(t('dashboard.researchExport.error'), 'error')
    isExporting.value = false
  }
}

async function pollForCompletion(jobId: string) {
  const maxAttempts = 60
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, 3000))
    try {
      const status = await getResearchExportStatus(jobId)
      exportStatus.value = status.status

      if (status.status === 'ready' && status.url) {
        window.open(status.url, '_blank')
        toast(t('dashboard.researchExport.ready'), 'success')
        isExporting.value = false
        isOpen.value = false
        return
      }
      if (status.status === 'failed') {
        toast(t('dashboard.researchExport.error'), 'error')
        isExporting.value = false
        return
      }
    } catch {
      // Polling errors are transient; keep trying
    }
  }
  toast(t('dashboard.researchExport.timeout'), 'error')
  isExporting.value = false
}

function closeDialog() {
  if (!isExporting.value) {
    isOpen.value = false
  }
}
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
      :disabled="isExporting"
      @click="isOpen = !isOpen"
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
          class="fixed inset-0 bg-black/40"
          @click="closeDialog"
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
                class="block text-sm font-medium text-[var(--color-fg)] mb-1"
              >
                {{ t('dashboard.researchExport.dateAfter') }}
              </label>
              <input
                v-model="dateAfter"
                type="date"
                class="w-full rounded-md border border-[var(--color-border)]
                       bg-[var(--color-canvas)] text-[var(--color-fg)]
                       px-3 py-1.5 text-sm"
                :disabled="isExporting"
              >
            </div>
            <div>
              <label
                class="block text-sm font-medium text-[var(--color-fg)] mb-1"
              >
                {{ t('dashboard.researchExport.dateBefore') }}
              </label>
              <input
                v-model="dateBefore"
                type="date"
                class="w-full rounded-md border border-[var(--color-border)]
                       bg-[var(--color-canvas)] text-[var(--color-fg)]
                       px-3 py-1.5 text-sm"
                :disabled="isExporting"
              >
            </div>
          </div>

          <div
            v-if="isExporting"
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
            {{ t(`dashboard.researchExport.status.${exportStatus}`) }}
          </div>

          <div class="flex justify-end gap-2">
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-sm font-medium
                     text-[var(--color-fg)]
                     hover:bg-[var(--color-surface-hover)]
                     transition-colors"
              :disabled="isExporting"
              @click="closeDialog"
            >
              {{ t('dashboard.researchExport.cancel') }}
            </button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-sm font-medium
                     bg-[var(--color-accent)] text-white
                     hover:opacity-90 transition-opacity
                     disabled:opacity-50"
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
