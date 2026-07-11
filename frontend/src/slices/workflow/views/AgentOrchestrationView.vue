<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useToast } from '@shared/composables'
import { SButton, SPageHeader, SWakeupEditor } from '@shared/ui'
import { normalizeWakeupConfig, type WakeupConfig } from '@shared/types/workflow'
import { agentsApi, agentKeys } from '@slices/agents'

import { patchAgentWakeupConfig } from '../api'
import DlqViewer from '../components/DlqViewer.vue'

const { t } = useI18n()
const route = useRoute()
const agentId = route.params.agentId as string
const toast = useToast()
const qc = useQueryClient()

const agentQuery = useQuery({
  queryKey: agentKeys.agent(agentId),
  queryFn: () => agentsApi.get(agentId),
})

const breadcrumbs = computed(() => [
  { label: agentQuery.data.value?.name ?? '...', to: { name: 'agents.detail', params: { agentId } } },
  { label: t('workflow.agentOps.breadcrumb') },
])

const config = ref<WakeupConfig | null>(null)
const saving = ref(false)
const version = ref(0)
const initialized = ref(false)

watch(() => agentQuery.data.value, (agent) => {
  if (!agent || initialized.value) return
  config.value = normalizeWakeupConfig(agent.wakeup_config)
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
        <SWakeupEditor v-model="config" />
        <SButton
          variant="primary"
          class="mt-2"
          type="button"
          :loading="saving"
          @click="save"
        >
          {{ t('workflow.agentOps.save') }}
        </SButton>
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
