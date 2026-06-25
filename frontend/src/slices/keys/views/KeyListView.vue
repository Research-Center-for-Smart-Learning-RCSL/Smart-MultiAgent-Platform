<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  PlusIcon,
  EyeIcon,
  ArrowPathIcon,
  TrashIcon,
  KeyIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SDropdown,
  SStatusBadge,
  SEmptyState,
  SAlert,
  SPagination,
} from '@shared/ui'
import { useConfirmDialog, useToast, useClientPagination } from '@shared/composables'
import { useMyKeys } from '../composables/useMyKeys'
import KeyUploadForm from '../components/KeyUploadForm.vue'
import CapabilityChip from '../components/CapabilityChip.vue'
import type { ApiKeyProvider } from '../api/keys'
import MaskedPreview from '../components/MaskedPreview.vue'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirmDialog()
const { keys, loading, error, reload, upload, retest, remove } = useMyKeys()

const showUpload = ref(false)
const retestingId = ref<string | null>(null)
const uploading = ref(false)

const { currentPage, totalPages, paginatedItems: paginatedKeys, pageSize } = useClientPagination(keys)

const columns = computed<Column[]>(() => [
  { key: 'provider', label: t('keys.list.provider'), width: '160px' },
  { key: 'name', label: t('keys.list.name') },
  { key: 'masked_preview', label: t('keys.list.preview'), width: '120px' },
  { key: 'test_status', label: t('keys.list.status'), width: '120px', align: 'center' },
  { key: 'actions', label: '', width: '80px', align: 'right' },
])

const actionItems = computed(() => [
  { key: 'detail', label: t('keys.list.viewDetail'), icon: EyeIcon },
  { key: 'retest', label: t('keys.list.retest'), icon: ArrowPathIcon },
  { key: 'delete', label: t('keys.list.delete'), icon: TrashIcon, danger: true },
])

async function onUpload(p: { provider: ApiKeyProvider; name: string; secret: string }) {
  if (uploading.value) return
  uploading.value = true
  try {
    await upload(p.provider, p.name, p.secret)
    showUpload.value = false
    toast.success(t('keys.form.uploaded'))
  } catch {
    toast.error(t('keys.form.uploadFailed'))
  } finally {
    uploading.value = false
  }
}

async function onRetest(id: string) {
  retestingId.value = id
  try {
    await retest(id)
    const key = keys.value.find((k) => k.id === id)
    if (key?.test_status === 'ok') {
      toast.success(t('keys.list.retestValid'))
    } else if (key?.test_status === 'failed') {
      toast.warning(t('keys.list.retestInvalid'))
    }
  } catch {
    toast.error(t('keys.list.retestFailed'))
  } finally {
    retestingId.value = null
  }
}

async function onDelete(id: string) {
  const key = keys.value.find((k) => k.id === id)
  const ok = await confirm({
    title: t('keys.list.deleteTitle'),
    message: t('keys.list.deleteBody', { name: key?.name ?? '' }),
    confirmLabel: t('keys.list.deleteConfirm'),
    cancelLabel: t('keys.list.deleteCancel'),
    variant: 'error',
  })
  if (!ok) return
  try {
    await remove(id)
    toast.success(t('keys.list.deleted'))
  } catch {
    toast.error(t('keys.list.deleteFailed'))
  }
}

function onAction(key: string, row: { id: string }) {
  if (key === 'detail') {
    router.push({ name: 'keys.detail', params: { id: row.id } })
  } else if (key === 'retest') {
    void onRetest(row.id)
  } else if (key === 'delete') {
    void onDelete(row.id)
  }
}

</script>

<template>
  <main class="p-6">
    <SPageHeader :title="$t('keys.list.title')">
      <template #description>
        {{ $t('keys.list.description') }}
      </template>
      <template #actions>
        <SButton
          variant="primary"
          @click="showUpload = true"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ $t('keys.form.submit') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-4"
    >
      {{ $t('keys.list.fetchError') }}
    </SAlert>

    <STable
      :columns="columns"
      :data="paginatedKeys"
      :loading="loading"
      row-key="id"
      class="mt-6"
    >
      <template #cell-provider="{ row }">
        <CapabilityChip :provider="row.provider" />
      </template>

      <template #cell-name="{ row }">
        <span class="truncate max-w-[40ch] inline-block">{{ row.name }}</span>
      </template>

      <template #cell-masked_preview="{ row }">
        <MaskedPreview
          :value="row.masked_preview"
          :aria-label="$t('keys.list.maskedKey')"
        />
      </template>

      <template #cell-test_status="{ row }">
        <SStatusBadge
          :status="row.test_status"
          :aria-label="`${$t('keys.list.status')}: ${row.test_status}`"
        />
        <small
          v-if="row.test_status === 'failed' && row.test_error"
          class="block text-xs text-[var(--color-muted)] truncate max-w-[60ch]"
          :title="row.test_error"
        >
          {{ row.test_error }}
        </small>
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
          :icon="KeyIcon"
          :title="$t('keys.list.emptyTitle')"
          :text="$t('keys.list.emptyDescription')"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="showUpload = true"
            >
              {{ $t('keys.form.submit') }}
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

    <KeyUploadForm
      :open="showUpload"
      :loading="uploading"
      @close="showUpload = false"
      @submit="onUpload"
    />
  </main>
</template>
