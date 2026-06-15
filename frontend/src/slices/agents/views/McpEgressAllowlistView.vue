<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { ElMessageBox } from 'element-plus'

import { FormField } from '@shared/ui'
import { useServerErrors, useToast } from '@shared/composables'
import { agentsApi, type EgressAllowlistEntry } from '../api'
import { agentKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const toast = useToast()

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
  try {
    await ElMessageBox.confirm(
      t('agents.egress.removeConfirm', { host: entry.hostname }),
      t('agents.egress.removeTitle'),
      { type: 'warning' },
    )
  } catch {
    return // dismissed
  }
  removeMutation.mutate(entry.hostname)
}
</script>

<template>
  <section class="egress p-6">
    <h1 class="text-xl font-semibold mb-1">
      {{ t('agents.egress.title') }}
    </h1>
    <p class="egress__subtitle mb-4">
      {{ t('agents.egress.subtitle') }}
    </p>

    <form
      class="egress__form"
      @submit.prevent="onSubmit"
    >
      <FormField
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
      </FormField>
      <FormField
        :label="t('agents.egress.note')"
        name="note"
        :error="errors.note"
      >
        <input
          id="note"
          v-model="note"
        >
      </FormField>
      <button
        type="submit"
        class="btn btn-primary"
        :disabled="addMutation.isPending.value"
      >
        {{ t('agents.egress.add') }}
      </button>
    </form>

    <p v-if="allowlistQuery.isLoading.value">
      {{ t('agents.egress.loading') }}
    </p>
    <table
      v-else
      class="egress__table"
    >
      <thead>
        <tr>
          <th>{{ t('agents.egress.colHost') }}</th>
          <th>{{ t('agents.egress.colNote') }}</th>
          <th>{{ t('agents.egress.colActions') }}</th>
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
  </section>
</template>

<style scoped>
.egress__subtitle {
  color: var(--color-muted);
}
.egress__form {
  max-width: 480px;
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.egress__table {
  width: 100%;
  border-collapse: collapse;
}
.egress__table th,
.egress__table td {
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.egress__host {
  font-family: var(--font-mono, monospace);
}
</style>
