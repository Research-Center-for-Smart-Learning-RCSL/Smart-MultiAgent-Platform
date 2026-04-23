<script setup lang="ts">
import { onMounted } from 'vue'
import { useMyKeys } from '../composables/useMyKeys'
import KeyUploadForm from '../components/KeyUploadForm.vue'
import CapabilityChip from '../components/CapabilityChip.vue'
import type { ApiKeyProvider } from '../api/keys'

const { keys, loading, error, reload, upload, retest, remove } = useMyKeys()

async function onUpload(p: { provider: ApiKeyProvider; name: string; secret: string }) {
  await upload(p.provider, p.name, p.secret)
}

onMounted(reload)
</script>

<template>
  <main class="key-list-view">
    <h1>{{ $t('keys.list.title') }}</h1>
    <p v-if="error" class="error" data-testid="key-error">{{ error }}</p>

    <KeyUploadForm @submit="onUpload" />

    <p v-if="loading">{{ $t('keys.list.loading') }}</p>
    <table v-else data-testid="key-list">
      <thead>
        <tr>
          <th>{{ $t('keys.list.provider') }}</th>
          <th>{{ $t('keys.list.name') }}</th>
          <th>{{ $t('keys.list.preview') }}</th>
          <th>{{ $t('keys.list.status') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="k in keys" :key="k.id" :data-testid="`key-row-${k.id}`">
          <td><CapabilityChip :provider="k.provider" /></td>
          <td>{{ k.name }}</td>
          <!-- `masked_preview` is backend-generated; plaintext never exists on
               the client after the upload form submits, so nothing to redact. -->
          <td><code>{{ k.masked_preview }}</code></td>
          <td :class="`status-${k.test_status}`">
            {{ k.test_status }}
            <small v-if="k.test_error"> — {{ k.test_error }}</small>
          </td>
          <td>
            <button @click="retest(k.id)" data-testid="retest">
              {{ $t('keys.list.retest') }}
            </button>
            <button @click="remove(k.id)" data-testid="delete">
              {{ $t('keys.list.delete') }}
            </button>
          </td>
        </tr>
        <tr v-if="keys.length === 0">
          <td colspan="5" class="empty">{{ $t('keys.list.empty') }}</td>
        </tr>
      </tbody>
    </table>
  </main>
</template>
