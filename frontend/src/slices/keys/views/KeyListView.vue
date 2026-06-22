<script setup lang="ts">
import { SPageHeader } from '@shared/ui'
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables'
import { useMyKeys } from '../composables/useMyKeys'
import KeyUploadForm from '../components/KeyUploadForm.vue'
import CapabilityChip from '../components/CapabilityChip.vue'
import type { ApiKeyProvider } from '../api/keys'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const { keys, loading, error, reload, upload, retest, remove } = useMyKeys()

async function onUpload(p: { provider: ApiKeyProvider; name: string; secret: string }) {
  await upload(p.provider, p.name, p.secret)
}

async function onRemove(id: string): Promise<void> {
  const ok = await confirm({
    title: t('keys.list.deleteConfirmTitle'),
    message: t('keys.list.deleteConfirm'),
    confirmLabel: t('keys.list.delete'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  await remove(id)
}

onMounted(reload)
</script>

<template>
  <main class="key-list-view">
    <SPageHeader :title="$t('keys.list.title')" />
    <p
      v-if="error"
      class="error"
      role="alert"
      data-testid="key-error"
    >
      {{ error }}
    </p>

    <KeyUploadForm @submit="onUpload" />

    <p v-if="loading">
      {{ $t('keys.list.loading') }}
    </p>
    <div
      v-else
      class="overflow-x-auto"
    >
    <table
      class="table"
      data-testid="key-list"
    >
      <thead>
        <tr>
          <th scope="col">{{ $t('keys.list.provider') }}</th>
          <th scope="col">{{ $t('keys.list.name') }}</th>
          <th scope="col">{{ $t('keys.list.preview') }}</th>
          <th scope="col">{{ $t('keys.list.status') }}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="k in keys"
          :key="k.id"
          :data-testid="`key-row-${k.id}`"
        >
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
            <button
              class="btn btn-sm"
              data-testid="retest"
              @click="retest(k.id)"
            >
              {{ $t('keys.list.retest') }}
            </button>
            <button
              class="btn btn-danger btn-sm"
              data-testid="delete"
              @click="onRemove(k.id)"
            >
              {{ $t('keys.list.delete') }}
            </button>
          </td>
        </tr>
        <tr v-if="keys.length === 0">
          <td
            colspan="5"
            class="empty"
          >
            {{ $t('keys.list.empty') }}
          </td>
        </tr>
      </tbody>
    </table>
    </div>
  </main>
</template>
