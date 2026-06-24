<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
  SModal,
  SFormField,
  SSelect,
  SInput,
  SRadio,
  SStatusBadge,
  SBadge,
  SDropdown,
  SEmptyState,
  SAlert,
  STooltip,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useSearchKeys } from '../composables/useSearchKeys'
import type { SearchProvider } from '../api/search-keys'
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
const provider = ref<SearchProvider>('brave')
const secret = ref('')
const cx = ref('')
const depth = ref<'basic' | 'advanced'>('basic')
const uploading = ref(false)
const uploadError = ref('')

const columns = computed<Column[]>(() => [
  { key: 'provider', label: t('keys.search.provider'), width: '160px' },
  { key: 'masked_preview', label: t('keys.search.preview'), width: '110px' },
  { key: 'test_status', label: t('keys.search.status'), width: '120px', align: 'center' },
  { key: 'is_active', label: t('keys.search.active'), width: '80px', align: 'center' },
  { key: 'actions', label: '', width: '80px', align: 'right' },
])

const providerSelectOptions = computed(() => [
  { value: 'brave', label: t('keys.search.brave') },
  { value: 'serper', label: t('keys.search.serper') },
  { value: 'tavily', label: t('keys.search.tavily') },
  { value: 'google_cse', label: t('keys.search.googleCse') },
])

const depthOptions = computed(() => [
  { value: 'basic', label: t('keys.search.depthBasic') },
  { value: 'advanced', label: t('keys.search.depthAdvanced') },
])

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

async function onUpload() {
  if (!secret.value.trim()) return
  uploading.value = true
  uploadError.value = ''
  const config: Record<string, unknown> = {}
  if (provider.value === 'google_cse') config.cx = cx.value.trim()
  if (provider.value === 'tavily') config.search_depth = depth.value
  try {
    await upload(provider.value, secret.value, config)
    showAdd.value = false
    secret.value = ''
    cx.value = ''
    depth.value = 'basic'
    toast.success(t('keys.search.uploaded'))
  } catch {
    uploadError.value = t('keys.search.uploadFailed')
  } finally {
    uploading.value = false
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
  const ok = await confirm({
    title: t('keys.search.deleteTitle'),
    message: t('keys.search.deleteBody', { provider: PROVIDER_DISPLAY[key?.provider ?? 'brave'] }),
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

function closeAddModal() {
  showAdd.value = false
  secret.value = ''
  cx.value = ''
  depth.value = 'basic'
  uploadError.value = ''
}

onMounted(reload)
watch(projectId, reload)
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
      :data="keys"
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
        <code class="text-[13px] font-mono text-[var(--color-muted)]">{{ row.masked_preview }}</code>
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

      <template #cell-is_active="{ row }">
        <STooltip
          v-if="row.test_status === 'failed'"
          :content="$t('keys.search.cannotActivateInvalid')"
          placement="top"
        >
          <SRadio
            :model-value="keys.find((k: { provider: string; is_active: boolean }) => k.provider === row.provider && k.is_active)?.id ?? ''"
            :value="row.id"
            :name="`active-${row.provider}`"
            disabled
            :data-testid="`activate-${row.id}`"
          />
        </STooltip>
        <SRadio
          v-else
          :model-value="keys.find((k: { provider: string; is_active: boolean }) => k.provider === row.provider && k.is_active)?.id ?? ''"
          :value="row.id"
          :name="`active-${row.provider}`"
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

    <!-- Add Search Key Modal -->
    <SModal
      :open="showAdd"
      :title="$t('keys.search.addTitle')"
      size="md"
      @close="closeAddModal"
    >
      <form
        id="add-search-key-form"
        @submit.prevent="onUpload"
      >
        <div class="flex flex-col gap-4">
          <SFormField
            :label="$t('keys.search.providerLabel')"
            name="provider"
            required
          >
            <SSelect
              v-model="provider"
              :options="providerSelectOptions"
              data-testid="search-provider"
            />
          </SFormField>

          <SFormField
            :label="$t('keys.search.secret')"
            name="secret"
            :error="uploadError"
            required
          >
            <SInput
              v-model="secret"
              type="password"
              autocomplete="off"
              :error="!!uploadError"
              data-testid="search-secret"
            />
          </SFormField>

          <!-- Google CSE specific -->
          <SFormField
            v-if="provider === 'google_cse'"
            :label="$t('keys.search.cx')"
            name="cx"
            required
          >
            <SInput
              v-model="cx"
              data-testid="search-cx"
            />
          </SFormField>

          <!-- Tavily specific -->
          <SFormField
            v-if="provider === 'tavily'"
            :label="$t('keys.search.searchDepth')"
            name="depth"
          >
            <SSelect
              v-model="depth"
              :options="depthOptions"
            />
          </SFormField>
        </div>
      </form>

      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="closeAddModal"
          >
            {{ $t('app.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            type="submit"
            form="add-search-key-form"
            :loading="uploading"
            data-testid="search-upload"
          >
            {{ $t('keys.search.upload') }}
          </SButton>
        </div>
      </template>
    </SModal>
  </main>
</template>
