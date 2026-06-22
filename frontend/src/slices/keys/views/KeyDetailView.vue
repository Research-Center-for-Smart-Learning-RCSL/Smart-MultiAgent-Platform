<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables'
import { useMyKeys } from '../composables/useMyKeys'
import CapabilityChip from '../components/CapabilityChip.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirmDialog()
const keyId = computed(() => route.params.id as string)
const { keys, error, reload, retest, remove } = useMyKeys()
const busy = ref(false)

const current = computed(() => keys.value.find((k) => k.id === keyId.value))

async function onRemove(id: string): Promise<void> {
  const ok = await confirm({
    title: t('keys.detail.deleteConfirmTitle'),
    message: t('keys.detail.deleteConfirm'),
    confirmLabel: t('keys.detail.delete'),
    variant: 'error',
  })
  if (!ok) return
  busy.value = true
  try {
    await remove(id)
    if (!error.value) {
      await router.replace({ name: 'keys.list' })
    }
  } finally {
    busy.value = false
  }
}

onMounted(reload)
</script>

<template>
  <main class="key-detail-view">
    <h1>{{ $t('keys.detail.title') }}</h1>
    <p
      v-if="error"
      class="error"
      role="alert"
    >
      {{ error }}
    </p>
    <p v-if="!current && !busy">
      {{ $t('keys.detail.notFound') }}
    </p>
    <section v-if="current">
      <dl>
        <dt>{{ $t('keys.detail.provider') }}</dt>
        <dd><CapabilityChip :provider="current.provider" /></dd>
        <dt>{{ $t('keys.detail.name') }}</dt>
        <dd>{{ current.name }}</dd>
        <dt>{{ $t('keys.detail.preview') }}</dt>
        <dd><code>{{ current.masked_preview }}</code></dd>
        <dt>{{ $t('keys.detail.status') }}</dt>
        <dd :class="`status-${current.test_status}`">
          {{ current.test_status }}
        </dd>
        <dt>{{ $t('keys.detail.lastTest') }}</dt>
        <dd>{{ current.last_test_at ?? '—' }}</dd>
      </dl>
      <button
        class="btn"
        @click="retest(current.id)"
      >
        {{ $t('keys.detail.retest') }}
      </button>
      <button
        class="btn btn-danger"
        :disabled="busy"
        @click="onRemove(current.id)"
      >
        {{ $t('keys.detail.delete') }}
      </button>
    </section>
  </main>
</template>
