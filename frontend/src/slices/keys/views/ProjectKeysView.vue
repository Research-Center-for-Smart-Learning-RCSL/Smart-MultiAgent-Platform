<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  KeyIcon,
  PlusCircleIcon,
  ChartBarIcon,
  ArrowUturnLeftIcon,
  InboxIcon,
  CheckCircleIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STabs,
  STable,
  SButton,
  SStatusBadge,
  SEmptyState,
  SAlert,
  SPagination,
} from '@shared/ui'
import { useConfirmDialog, useToast, useClientPagination } from '@shared/composables'
import { useMyKeys } from '../composables/useMyKeys'
import { useProjectKeys } from '../composables/useProjectKeys'
import CapabilityChip from '../components/CapabilityChip.vue'
import UsageDashboard from '../components/UsageDashboard.vue'
import MaskedPreview from '../components/MaskedPreview.vue'
import type { ApiKey } from '../api/keys'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const toast = useToast()
const route = useRoute()
const { confirm } = useConfirmDialog()
const projectId = computed(() => route.params.projectId as string)

// Named apart from useProjectKeys' `loading` below: that one belongs to the
// carried query, and binding it to the available table would tie that table to
// an unrelated request.
const { keys: myKeys, loading: myKeysLoading } = useMyKeys()
const { carried, loading, error, carry, withdraw } = useProjectKeys(
  () => projectId.value,
)

const activeTab = ref('carried')
const expandedKeyId = ref<string | null>(null)

const { currentPage: carriedPage, totalPages: carriedTotalPages, paginatedItems: paginatedCarried, pageSize } = useClientPagination(carried)

const carriable = computed(() =>
  myKeys.value.filter((m) => !carried.value.some((c) => c.id === m.id)),
)

// Neither badge is rendered while its own query is in flight: both counts read
// an empty array until then, so a number would assert "0 keys" before anything
// is known. Each tab watches the query that actually feeds it - `carried` the
// project's carried list, `available` the user's own key list.
const tabs = computed(() => [
  {
    key: 'carried',
    label: t('keys.project.carried'),
    icon: KeyIcon,
    ...(loading.value ? {} : { badge: String(carried.value.length) }),
  },
  {
    key: 'available',
    label: t('keys.project.carry'),
    icon: PlusCircleIcon,
    ...(myKeysLoading.value ? {} : { badge: String(carriable.value.length) }),
  },
])

const carriedColumns = computed<Column[]>(() => [
  { key: 'provider', label: t('keys.project.provider'), width: '140px' },
  { key: 'name', label: t('keys.project.name') },
  { key: 'masked_preview', label: t('keys.project.preview'), width: '110px' },
  { key: 'test_status', label: t('keys.project.status'), width: '100px', align: 'center' },
  { key: 'usage', label: t('keys.project.usage'), width: '120px', align: 'center' },
  { key: 'actions', label: '', width: '100px', align: 'right' },
])

const availableColumns = computed<Column[]>(() => [
  { key: 'provider', label: t('keys.project.provider'), width: '140px' },
  { key: 'name', label: t('keys.project.name') },
  { key: 'masked_preview', label: t('keys.project.preview'), width: '110px' },
  { key: 'test_status', label: t('keys.project.status'), width: '100px', align: 'center' },
  { key: 'actions', label: '', width: '100px', align: 'right' },
])

// ApiKey is a TS `interface` (no index signature), so it doesn't structurally
// satisfy STable's `T extends Record<string, unknown>` constraint; map to fresh
// objects so STable's generic resolves to the real row shape instead of falling
// back to `Record<string, unknown>`.
const tableCarried = computed(() => paginatedCarried.value.map((k) => ({ ...k })))
const tableCarriable = computed(() => carriable.value.map((k) => ({ ...k })))

function toggleUsage(keyId: string) {
  expandedKeyId.value = expandedKeyId.value === keyId ? null : keyId
}

async function onWithdraw(keyId: string) {
  const ok = await confirm({
    title: t('keys.project.withdrawTitle'),
    message: t('keys.project.withdrawBody'),
    confirmLabel: t('keys.project.withdraw'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await withdraw(keyId)
    toast.success(t('keys.project.withdrawn'))
    if (expandedKeyId.value === keyId) expandedKeyId.value = null
  } catch {
    toast.error(t('keys.project.withdrawFailed'))
  }
}

async function onCarry(keyId: string) {
  try {
    await carry(keyId)
    toast.success(t('keys.project.carried'))
  } catch {
    toast.error(t('keys.project.carryFailed'))
  }
}

</script>

<template>
  <div>
    <SPageHeader :title="$t('keys.project.title')">
      <template #description>
        {{ $t('keys.project.description') }}
      </template>
    </SPageHeader>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-4"
    >
      {{ $t('keys.project.fetchError') }}
    </SAlert>

    <STabs
      v-model="activeTab"
      :tabs="tabs"
      class="mt-6"
    >
      <!-- Carried Keys tab -->
      <template #tab-carried>
        <STable
          :columns="carriedColumns"
          :data="tableCarried"
          :loading="loading"
          row-key="id"
        >
          <template #cell-provider="{ row }: { row: ApiKey }">
            <CapabilityChip :provider="row.provider" />
          </template>

          <template #cell-masked_preview="{ row }: { row: ApiKey }">
            <MaskedPreview :value="row.masked_preview" />
          </template>

          <template #cell-test_status="{ row }: { row: ApiKey }">
            <SStatusBadge :status="row.test_status" />
          </template>

          <template #cell-usage="{ row }: { row: ApiKey }">
            <SButton
              variant="ghost"
              size="sm"
              icon-only
              :aria-label="$t('keys.project.usage')"
              @click="toggleUsage(row.id)"
            >
              <ChartBarIcon class="w-4 h-4" />
            </SButton>
          </template>

          <template #actions="{ row }: { row: ApiKey }">
            <SButton
              variant="ghost"
              size="sm"
              @click="onWithdraw(row.id)"
            >
              <template #icon-left>
                <ArrowUturnLeftIcon class="w-4 h-4" />
              </template>
              {{ $t('keys.project.withdraw') }}
            </SButton>
          </template>

          <template #empty>
            <SEmptyState
              :icon="InboxIcon"
              :title="$t('keys.project.emptyCarried')"
              :text="$t('keys.project.emptyCarriedDescription')"
            >
              <template #action>
                <SButton
                  variant="primary"
                  @click="activeTab = 'available'"
                >
                  {{ $t('keys.project.carry') }}
                </SButton>
              </template>
            </SEmptyState>
          </template>
        </STable>

        <!-- Inline usage expansion -->
        <div
          v-if="expandedKeyId"
          class="border border-[var(--color-border)] border-t-0 bg-[var(--color-surface)] rounded-b-[var(--radius-md)] px-6 py-4"
        >
          <UsageDashboard
            :project-id="projectId"
            :key-id="expandedKeyId"
            compact
          />
        </div>
        <SPagination
          v-if="carried.length > pageSize"
          :page="carriedPage"
          :total-pages="carriedTotalPages"
          :total-items="carried.length"
          :page-size="pageSize"
          class="mt-4"
          @update:page="carriedPage = $event"
        />
      </template>

      <!-- Available Keys tab -->
      <template #tab-available>
        <STable
          :columns="availableColumns"
          :data="tableCarriable"
          :loading="myKeysLoading"
          row-key="id"
        >
          <template #cell-provider="{ row }: { row: ApiKey }">
            <CapabilityChip :provider="row.provider" />
          </template>

          <template #cell-masked_preview="{ row }: { row: ApiKey }">
            <MaskedPreview :value="row.masked_preview" />
          </template>

          <template #cell-test_status="{ row }: { row: ApiKey }">
            <SStatusBadge :status="row.test_status" />
          </template>

          <template #actions="{ row }: { row: ApiKey }">
            <SButton
              variant="primary"
              size="sm"
              @click="onCarry(row.id)"
            >
              {{ $t('keys.project.carryAction') }}
            </SButton>
          </template>

          <template #empty>
            <SEmptyState
              :icon="CheckCircleIcon"
              :title="$t('keys.project.emptyAvailable')"
              :text="$t('keys.project.emptyAvailableDescription')"
            >
              <template #action>
                <SButton
                  variant="secondary"
                  :to="{ name: 'keys.list' }"
                  as="router-link"
                >
                  {{ $t('keys.form.submit') }}
                </SButton>
              </template>
            </SEmptyState>
          </template>
        </STable>
      </template>
    </STabs>
  </div>
</template>
