<template>
  <section class="admin-impersonate">
    <h1>{{ $t('admin.impersonation.title') }}</h1>
    <p>{{ $t('admin.impersonation.description') }}</p>

    <form @submit.prevent="onStart">
      <input
        v-model="targetUserId"
        :placeholder="$t('admin.impersonation.targetPlaceholder')"
        required
      >
      <button
        type="submit"
        :disabled="startImpersonation.isPending.value"
      >
        {{ $t('admin.impersonation.start') }}
      </button>
    </form>

    <div
      v-if="isImpersonating"
      class="admin-impersonate__active"
    >
      <p>{{ $t('admin.impersonation.activeSession') }}</p>
      <button
        :disabled="endImpersonation.isPending.value"
        @click="onEnd"
      >
        {{ $t('admin.impersonation.end') }}
      </button>
    </div>

    <p
      v-if="error"
      class="admin-impersonate__error"
    >
      {{ error }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useImpersonation } from '../composables/useImpersonation'

const { t } = useI18n()
const targetUserId = ref('')
const error = ref<string | null>(null)

const { isImpersonating, activeSessionTarget, startImpersonation, endImpersonation } = useImpersonation()

async function onStart(): Promise<void> {
  error.value = null
  try {
    await startImpersonation.mutateAsync(targetUserId.value.trim())
  } catch {
    error.value = t('admin.impersonation.startFailed')
  }
}

async function onEnd(): Promise<void> {
  error.value = null
  try {
    await endImpersonation.mutateAsync(activeSessionTarget.value ?? '')
  } catch {
    error.value = t('admin.impersonation.endFailed')
  }
}
</script>

<style scoped>
form { display: flex; gap: 0.5rem; margin: 1rem 0; }
.admin-impersonate__active {
  margin: 1rem 0;
  padding: 0.75rem;
  border: 2px solid var(--color-warning);
  border-radius: var(--radius-md);
}
.admin-impersonate__error { color: var(--color-danger); }
</style>
