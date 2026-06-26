<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { PlusIcon, TrashIcon, ShieldCheckIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SCard,
  STable,
  SFormField,
  SInput,
  SButton,
  SAlert,
  SEmptyState,
} from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { projectsApi, tenancyKeys } from '@slices/tenancy'
import { agentsApi, type EgressAllowlistEntry } from '../api'
import { agentKeys } from '../queries'
import { useProjectBreadcrumbs } from '../composables/useProjectBreadcrumbs'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const toast = useToast()
const { confirm } = useConfirmDialog()
const session = useSessionStore()

const { breadcrumbs } = useProjectBreadcrumbs(projectId, [
  { label: t('agents.breadcrumb.egressAllowlist') },
])

const isAdmin = computed(() => session.me?.is_admin === true)

const membersQuery = useQuery({
  queryKey: computed(() => tenancyKeys.projectMembers(projectId)),
  queryFn: () => projectsApi.listMembers(projectId).then((r) => r.data),
})

const isOwner = computed(() => {
  const me = session.me
  if (!me || !membersQuery.data.value) return false
  const membership = membersQuery.data.value.find((m) => m.user_id === me.id)
  return membership?.role === 'owner'
})

const allowlistQuery = useQuery({
  queryKey: agentKeys.egressAllowlist(projectId),
  queryFn: async () => (await agentsApi.listEgressAllowlist(projectId)).data,
})

const entries = computed<EgressAllowlistEntry[]>(() => allowlistQuery.data.value ?? [])
const loading = computed(() => allowlistQuery.isLoading.value)
const error = computed(() => allowlistQuery.error.value)

const RFC_1123 = /^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/

const addSchema = computed(() =>
  toTypedSchema(
    z.object({
      hostname: z.string().trim().min(1).max(253)
        .regex(RFC_1123, { message: t('agents.egress.hostnameInvalid') }),
      note: z.string().trim().max(500).nullable().default(null),
    }),
  ),
)
type AddInput = { hostname: string; note: string | null }

const { handleSubmit, errors, defineField, resetForm, setErrors } = useForm<AddInput>({
  validationSchema: addSchema,
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
  const ok = await confirm({
    title: t('agents.egress.removeTitle'),
    message: t('agents.egress.removeConfirm', { host: entry.hostname }),
    variant: 'warning',
  })
  if (!ok) return
  removeMutation.mutate(entry.hostname)
}

function formatRelativeTime(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (days < 1) return t('agents.egress.addedToday')
  if (days < 30) return t('agents.egress.addedDaysAgo', { days })
  return new Date(iso).toLocaleDateString()
}

const columns = computed<Column[]>(() => {
  const cols: Column[] = [
    { key: 'hostname', label: t('agents.egress.colHost') },
    { key: 'note', label: t('agents.egress.colNote'), width: '200px' },
  ]
  if (isAdmin.value) {
    cols.push({ key: 'added_by_user_id', label: t('agents.egress.colAddedBy'), width: '140px' })
  }
  cols.push(
    { key: 'added_at', label: t('agents.egress.colAdded'), width: '120px' },
    { key: 'actions', label: '', width: '48px', align: 'right' },
  )
  return cols
})
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="t('agents.egress.title')"
      :breadcrumbs="breadcrumbs"
    />

    <SAlert
      variant="info"
      class="mt-4"
    >
      {{ t('agents.egress.infoAlert') }}
    </SAlert>

    <SCard
      v-if="isOwner || membersQuery.isPending.value"
      class="mt-6"
    >
      <form
        class="flex flex-col gap-3 md:flex-row md:items-end"
        @submit.prevent="onSubmit"
      >
        <div class="flex-[2]">
          <SFormField
            :label="t('agents.egress.hostname')"
            name="hostname"
            :error="errors.hostname"
            required
          >
            <SInput
              v-model="hostname"
              :placeholder="t('agents.egress.hostnamePlaceholder')"
              :error="!!errors.hostname"
            />
          </SFormField>
        </div>
        <div class="flex-1">
          <SFormField
            :label="t('agents.egress.note')"
            name="note"
            :error="errors.note"
          >
            <SInput v-model="note" />
          </SFormField>
        </div>
        <SButton
          variant="primary"
          type="submit"
          :loading="addMutation.isPending.value"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('agents.egress.add') }}
        </SButton>
      </form>
    </SCard>

    <p
      v-if="!isOwner && !membersQuery.isPending.value"
      class="mt-6 text-sm text-[var(--color-muted)]"
    >
      {{ t('agents.egress.ownerOnly') }}
    </p>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-4"
    >
      {{ t('agents.egress.addFailed') }}
      <template #actions>
        <SButton
          variant="ghost"
          size="sm"
          @click="allowlistQuery.refetch()"
        >
          {{ t('agents.detail.reload') }}
        </SButton>
      </template>
    </SAlert>

    <STable
      :columns="columns"
      :data="entries"
      :loading="loading"
      row-key="id"
      class="mt-6"
    >
      <template #cell-hostname="{ row }">
        <span class="font-mono text-sm">{{ row.hostname }}</span>
      </template>

      <template #cell-note="{ row }">
        <span
          v-if="row.note"
          class="truncate max-w-[200px] inline-block"
        >{{ row.note }}</span>
        <span
          v-else
          class="text-[var(--color-muted)]"
        >--</span>
      </template>

      <template
        v-if="isAdmin"
        #cell-added_by_user_id="{ row }"
      >
        <span
          v-if="row.added_by_user_id"
          class="font-mono text-sm truncate max-w-[140px] inline-block"
        >{{ row.added_by_user_id }}</span>
        <span
          v-else
          class="text-[var(--color-muted)]"
        >--</span>
      </template>

      <template #cell-added_at="{ row }">
        {{ formatRelativeTime(row.added_at) }}
      </template>

      <template #actions="{ row }">
        <SButton
          v-if="isOwner"
          variant="ghost"
          icon-only
          size="sm"
          :aria-label="t('agents.egress.remove')"
          @click="confirmRemove(row)"
        >
          <TrashIcon class="w-4 h-4 text-[var(--color-danger)]" />
        </SButton>
      </template>

      <template #empty>
        <SEmptyState
          :icon="ShieldCheckIcon"
          :title="t('agents.egress.emptyTitle')"
          :text="t('agents.egress.emptyDescription')"
        />
      </template>
    </STable>
  </main>
</template>
