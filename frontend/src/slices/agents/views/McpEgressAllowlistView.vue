<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { SCard, SFormField, SPageHeader } from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import { agentsApi, type EgressAllowlistEntry } from '../api'
import { agentKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const toast = useToast()
const { confirm } = useConfirmDialog()

const allowlistQuery = useQuery({
  queryKey: agentKeys.egressAllowlist(projectId),
  queryFn: async () => (await agentsApi.listEgressAllowlist(projectId)).data,
})

// Mirrors backend AllowlistAddIn (hostname 1..253, optional note ≤500).
const addSchema = z.object({
  hostname: z.string().trim().min(1).max(253),
  note: z.string().trim().max(500).nullable().default(null),
})
type AddInput = z.infer<typeof addSchema>

const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm<AddInput>({
  validationSchema: toTypedSchema(addSchema),
  initialValues: { hostname: '', note: null },
})
const [hostname] = defineField('hostname')
const [note] = defineField('note')

const { applyServerErrors } = useServerErrors(setErrors)

const addMutation = useMutation({
  mutationFn: async (payload: AddInput) =>
    (await agentsApi.addEgressAllowlistEntry(projectId, payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.egressAllowlist(projectId) })
    resetForm()
    toast.success(t('agents.egress.added'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.egress.addFailed'))
  },
})

const onSubmit = handleSubmit((values) => addMutation.mutate(values))

const removeMutation = useMutation({
  mutationFn: (host: string) => agentsApi.removeEgressAllowlistEntry(projectId, host),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.egressAllowlist(projectId) })
    toast.success(t('agents.egress.removed'))
  },
  onError: () => toast.error(t('agents.egress.removeFailed')),
})

async function confirmRemove(entry: EgressAllowlistEntry): Promise<void> {
  const ok = await confirm({ title: t('agents.egress.removeTitle'), message: t('agents.egress.removeConfirm', { host: entry.hostname }), variant: 'warning' })
  if (!ok) return
  removeMutation.mutate(entry.hostname)
}
</script>

<template>
  <section class="egress px-4 py-4 sm:p-6">
    <SPageHeader :title="t('agents.egress.title')" />
    <p class="egress__subtitle mb-4">
      {{ t('agents.egress.subtitle') }}
    </p>

    <SCard class="max-w-[480px] mb-6">
      <form
        @submit.prevent="onSubmit"
      >
        <SFormField
          :label="t('agents.egress.hostname')"
          name="hostname"
          :error="errors.hostname"
          required
        >
          <input
            id="hostname"
            v-model="hostname"
            :placeholder="t('agents.egress.hostnamePlaceholder')"
            :aria-invalid="!!errors.hostname"
          >
        </SFormField>
        <SFormField
          :label="t('agents.egress.note')"
          name="note"
          :error="errors.note"
        >
          <input
            id="note"
            v-model="note"
          >
        </SFormField>
        <button
          type="submit"
          class="btn btn-primary"
          :disabled="addMutation.isPending.value"
        >
          {{ t('agents.egress.add') }}
        </button>
      </form>
    </SCard>

    <p v-if="allowlistQuery.isLoading.value">
      {{ t('agents.egress.loading') }}
    </p>
    <div
      v-else
      class="overflow-x-auto"
    >
      <table class="table">
        <thead>
          <tr>
            <th scope="col">
              {{ t('agents.egress.colHost') }}
            </th>
            <th scope="col">
              {{ t('agents.egress.colNote') }}
            </th>
            <th scope="col">
              {{ t('agents.egress.colActions') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="e in allowlistQuery.data.value ?? []"
            :key="e.id"
          >
            <td class="egress__host">
              {{ e.hostname }}
            </td>
            <td>{{ e.note ?? '—' }}</td>
            <td>
              <button
                class="btn btn-danger"
                type="button"
                @click="confirmRemove(e)"
              >
                {{ t('agents.egress.remove') }}
              </button>
            </td>
          </tr>
          <tr v-if="(allowlistQuery.data.value ?? []).length === 0">
            <td colspan="3">
              {{ t('agents.egress.empty') }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.egress__subtitle {
  color: var(--color-muted);
}
.egress__host {
  font-family: var(--font-mono, monospace);
}
</style>
