<template>
  <section class="admin-impersonate">
    <h1>{{ $t('admin.impersonation.title') }}</h1>
    <p>{{ $t('admin.impersonation.description') }}</p>

    <form @submit.prevent="onStart">
      <input v-model="targetUserId" placeholder="Target user UUID" required />
      <button type="submit" :disabled="startImpersonation.isPending.value">
        {{ $t('admin.impersonation.start') }}
      </button>
    </form>

    <div v-if="isImpersonating" class="admin-impersonate__active">
      <p>{{ $t('admin.impersonation.activeSession') }}</p>
      <button :disabled="endImpersonation.isPending.value" @click="onEnd">{{ $t('admin.impersonation.end') }}</button>
    </div>

    <p v-if="error" class="admin-impersonate__error">{{ error }}</p>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useImpersonation } from '../composables/useImpersonation'

const targetUserId = ref('')
const error = ref<string | null>(null)

const { isImpersonating, activeSessionTarget, startImpersonation, endImpersonation } = useImpersonation()

async function onStart(): Promise<void> {
  error.value = null
  try {
    await startImpersonation.mutateAsync(targetUserId.value.trim())
  } catch {
    error.value = 'Failed to start impersonation session.'
  }
}

async function onEnd(): Promise<void> {
  error.value = null
  try {
    await endImpersonation.mutateAsync(activeSessionTarget.value ?? '')
  } catch {
    error.value = 'Failed to end impersonation session.'
  }
}
</script>

<style scoped>
form { display: flex; gap: 0.5rem; margin: 1rem 0; }
.admin-impersonate__active {
  margin: 1rem 0;
  padding: 0.75rem;
  border: 2px solid var(--color-warning, #f59e0b);
  border-radius: 4px;
}
.admin-impersonate__error { color: var(--color-danger, #dc2626); }
</style>
