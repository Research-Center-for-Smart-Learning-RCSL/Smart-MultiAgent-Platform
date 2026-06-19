<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { orgsApi, type OriginalCreatorTransfer } from '../api/orgs'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const session = useSessionStore()
const pending = ref<OriginalCreatorTransfer | null>(null)
const targetUserId = ref('')
const error = ref<string | null>(null)

const isTarget = computed(
  () => pending.value !== null && session.me?.id === pending.value.target_user_id,
)
const isInitiator = computed(
  () => pending.value !== null && session.me?.id === pending.value.initiator_user_id,
)

function orgId(): string {
  return route.params.id as string
}

async function load(): Promise<void> {
  try {
    const { data } = await orgsApi.listTransfers(orgId())
    pending.value = data.find((tr) => tr.state === 'pending') ?? null
  } catch {
    toast.error(t('tenancy.transfer.loadFailed'))
  }
}

async function initiate(): Promise<void> {
  error.value = null
  try {
    await orgsApi.initiateTransfer(orgId(), targetUserId.value.trim())
    targetUserId.value = ''
    await load()
  } catch {
    error.value = t('tenancy.errors.generic')
  }
}

async function acceptTransfer(): Promise<void> {
  if (!pending.value) return
  try {
    await orgsApi.acceptTransfer(orgId(), pending.value.id)
    await load()
  } catch {
    toast.error(t('tenancy.transfer.acceptFailed'))
  }
}

async function cancel(): Promise<void> {
  if (!pending.value) return
  try {
    await orgsApi.cancelTransfer(orgId(), pending.value.id)
    await load()
  } catch {
    toast.error(t('tenancy.transfer.cancelFailed'))
  }
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.transfer.title') }}</h1>
    <div v-if="pending">
      <p>{{ $t('tenancy.transfer.pending') }}: {{ pending.target_user_id }} ({{ pending.expires_at }})</p>
      <button
        v-if="isTarget"
        @click="acceptTransfer"
      >
        {{ $t('tenancy.transfer.accept') }}
      </button>
      <button
        v-if="isInitiator"
        @click="cancel"
      >
        {{ $t('tenancy.transfer.cancel') }}
      </button>
    </div>
    <template v-else>
      <p>{{ $t('tenancy.transfer.none') }}</p>
      <form @submit.prevent="initiate">
        <label>
          {{ $t('tenancy.transfer.targetLabel') }}
          <input
            v-model="targetUserId"
            :placeholder="$t('tenancy.transfer.targetPlaceholder')"
            required
          >
        </label>
        <button type="submit">
          {{ $t('tenancy.transfer.initiate') }}
        </button>
      </form>
      <p
        v-if="error"
        class="error"
      >
        {{ error }}
      </p>
    </template>
  </main>
</template>
