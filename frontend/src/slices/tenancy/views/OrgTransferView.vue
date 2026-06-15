<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useToast } from '@shared/composables'
import { orgsApi, type OriginalCreatorTransfer } from '../api/orgs'

const route = useRoute()
const toast = useToast()
const pending = ref<OriginalCreatorTransfer | null>(null)
const targetUserId = ref('')
const error = ref<string | null>(null)

function orgId(): string {
  return route.params.id as string
}

async function load(): Promise<void> {
  try {
    const { data } = await orgsApi.listTransfers(orgId())
    pending.value = data.find((t) => t.state === 'pending') ?? null
  } catch {
    toast.error('Failed to load transfers.')
  }
}

async function initiate(): Promise<void> {
  error.value = null
  try {
    await orgsApi.initiateTransfer(orgId(), targetUserId.value.trim())
    targetUserId.value = ''
    await load()
  } catch {
    error.value = 'generic'
  }
}

async function cancel(): Promise<void> {
  if (!pending.value) return
  try {
    await orgsApi.cancelTransfer(orgId(), pending.value.id)
    await load()
  } catch {
    toast.error('Failed to cancel transfer.')
  }
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.transfer.title') }}</h1>
    <div v-if="pending">
      <p>{{ $t('tenancy.transfer.pending') }}: {{ pending.target_user_id }} ({{ pending.expires_at }})</p>
      <button @click="cancel">
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
