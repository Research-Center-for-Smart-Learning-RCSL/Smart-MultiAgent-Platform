<script setup lang="ts">
import { SPageHeader } from '@shared/ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables'
import { useSearchKeys } from '../composables/useSearchKeys'
import type { SearchProvider } from '../api/search-keys'

const { t } = useI18n()
const route = useRoute()
const { confirm } = useConfirmDialog()
const projectId = computed(() => route.params.projectId as string)
const { keys, error, reload, upload, retest, activate, remove } = useSearchKeys(
  () => projectId.value,
)
const busy = ref(false)

const provider = ref<SearchProvider>('brave')
const secret = ref('')
const cx = ref('')
const depth = ref<'basic' | 'advanced'>('basic')

async function onUpload() {
  if (!secret.value.trim()) return
  const config: Record<string, unknown> = {}
  if (provider.value === 'google_cse') config.cx = cx.value.trim()
  if (provider.value === 'tavily') config.search_depth = depth.value
  await upload(provider.value, secret.value, config)
  secret.value = ''
}

async function onRemove(id: string): Promise<void> {
  const ok = await confirm({
    title: t('keys.search.deleteConfirmTitle'),
    message: t('keys.search.deleteConfirm'),
    confirmLabel: t('keys.search.delete'),
    variant: 'error',
  })
  if (!ok) return
  busy.value = true
  try { await remove(id) } finally { busy.value = false }
}

onMounted(reload)
watch(projectId, reload)
</script>

<template>
  <main class="search-key-view">
    <SPageHeader :title="$t('keys.search.title')" />
    <p
      v-if="error"
      class="error"
      role="alert"
    >
      {{ error }}
    </p>

    <form @submit.prevent="onUpload">
      <label>
        {{ $t('keys.search.provider') }}
        <select
          v-model="provider"
          data-testid="search-provider"
        >
          <option value="brave">{{ $t('keys.search.providerBrave') }}</option>
          <option value="serper">{{ $t('keys.search.providerSerper') }}</option>
          <option value="tavily">{{ $t('keys.search.providerTavily') }}</option>
          <option value="google_cse">{{ $t('keys.search.providerGoogleCse') }}</option>
        </select>
      </label>
      <label>
        {{ $t('keys.search.secret') }}
        <input
          v-model="secret"
          type="password"
          autocomplete="off"
          data-testid="search-secret"
        >
      </label>
      <label v-if="provider === 'google_cse'">
        {{ $t('keys.search.cx') }}
        <input
          v-model="cx"
          data-testid="search-cx"
        >
      </label>
      <label v-if="provider === 'tavily'">
        {{ $t('keys.search.searchDepth') }}
        <select v-model="depth">
          <option value="basic">{{ $t('keys.search.depthBasic') }}</option>
          <option value="advanced">{{ $t('keys.search.depthAdvanced') }}</option>
        </select>
      </label>
      <button
        type="submit"
        class="btn btn-primary"
        data-testid="search-upload"
      >
        {{ $t('keys.search.upload') }}
      </button>
    </form>

    <div class="overflow-x-auto">
      <table
        class="table"
        data-testid="search-list"
      >
        <thead>
          <tr>
            <th scope="col">
              {{ $t('keys.search.provider') }}
            </th>
            <th scope="col">
              {{ $t('keys.search.preview') }}
            </th>
            <th scope="col">
              {{ $t('keys.search.status') }}
            </th>
            <th scope="col">
              {{ $t('keys.search.active') }}
            </th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="k in keys"
            :key="k.id"
          >
            <td>{{ k.provider }}</td>
            <td><code>{{ k.masked_preview }}</code></td>
            <td :class="`status-${k.test_status}`">
              {{ k.test_status }}
            </td>
            <td>
              <input
                type="radio"
                :checked="k.is_active"
                name="active"
                :data-testid="`activate-${k.id}`"
                :aria-label="$t('keys.search.active')"
                @change="activate(k.id)"
              >
            </td>
            <td>
              <button
                class="btn btn-sm"
                @click="retest(k.id)"
              >
                {{ $t('keys.search.retest') }}
              </button>
              <button
                class="btn btn-danger btn-sm"
                :disabled="busy"
                @click="onRemove(k.id)"
              >
                {{ $t('keys.search.delete') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </main>
</template>
