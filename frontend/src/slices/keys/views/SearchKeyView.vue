<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  PlusIcon,
  ArrowPathIcon,
  TrashIcon,
  MagnifyingGlassIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SRadio,
  SStatusBadge,
  SBadge,
  SDropdown,
  SEmptyState,
  SAlert,
  STooltip,
  SPagination,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useSearchKeys } from '../composables/useSearchKeys'
import SearchKeyUploadForm from '../components/SearchKeyUploadForm.vue'
import type { SearchProvider } from '../api/search-keys'
import MaskedPreview from '../components/MaskedPreview.vue'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const { confirm } = useConfirmDialog()
const projectId = computed(() => route.params.projectId as string)
const { keys, error, reload, upload, retest, activate, remove } = useSearchKeys(
  () => projectId.value,
)

const showAdd = ref(false)
const uploadFormRef = ref<InstanceType<typeof SearchKeyUploadForm> | null>(null)
const currentPage = ref(1)
const pageSize = 20

const totalPages = computed(() => Math.max(1, Math.ceil(keys.value.length / pageSize)))
const paginatedKeys = computed(() => {
  const start = (currentPage.value - 1) * pageSize
  return keys.value.slice(start, start + pageSize)
})

const columns = computed<Column[]>(() => [
  { key: 'provider', label: t('keys.search.provider'), width: '160px' },
  { key: 'masked_preview', label: t('keys.search.preview'), width: '110px' },
  { key: 'test_status', label: t('keys.search.status'), width: '120px', align: 'center' },
  { key: 'last_test_at', label: t('keys.search.lastTested'), width: '140px' },
  { key: 'is_active', label: t('keys.search.active'), width: '80px', align: 'center' },
  { key: 'actions', label: '', width: '80px', align: 'right' },
])

const activeKeyId = computed(() => keys.value.find((k) => k.is_active)?.id ?? '')

const PROVIDER_DISPLAY: Record<SearchProvider, string> = {
  brave: 'Brave',
  serper: 'Serper',
  tavily: 'Tavily',
  google_cse: 'Google CSE',
}

const actionItems = computed(() => [
  { key: 'retest', label: t('keys.search.retest'), icon: ArrowPathIcon },
  { key: 'delete', label: t('keys.search.delete'), icon: TrashIcon, danger: true },
])

async function onUpload(payload: { provider: SearchProvider; secret: string; config: Record<string, unknown> }) {
  try {
    await upload(payload.provider, payload.secret, payload.config)
    showAdd.value = false
    uploadFormRef.value?.reset()
    toast.success(t('keys.search.uploaded'))
  } catch {
    uploadFormRef.value?.setError(t('keys.search.uploadFailed'))
  }
}

async function onRetest(id: string) {
  try {
    await retest(id)
    const key = keys.value.find((k) => k.id === id)
    if (key?.test_status === 'ok') {
      toast.success(t('keys.search.retestValid'))
    } else if (key?.test_status === 'failed') {
      toast.warning(t('keys.search.retestInvalid'))
    }
  } catch {
    toast.error(t('keys.search.retestFailed'))
  }
}

async function onActivate(id: string) {
  try {
    await activate(id)
  } catch {
    toast.error(t('keys.search.activateFailed'))
    await reload()
  }
}

async function onDelete(id: string) {
  const key = keys.value.find((k) => k.id === id)
  const provider = PROVIDER_DISPLAY[key?.provider ?? 'brave']
  const message = key?.is_active
    ? `${t('keys.search.deleteBody', { provider })}\n\n${t('keys.search.deleteActiveWarning')}`
    : t('keys.search.deleteBody', { provider })
  const ok = await confirm({
    title: t('keys.search.deleteTitle'),
    message,
    confirmLabel: t('keys.search.delete'),
    variant: 'error',
  })
  if (!ok) return
  try {
    await remove(id)
    toast.success(t('keys.search.deleted'))
  } catch {
    toast.error(t('keys.search.deleteFailed'))
  }
}

function onAction(key: string, row: { id: string }) {
  if (key === 'retest') {
    void onRetest(row.id)
  } else if (key === 'delete') {
    void onDelete(row.id)
  }
}

</script>

<template>
  <main class="p-6">
    <SPageHeader :title="$t('keys.search.title')">
      <template #description>
        {{ $t('keys.search.description') }}
      </template>
      <template #actions>
        <SButton
          variant="primary"
          @click="showAdd = true"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ $t('keys.search.add') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-4"
    >
      {{ $t('keys.search.fetchError') }}
    </SAlert>

    <STable
      :columns="columns"
      :data="paginatedKeys"
      row-key="id"
      class="mt-6"
    >
      <template #cell-provider="{ row }">
        <div>
          <span class="text-sm font-medium">{{ PROVIDER_DISPLAY[row.provider as SearchProvider] }}</span>
          <div
            v-if="row.provider === 'google_cse' && row.config?.cx"
            class="text-xs text-[var(--color-muted)] mt-0.5"
          >
            {{ $t('keys.search.cxPrefix', { value: String(row.config.cx).slice(0, 20) }) }}
          </div>
          <div
            v-if="row.provider === 'tavily' && row.config?.search_depth"
            class="mt-0.5"
          >
            <SBadge
              variant="neutral"
              size="sm"
            >
              {{ row.config.search_depth }}
            </SBadge>
          </div>
        </div>
      </template>

      <template #cell-masked_preview="{ row }">
        <MaskedPreview :value="row.masked_preview" />
      </template>

      <template #cell-test_status="{ row }">
        <SStatusBadge :status="row.test_status" />
        <small
          v-if="row.test_status === 'failed' && row.test_error"
          class="block text-xs text-[var(--color-muted)] truncate max-w-[60ch]"
          :title="row.test_error"
        >
          {{ row.test_error }}
        </small>
      </template>

      <template #cell-last_test_at="{ row }">
        <span class="text-sm text-[var(--color-muted)]">
          {{ row.last_test_at ? new Date(row.last_test_at).toLocaleString() : $t('keys.search.never') }}
        </span>
      </template>

      <template #cell-is_active="{ row }">
        <STooltip
          v-if="row.test_status === 'failed'"
          :content="$t('keys.search.cannotActivateInvalid')"
          placement="top"
        >
          <SRadio
            :model-value="activeKeyId"
            :value="row.id"
            name="active-search-key"
            disabled
            :data-testid="`activate-${row.id}`"
          />
        </STooltip>
        <SRadio
          v-else
          :model-value="activeKeyId"
          :value="row.id"
          name="active-search-key"
          :data-testid="`activate-${row.id}`"
          @update:model-value="onActivate(row.id)"
        />
      </template>

      <template #actions="{ row }">
        <SDropdown
          :items="actionItems"
          placement="bottom-end"
          @select="onAction($event, row)"
        >
          <template #trigger>
            <SButton
              variant="ghost"
              icon-only
              :aria-label="$t('keys.list.actions')"
            >
              <EllipsisVerticalIcon class="w-4 h-4" />
            </SButton>
          </template>
        </SDropdown>
      </template>

      <template #empty>
        <SEmptyState
          :icon="MagnifyingGlassIcon"
          :title="$t('keys.search.emptyTitle')"
          :text="$t('keys.search.emptyDescription')"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="showAdd = true"
            >
              {{ $t('keys.search.add') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <SPagination
      v-if="keys.length > pageSize"
      :page="currentPage"
      :total-pages="totalPages"
      :total-items="keys.length"
      :page-size="pageSize"
      class="mt-4"
      @update:page="currentPage = $event"
    />

    <SearchKeyUploadForm
      ref="uploadFormRef"
      :open="showAdd"
      @close="showAdd = false"
      @submit="onUpload"
    />
  </main>
</template>
