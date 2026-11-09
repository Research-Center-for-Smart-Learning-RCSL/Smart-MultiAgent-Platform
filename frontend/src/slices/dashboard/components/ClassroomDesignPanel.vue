<script setup lang="ts">
// "Design classroom agents" ([R34.08]): the owner fills a form, the project's
// design agent drafts teacher and peer prompts, and the owner compares them
// with the current ones before applying both at once. Nothing is written to an
// agent until Apply, and Apply is refused if either agent changed meanwhile.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { SparklesIcon } from '@heroicons/vue/24/outline'
import { SAlert, SCard, SLoadingSpinner, SQueryError } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import type { DesignRunIn } from '@shared/api-client'
import { agentKeys, agentsApi } from '@slices/agents'
import { activityKeys, listActivityTypes } from '@slices/activities'
import { useClassroomDesign } from '../composables/useClassroomDesign'
import DesignProposalView from './DesignProposalView.vue'
import DesignRunForm from './DesignRunForm.vue'

const props = defineProps<{
  projectId: string
}>()

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()

const agentsQuery = useQuery({
  queryKey: computed(() => agentKeys.agents(props.projectId)),
  queryFn: () => agentsApi.list(props.projectId),
})
const typesQuery = useQuery({
  queryKey: computed(() => activityKeys.types(props.projectId)),
  queryFn: () => listActivityTypes(props.projectId),
})
const agentOptions = computed(() =>
  (agentsQuery.data.value ?? []).map((a) => ({ value: a.id, label: a.name })),
)
const typeOptions = computed(() =>
  (typesQuery.data.value ?? []).map((ty) => ({ value: ty.id, label: ty.name })),
)
const pickerError = computed(() => agentsQuery.error.value ?? typesQuery.error.value ?? null)
function refetchPickers(): void {
  void agentsQuery.refetch()
  void typesQuery.refetch()
}
const agentName = (id: string): string =>
  agentsQuery.data.value?.find((a) => a.id === id)?.name ?? t('dashboard.alerts.unknownAgent')

const design = useClassroomDesign(() => props.projectId)
const { run, inFlight, conflict } = design
// The owner chose to start over while a proposal is on screen.
const startingOver = ref(false)
const showForm = computed(
  () => !inFlight.value && (!run.value || run.value.status !== 'succeeded' || startingOver.value || conflict.value),
)

async function onSubmit(values: DesignRunIn): Promise<void> {
  try {
    await design.create(values)
    startingOver.value = false
  } catch (err) {
    toast.error(
      isProblemWithType(err, '/agents/design-run-in-flight')
        ? t('dashboard.design.inFlight')
        : t('dashboard.design.submitFailed'),
    )
  }
}

async function onApply(): Promise<void> {
  const current = run.value
  if (!current) return
  const ok = await confirm({
    title: t('dashboard.design.applyTitle'),
    message: t('dashboard.design.applyMessage', {
      teacher: agentName(current.teacher.agent_id),
      peer: agentName(current.peer.agent_id),
    }),
    confirmLabel: t('dashboard.design.apply'),
    variant: 'warning',
  })
  if (!ok) return
  const outcome = await design.apply(current.id)
  if (outcome === 'applied') toast.success(t('dashboard.design.applied'))
  else if (outcome === 'not-applicable') toast.error(t('dashboard.design.notApplicable'))
  else if (outcome === 'failed') toast.error(t('dashboard.design.applyFailed'))
}
</script>

<template>
  <section aria-labelledby="dashboard-design-heading">
    <SCard class="p-4">
      <div class="mb-3 flex items-start gap-2">
        <SparklesIcon
          class="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-muted)]"
          aria-hidden="true"
        />
        <div>
          <h2
            id="dashboard-design-heading"
            class="text-lg font-semibold text-[var(--color-fg)]"
          >
            {{ t('dashboard.design.title') }}
          </h2>
          <p class="text-sm text-[var(--color-muted)]">
            {{ t('dashboard.design.description') }}
          </p>
        </div>
      </div>

      <SQueryError
        v-if="design.error.value"
        :message="design.error.value.message"
        :retry-label="t('dashboard.retry')"
        @retry="design.refetch()"
      />

      <SLoadingSpinner
        v-else-if="design.isLoading.value"
        size="sm"
        data-testid="design-loading"
      />

      <div
        v-else-if="inFlight"
        class="flex items-center gap-2 text-sm text-[var(--color-muted)]"
        role="status"
        data-testid="design-in-flight"
      >
        <SLoadingSpinner size="sm" />
        {{ t(`dashboard.design.status.${run?.status}`) }}
      </div>

      <template v-else>
        <SAlert
          v-if="conflict"
          variant="warning"
          class="mb-3"
          data-testid="design-conflict"
        >
          {{ t('dashboard.design.conflict') }}
        </SAlert>
        <SAlert
          v-else-if="run?.status === 'failed' && !startingOver"
          variant="danger"
          class="mb-3"
          data-testid="design-failed"
        >
          {{ t(`dashboard.design.failure.${run.failure_reason ?? 'design_unavailable'}`) }}
        </SAlert>
        <SAlert
          v-else-if="run?.status === 'applied' && !startingOver"
          variant="success"
          class="mb-3"
          data-testid="design-applied"
        >
          {{ t('dashboard.design.appliedNote', {
            teacher: agentName(run.teacher.agent_id),
            peer: agentName(run.peer.agent_id),
          }) }}
        </SAlert>

        <DesignProposalView
          v-if="run?.status === 'succeeded' && !showForm"
          :run="run"
          :teacher-name="agentName(run.teacher.agent_id)"
          :peer-name="agentName(run.peer.agent_id)"
          :applying="design.applying.value"
          @apply="onApply"
          @start-over="startingOver = true"
        />

        <SQueryError
          v-if="showForm && pickerError"
          :message="pickerError.message"
          :retry-label="t('dashboard.retry')"
          data-testid="design-pickers-error"
          @retry="refetchPickers"
        />
        <DesignRunForm
          v-else-if="showForm"
          :agent-options="agentOptions"
          :type-options="typeOptions"
          :creating="design.creating.value"
          :can-go-back="startingOver"
          @submit="onSubmit"
          @back="startingOver = false"
        />
      </template>
    </SCard>
  </section>
</template>
