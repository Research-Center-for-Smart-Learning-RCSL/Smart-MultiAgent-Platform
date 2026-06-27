<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useToast } from '@shared/composables'
import { SPageHeader } from '@shared/ui'
import { agentsApi, agentKeys } from '@slices/agents'

import { patchAgentWakeupConfig } from '../api'
import type { WakeupConfig } from '../types'
import WakeupConfigEditor from '../components/WakeupConfigEditor.vue'
import DlqViewer from '../components/DlqViewer.vue'

const { t } = useI18n()
const route = useRoute()
const agentId = route.params.agentId as string
const toast = useToast()
const qc = useQueryClient()

const agentQuery = useQuery({
  queryKey: agentKeys.agent(agentId),
  queryFn: async () => (await agentsApi.get(agentId)).data,
})

const breadcrumbs = computed(() => [
  { label: agentQuery.data.value?.name ?? '...', to: { name: 'agents.detail', params: { agentId } } },
  { label: t('workflow.agentOps.breadcrumb') },
])

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
const saving = ref(false)
const version = ref(0)
const initialized = ref(false)

watch(() => agentQuery.data.value, (agent) => {
  if (!agent || initialized.value) return
  config.value = withDefaults(agent.wakeup_config)
  version.value = agent.version
  initialized.value = true
}, { immediate: true })

async function save(): Promise<void> {
  if (!config.value) return
  saving.value = true
  try {
    version.value = await patchAgentWakeupConfig(agentId, config.value, version.value)
    await qc.invalidateQueries({ queryKey: agentKeys.agent(agentId) })
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
      :breadcrumbs="breadcrumbs"
    />

    <div class="mb-6">
      <h2 class="font-semibold mb-2">
        {{ t('workflow.agentOps.wakeupSection') }}
      </h2>
      <p v-if="agentQuery.isLoading.value">
        {{ t('workflow.agentOps.loading') }}
      </p>
      <p
        v-else-if="agentQuery.isError.value"
        class="text-red-600"
      >
        {{ t('workflow.agentOps.loadError') }}
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
