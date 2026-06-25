<template>
  <section class="admin-impersonate">
    <SPageHeader :title="$t('admin.impersonation.title')">
      <template #description>
        {{ $t('admin.impersonation.description') }}
      </template>
    </SPageHeader>

    <form
      class="admin-impersonate__form"
      @submit.prevent="onStart"
    >
      <SInput
        v-model="targetUserId"
        class="admin-impersonate__input"
        :placeholder="$t('admin.impersonation.targetPlaceholder')"
        :aria-label="$t('admin.impersonation.targetPlaceholder')"
      />
      <SButton
        type="submit"
        variant="primary"
        :loading="startImpersonation.isPending.value"
      >
        {{ $t('admin.impersonation.start') }}
      </SButton>
    </form>

    <SCard
      v-if="isImpersonating"
      class="admin-impersonate__active"
    >
      <div class="admin-impersonate__active-row">
        <span class="admin-impersonate__active-text">
          {{ $t('admin.impersonation.activeSession') }}
        </span>
        <SButton
          variant="danger"
          size="sm"
          :loading="endImpersonation.isPending.value"
          @click="onEnd"
        >
          {{ $t('admin.impersonation.end') }}
        </SButton>
      </div>
    </SCard>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-2"
      role="alert"
    >
      {{ error }}
    </SAlert>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SPageHeader, SInput, SButton, SCard, SAlert } from '@shared/ui'
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
.admin-impersonate__form {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 1rem 0;
  align-items: center;
}
.admin-impersonate__input {
  flex: 1 1 20rem;
  max-width: 28rem;
}
.admin-impersonate__active {
  margin: 1rem 0;
  border: 2px solid var(--color-warning);
}
.admin-impersonate__active-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}
.admin-impersonate__active-text {
  font-weight: 600;
}
</style>
