<template>
  <section class="admin-activities">
    <SPageHeader
      :title="$t('admin.activities.title')"
      :subtitle="$t('admin.activities.subtitle')"
    />

    <h2 class="mt-6 text-sm font-semibold text-[var(--color-fg)]">
      {{ $t('admin.activities.activeHeading') }}
    </h2>
    <p class="mt-1 text-xs text-[var(--color-muted)]">
      {{ $t('admin.activities.activeHelp') }}
    </p>

    <SQueryError
      v-if="activationsQuery.isError.value"
      class="mt-3"
      :message="$t('admin.common.loadError')"
      :retry-label="$t('admin.common.retry')"
      @retry="activationsQuery.refetch()"
    />

    <STable
      v-else
      class="mt-3"
      :columns="activationColumns"
      :data="activationRows"
      :loading="activationsQuery.isPending.value"
      :loading-label="$t('admin.common.loading')"
      row-key="id"
    >
      <template #cell-chatroom_name="{ row }">
        {{ row.chatroom_name ?? row.chatroom_id }}
      </template>

      <template #cell-activity_type_name="{ row }">
        {{ row.activity_type_name ?? row.activity_type_id }}
      </template>

      <template #cell-created_at="{ row }">
        {{ formatDateTime(row.created_at) }}
      </template>

      <template #cell-audit="{ row }">
        <router-link
          class="text-[var(--color-accent)] underline"
          :to="auditLinkFor('activity_activation', row.id)"
        >
          {{ $t('admin.activities.viewAudit') }}
        </router-link>
      </template>

      <template #empty>
        <SEmptyState
          :icon="PlayCircleIcon"
          :text="$t('admin.activities.activeEmpty')"
        />
      </template>
    </STable>

    <p
      v-if="activationsTruncated"
      class="mt-2 text-xs text-[var(--color-warning)]"
      role="status"
    >
      {{ $t('admin.activities.truncated', { count: PAGE_LIMIT }) }}
    </p>

    <h2 class="mt-8 text-sm font-semibold text-[var(--color-fg)]">
      {{ $t('admin.activities.typesHeading') }}
    </h2>
    <p class="mt-1 text-xs text-[var(--color-muted)]">
      {{ $t('admin.activities.typesHelp') }}
    </p>

    <SQueryError
      v-if="typesQuery.isError.value"
      class="mt-3"
      :message="$t('admin.common.loadError')"
      :retry-label="$t('admin.common.retry')"
      @retry="typesQuery.refetch()"
    />

    <STable
      v-else
      class="mt-3"
      :columns="typeColumns"
      :data="typeRows"
      :loading="typesQuery.isPending.value"
      :loading-label="$t('admin.common.loading')"
      row-key="id"
    >
      <template #cell-project_name="{ row }">
        {{ row.project_name ?? row.project_id }}
      </template>

      <template #cell-expose_payload_to_agent="{ row }">
        <SBadge
          size="sm"
          :variant="row.expose_payload_to_agent ? 'warning' : 'neutral'"
        >
          {{ row.expose_payload_to_agent ? $t('admin.activities.on') : $t('admin.activities.off') }}
        </SBadge>
      </template>

      <template #cell-echo_includes_content="{ row }">
        <SBadge
          size="sm"
          :variant="row.echo_includes_content ? 'warning' : 'neutral'"
        >
          {{ row.echo_includes_content ? $t('admin.activities.on') : $t('admin.activities.off') }}
        </SBadge>
      </template>

      <template #cell-retention_days="{ row }">
        {{ row.retention_days ?? $t('admin.activities.retentionUnset') }}
      </template>

      <template #cell-audit="{ row }">
        <router-link
          class="text-[var(--color-accent)] underline"
          :to="auditLinkFor('activity_type', row.id)"
        >
          {{ $t('admin.activities.viewAudit') }}
        </router-link>
      </template>

      <template #empty>
        <SEmptyState
          :icon="ClipboardDocumentListIcon"
          :text="$t('admin.activities.typesEmpty')"
        />
      </template>
    </STable>

    <p
      v-if="typesTruncated"
      class="mt-2 text-xs text-[var(--color-warning)]"
      role="status"
    >
      {{ $t('admin.activities.truncated', { count: PAGE_LIMIT }) }}
    </p>
  </section>
</template>

<script setup lang="ts">
// Read-only platform-wide activity governance view (R30.31). It grants no
// create/edit/deactivate capability by design — an admin who needs to change a
// type does it through the owning project.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ClipboardDocumentListIcon, PlayCircleIcon } from '@heroicons/vue/24/outline'
import { SPageHeader, STable, SQueryError, SEmptyState, SBadge } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'
import { formatDateTime } from '@shared/utils/datetime'
import { useQuery } from '@tanstack/vue-query'
import type { RouteLocationRaw } from 'vue-router'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { AdminActivityActivationRow, AdminActivityTypeRow } from '../types'

const { t } = useI18n()

const activationColumns = computed<Column[]>(() => [
  { key: 'chatroom_name', label: t('admin.activities.room') },
  { key: 'activity_type_name', label: t('admin.activities.activity') },
  { key: 'created_at', label: t('admin.activities.startedAt'), width: '170px' },
  { key: 'audit', label: '', width: '110px' },
])

const typeColumns = computed<Column[]>(() => [
  { key: 'project_name', label: t('admin.activities.project') },
  { key: 'name', label: t('admin.activities.name') },
  { key: 'key', label: t('admin.activities.key') },
  { key: 'validator_kind', label: t('admin.activities.validator'), width: '120px' },
  { key: 'expose_payload_to_agent', label: t('admin.activities.agentVisibility'), width: '130px' },
  { key: 'echo_includes_content', label: t('admin.activities.roomVisibility'), width: '130px' },
  { key: 'retention_days', label: t('admin.activities.retention'), width: '110px' },
  { key: 'audit', label: '', width: '110px' },
])

// The endpoints are keyset-paginated but this view fetches a single page, so it
// asks for the server maximum and says so when the page comes back full. A
// governance view that silently showed the newest 50 of 300 types would be worse
// than no view at all — the admin would believe they had seen everything.
// Paging through (Load More, as AdminAuditView does) is the follow-up.
const PAGE_LIMIT = 200

const typesQuery = useQuery({
  queryKey: adminKeys.activityTypes(),
  queryFn: () => adminApi.listAllActivityTypes({ limit: PAGE_LIMIT }),
})

const activationsQuery = useQuery({
  queryKey: adminKeys.activityActivations(),
  queryFn: () => adminApi.listAllActiveActivations({ limit: PAGE_LIMIT }),
})

const typesTruncated = computed(() => typeRows.value.length >= PAGE_LIMIT)
const activationsTruncated = computed(() => activationRows.value.length >= PAGE_LIMIT)

/** Deep-link into the audit view pre-filtered to one resource. The audit view
 *  reads these off `route.query`. */
function auditLinkFor(resourceType: string, resourceId: string): RouteLocationRaw {
  return {
    name: 'admin.audit',
    query: { resource_type: resourceType, resource_id: resourceId },
  }
}

// STable's generic constrains T to Record<string, unknown>; the row types have no
// index signature by design, so intersect one in for this cast only (the runtime
// shape is unchanged).
type TypeRow = AdminActivityTypeRow & Record<string, unknown>
type ActivationRow = AdminActivityActivationRow & Record<string, unknown>

const typeRows = computed<TypeRow[]>(() => (typesQuery.data.value ?? []) as unknown as TypeRow[])
const activationRows = computed<ActivationRow[]>(
  () => (activationsQuery.data.value ?? []) as unknown as ActivationRow[],
)
</script>
