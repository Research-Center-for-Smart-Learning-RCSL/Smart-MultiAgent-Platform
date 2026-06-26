<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { SPageHeader } from '@shared/ui'

import { getAgentWakeupConfig, patchAgentWakeupConfig } from '../api'
import type { WakeupConfig } from '../types'
import WakeupConfigEditor from '../components/WakeupConfigEditor.vue'
import DlqViewer from '../components/DlqViewer.vue'

const { t } = useI18n()
const route = useRoute()
const agentId = route.params.agentId as string
const toast = useToast()

// The stored wakeup_config is `{}` by default, but the editor accesses the full
// nested shape — so merge whatever is stored onto a complete default.
const DEFAULT_WAKEUP: WakeupConfig = {
  triggers: {
    every_n_messages: { enabled: false, n: 5 },
    silence_minutes: {
      enabled: false,
      t_minutes: 30,
      autostop_rounds: 3,
      autostop_max_default: 10,
    },
    call_only: { enabled: false },
  },
  allow_self_open: false,
  refresh_every_hours: 24,
}

function withDefaults(raw: unknown): WakeupConfig {
  const r = (raw ?? {}) as Record<string, unknown>
  const t0 = (r.triggers ?? {}) as Record<string, Record<string, unknown>>
  return {
    triggers: {
      every_n_messages: { ...DEFAULT_WAKEUP.triggers.every_n_messages, ...(t0.every_n_messages ?? {}) },
      silence_minutes: { ...DEFAULT_WAKEUP.triggers.silence_minutes, ...(t0.silence_minutes ?? {}) },
      call_only: { ...DEFAULT_WAKEUP.triggers.call_only, ...(t0.call_only ?? {}) },
    },
    allow_self_open: (r.allow_self_open as boolean) ?? DEFAULT_WAKEUP.allow_self_open,
    refresh_every_hours: (r.refresh_every_hours as number) ?? DEFAULT_WAKEUP.refresh_every_hours,
  }
}

const config = ref<WakeupConfig | null>(null)
const loading = ref(true)
const saving = ref(false)
// Agent PATCH requires an If-Match precondition; track the version across saves.
const version = ref(0)

onMounted(async () => {
  try {
    const { wakeupConfig, version: v } = await getAgentWakeupConfig(agentId)
    config.value = withDefaults(wakeupConfig)
    version.value = v
  } catch {
    config.value = withDefaults({})
    toast.error(t('workflow.agentOps.loadError'))
  } finally {
    loading.value = false
  }
})

async function save(): Promise<void> {
  if (!config.value) return
  saving.value = true
  try {
    version.value = await patchAgentWakeupConfig(agentId, config.value, version.value)
    toast.success(t('workflow.agentOps.saved'))
  } catch {
    toast.error(t('workflow.agentOps.saveError'))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="agent-ops p-4">
    <SPageHeader
      :title="t('workflow.agentOps.title')"
      :subtitle="t('workflow.agentOps.subtitle')"
    />

    <div class="mb-6">
      <h2 class="font-semibold mb-2">
        {{ t('workflow.agentOps.wakeupSection') }}
      </h2>
      <p v-if="loading">
        {{ t('workflow.agentOps.loading') }}
      </p>
      <template v-else-if="config">
        <WakeupConfigEditor v-model="config" />
        <button
          class="btn btn-primary mt-2"
          type="button"
          :disabled="saving"
          @click="save"
        >
          {{ t('workflow.agentOps.save') }}
        </button>
      </template>
    </div>

    <div class="mb-6">
      <h2 class="font-semibold mb-2">
        {{ t('workflow.agentOps.dlqSection') }}
      </h2>
      <DlqViewer :agent-id="agentId" />
    </div>
  </section>
</template>
