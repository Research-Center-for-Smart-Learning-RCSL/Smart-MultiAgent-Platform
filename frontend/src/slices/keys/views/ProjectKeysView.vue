<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useMyKeys } from '../composables/useMyKeys'
import { useProjectKeys } from '../composables/useProjectKeys'
import CapabilityChip from '../components/CapabilityChip.vue'
import UsageDashboard from '../components/UsageDashboard.vue'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const toast = useToast()
const route = useRoute()
const { confirm } = useConfirmDialog()
const projectId = computed(() => route.params.projectId as string)

const { keys: myKeys, reload: reloadMine } = useMyKeys()
const { carried, loading, error, reload, carry, withdraw } = useProjectKeys(
  () => projectId.value,
)

const activeTab = ref('carried')
const expandedKeyId = ref<string | null>(null)

const carriable = computed(() =>
  myKeys.value.filter((m) => !carried.value.some((c) => c.id === m.id)),
)

const tabs = computed(() => [
  { key: 'carried', label: t('keys.project.carried'), icon: KeyIcon, badge: String(carried.value.length) },
  { key: 'available', label: t('keys.project.carry'), icon: PlusCircleIcon, badge: String(carriable.value.length) },
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

onMounted(async () => {
  await Promise.all([reloadMine(), reload()])
})
watch(projectId, async () => {
  await Promise.all([reloadMine(), reload()])
})
</script>

<template>
  <main class="p-6">
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
          :data="carried"
          :loading="loading"
          row-key="id"
        >
          <template #cell-provider="{ row }">
            <CapabilityChip :provider="row.provider" />
          </template>

          <template #cell-masked_preview="{ row }">
            <code class="text-[13px] font-mono text-[var(--color-muted)]">{{ row.masked_preview }}</code>
          </template>

          <template #cell-test_status="{ row }">
            <SStatusBadge :status="row.test_status" />
          </template>

          <template #cell-usage="{ row }">
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

          <template #actions="{ row }">
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
      </template>

      <!-- Available Keys tab -->
      <template #tab-available>
        <STable
          :columns="availableColumns"
          :data="carriable"
          row-key="id"
        >
          <template #cell-provider="{ row }">
            <CapabilityChip :provider="row.provider" />
          </template>

          <template #cell-masked_preview="{ row }">
            <code class="text-[13px] font-mono text-[var(--color-muted)]">{{ row.masked_preview }}</code>
          </template>

          <template #cell-test_status="{ row }">
            <SStatusBadge :status="row.test_status" />
          </template>

          <template #actions="{ row }">
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
  </main>
</template>
