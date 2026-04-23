<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSearchKeys } from '../composables/useSearchKeys'
import type { SearchProvider } from '../api/search-keys'

const route = useRoute()
const projectId = computed(() => route.params.projectId as string)
const { keys, error, reload, upload, retest, activate, remove } = useSearchKeys(
  () => projectId.value,
)

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

onMounted(reload)
watch(projectId, reload)
</script>

<template>
  <main class="search-key-view">
    <h1>{{ $t('keys.search.title') }}</h1>
    <p v-if="error" class="error">{{ error }}</p>

    <form @submit.prevent="onUpload">
      <label>
        {{ $t('keys.search.provider') }}
        <select v-model="provider" data-testid="search-provider">
          <option value="brave">brave</option>
          <option value="serper">serper</option>
          <option value="tavily">tavily</option>
          <option value="google_cse">google_cse</option>
        </select>
      </label>
      <label>
        {{ $t('keys.search.secret') }}
        <input
          v-model="secret"
          type="password"
          autocomplete="off"
          data-testid="search-secret"
        />
      </label>
      <label v-if="provider === 'google_cse'">
        cx
        <input v-model="cx" data-testid="search-cx" />
      </label>
      <label v-if="provider === 'tavily'">
        search_depth
        <select v-model="depth">
          <option value="basic">basic</option>
          <option value="advanced">advanced</option>
        </select>
      </label>
      <button type="submit" data-testid="search-upload">
        {{ $t('keys.search.upload') }}
      </button>
    </form>

    <table data-testid="search-list">
      <thead>
        <tr>
          <th>{{ $t('keys.search.provider') }}</th>
          <th>{{ $t('keys.search.preview') }}</th>
          <th>{{ $t('keys.search.status') }}</th>
          <th>{{ $t('keys.search.active') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="k in keys" :key="k.id">
          <td>{{ k.provider }}</td>
          <td><code>{{ k.masked_preview }}</code></td>
          <td :class="`status-${k.test_status}`">{{ k.test_status }}</td>
          <td>
            <input
              type="radio"
              :checked="k.is_active"
              name="active"
              :data-testid="`activate-${k.id}`"
              @change="activate(k.id)"
            />
          </td>
          <td>
            <button @click="retest(k.id)">{{ $t('keys.search.retest') }}</button>
            <button @click="remove(k.id)">{{ $t('keys.search.delete') }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </main>
</template>
