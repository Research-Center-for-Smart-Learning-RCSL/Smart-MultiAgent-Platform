<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowPathIcon,
  TrashIcon,
  ExclamationTriangleIcon,
} from '@heroicons/vue/24/outline'
import { SPageHeader, SCard, SButton, SStatusBadge, SEmptyState } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useMyKeys } from '../composables/useMyKeys'
import CapabilityChip from '../components/CapabilityChip.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirmDialog()
const keyId = computed(() => route.params.id as string)
const { keys, loading, error, reload, retest, remove } = useMyKeys()

const retesting = ref(false)
const deleting = ref(false)

const current = computed(() => keys.value.find((k) => k.id === keyId.value))

const breadcrumbs = computed(() => [
  { label: t('keys.list.title'), to: { name: 'keys.list' } },
  { label: current.value?.name ?? '' },
])

function formatDatetime(iso: string | null): string {
  if (!iso) return t('keys.detail.never')
  return new Date(iso).toLocaleString()
}

async function onRetest() {
  retesting.value = true
  try {
    await retest(keyId.value)
    const key = keys.value.find((k) => k.id === keyId.value)
    if (key?.test_status === 'ok') {
      toast.success(t('keys.detail.retestValid'))
    } else if (key?.test_status === 'failed') {
      toast.warning(t('keys.detail.retestInvalid'))
    }
  } catch {
    toast.error(t('keys.detail.retestFailed'))
  } finally {
    retesting.value = false
  }
}

async function onDelete() {
  const ok = await confirm({
    title: t('keys.detail.deleteTitle'),
    message: t('keys.detail.deleteBody', { name: current.value?.name ?? '' }),
    confirmLabel: t('keys.detail.deleteConfirmLabel'),
    variant: 'error',
  })
  if (!ok) return
  deleting.value = true
  try {
    await remove(keyId.value)
    if (!error.value) {
      await router.replace({ name: 'keys.list' })
    }
  } catch {
    toast.error(t('keys.detail.deleteFailed'))
  } finally {
    deleting.value = false
  }
}

</script>

<template>
  <main class="p-6">
    <!-- Not found state -->
    <div
      v-if="!loading && !current"
      class="flex justify-center mt-12"
    >
      <SEmptyState
        :icon="ExclamationTriangleIcon"
        :title="$t('keys.detail.notFound')"
        :text="$t('keys.detail.notFoundDescription')"
      >
        <template #action>
          <SButton
            variant="secondary"
            :to="{ name: 'keys.list' }"
            as="router-link"
          >
            {{ $t('keys.detail.backToKeys') }}
          </SButton>
        </template>
      </SEmptyState>
    </div>

    <template v-if="current">
      <SPageHeader
        :title="current.name"
        :breadcrumbs="breadcrumbs"
      >
        <template #actions>
          <SButton
            variant="secondary"
            :loading="retesting"
            @click="onRetest"
          >
            <template #icon-left>
              <ArrowPathIcon class="w-4 h-4" />
            </template>
            {{ $t('keys.detail.retest') }}
          </SButton>
          <SButton
            variant="danger"
            :loading="deleting"
            @click="onDelete"
          >
            <template #icon-left>
              <TrashIcon class="w-4 h-4" />
            </template>
            {{ $t('keys.detail.delete') }}
          </SButton>
        </template>
      </SPageHeader>

      <SCard
        variant="elevated"
        padding="lg"
        class="mt-6"
      >
        <dl class="detail-dl">
          <div class="detail-row">
            <dt>{{ $t('keys.detail.provider') }}</dt>
            <dd><CapabilityChip :provider="current.provider" /></dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.name') }}</dt>
            <dd>{{ current.name }}</dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.preview') }}</dt>
            <dd>
              <code class="text-[13px] font-mono">{{ current.masked_preview }}</code>
            </dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.status') }}</dt>
            <dd>
              <SStatusBadge :status="current.test_status" />
              <span
                v-if="current.test_status === 'failed' && current.test_error"
                class="text-xs text-[var(--color-muted)] ml-2"
              >{{ current.test_error }}</span>
            </dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.lastTested') }}</dt>
            <dd>{{ formatDatetime(current.last_test_at) }}</dd>
          </div>
          <div class="detail-row border-b-0">
            <dt>{{ $t('keys.detail.created') }}</dt>
            <dd>{{ formatDatetime(current.created_at) }}</dd>
          </div>
        </dl>
      </SCard>
    </template>
  </main>
</template>

<style scoped>
.detail-dl {
  display: flex;
  flex-direction: column;
}

.detail-row {
  display: flex;
  align-items: center;
  min-height: 40px;
  border-bottom: 1px solid var(--color-border);
}

.detail-row dt {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-muted);
  width: 140px;
  flex-shrink: 0;
}

.detail-row dd {
  font-size: 0.875rem;
  color: var(--color-fg);
}

@media (max-width: 767px) {
  .detail-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    padding: 8px 0;
  }

  .detail-row dt {
    width: auto;
  }
}
</style>
