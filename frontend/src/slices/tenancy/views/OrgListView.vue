<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SButton, STable, SModal, SFormField, SInput,
  SBadge, SEmptyState, SAlert,
} from '@shared/ui'
import { useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { BuildingOffice2Icon, PlusIcon, ChevronRightIcon } from '@heroicons/vue/24/outline'
import { orgsApi, type Org } from '../api/orgs'
import { tenancyKeys } from '../queries'
import { formatDate } from '../utils/formatters'

const { t } = useI18n()
const router = useRouter()
const toast = useToast()
const qc = useQueryClient()

const { data: orgs, isLoading, isError, refetch } = useQuery({
  queryKey: tenancyKeys.orgs(),
  queryFn: () => orgsApi.list().then(r => r.data),
})

const showCreate = ref(false)
const createName = ref('')
const createError = ref<string | null>(null)
const creating = ref(false)

const columns = computed(() => [
  { key: 'name', label: t('tenancy.breadcrumb.organizations'), sortable: true },
  { key: 'role', label: t('tenancy.role.member'), sortable: true, width: '120px' },
  { key: 'created_at', label: t('tenancy.settings.created'), sortable: true, width: '140px' },
  { key: 'arrow', label: '', width: '40px' },
])

function roleBadgeVariant(org: Org): 'info' | 'neutral' {
  return org.creator_user_id ? 'info' : 'neutral'
}

function openCreate(): void {
  createName.value = ''
  createError.value = null
  showCreate.value = true
}

async function submitCreate(): Promise<void> {
  const trimmed = createName.value.trim()
  if (!trimmed || creating.value) return

  creating.value = true
  createError.value = null
  try {
    await orgsApi.create(trimmed)
    showCreate.value = false
    qc.invalidateQueries({ queryKey: tenancyKeys.orgs() })
    toast.success(t('tenancy.org.created'))
  } catch (e: unknown) {
    if (isProblemWithType(e, '/tenancy/name-taken')) {
      createError.value = t('tenancy.org.nameTaken')
    } else {
      createError.value = t('tenancy.org.loadError')
    }
  } finally {
    creating.value = false
  }
}

function onRowClick(row: Org): void {
  router.push({ name: 'tenancy.orgDetail', params: { id: row.id } })
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
])
</script>

<template>
  <div>
    <SPageHeader
      :title="t('tenancy.breadcrumb.organizations')"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <SButton
          variant="primary"
          @click="openCreate"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('tenancy.org.createTitle') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      v-if="isError"
      variant="danger"
    >
      {{ t('tenancy.org.loadError') }}
      <template #actions>
        <SButton
          variant="secondary"
          size="sm"
          @click="() => refetch()"
        >
          {{ t('app.confirm') }}
        </SButton>
      </template>
    </SAlert>

    <STable
      v-else
      :columns="columns"
      :data="orgs ?? []"
      :loading="isLoading"
      row-key="id"
      @row-click="onRowClick"
    >
      <template #cell-name="{ row }">
        <span class="org-name">{{ row.name }}</span>
      </template>

      <template #cell-role="{ row }">
        <SBadge :variant="roleBadgeVariant(row as Org)">
          {{ row.creator_user_id ? t('tenancy.role.originalCreator') : t('tenancy.role.owner') }}
        </SBadge>
      </template>

      <template #cell-created_at="{ row }">
        {{ formatDate(row.created_at) }}
      </template>

      <template #cell-arrow>
        <ChevronRightIcon class="w-4 h-4 arrow-icon" />
      </template>

      <template #empty>
        <SEmptyState
          :icon="BuildingOffice2Icon"
          :title="t('tenancy.org.empty')"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="openCreate"
            >
              {{ t('tenancy.org.createTitle') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <SModal
      :open="showCreate"
      :title="t('tenancy.org.createTitle')"
      size="sm"
      @close="showCreate = false"
    >
      <form @submit.prevent="submitCreate">
        <SFormField
          :label="t('tenancy.breadcrumb.organizations')"
          name="orgName"
          :error="createError ?? undefined"
          :help="t('tenancy.org.nameHelp')"
          required
        >
          <SInput
            v-model="createName"
            :error="!!createError"
            :disabled="creating"
          />
        </SFormField>
      </form>

      <template #footer>
        <SButton
          variant="secondary"
          :disabled="creating"
          @click="showCreate = false"
        >
          {{ t('app.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          :loading="creating"
          :disabled="creating || !createName.trim()"
          @click="submitCreate"
        >
          {{ t('tenancy.org.createTitle') }}
        </SButton>
      </template>
    </SModal>
  </div>
</template>

<style scoped>
.org-name {
  font-weight: 500;
}

.arrow-icon {
  color: var(--color-muted);
}
</style>
