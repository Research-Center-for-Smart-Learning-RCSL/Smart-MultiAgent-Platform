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
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SPageHeader, SInput, SButton, SCard } from '@shared/ui'
import { useConfirmDialog } from '@shared/composables'
import { useImpersonation } from '../composables/useImpersonation'

const { t } = useI18n()
const targetUserId = ref('')

const { confirm } = useConfirmDialog()
const { isImpersonating, activeSessionTarget, startImpersonation, endImpersonation } = useImpersonation()

async function onStart(): Promise<void> {
  const ok = await confirm({
    title: t('admin.impersonation.confirmTitle'),
    message: t('admin.impersonation.confirmMessage'),
    confirmLabel: t('admin.impersonation.confirmStart'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await startImpersonation.mutateAsync(targetUserId.value.trim())
  } catch {
    // The composable owns the single failure toast.
  }
}

async function onEnd(): Promise<void> {
  try {
    await endImpersonation.mutateAsync(activeSessionTarget.value ?? '')
  } catch {
    // The composable owns the single failure toast.
  }
}
</script>

<style scoped>
.admin-impersonate__form {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: var(--space-4) 0;
  align-items: center;
}
.admin-impersonate__input {
  flex: 1 1 20rem;
  max-width: 28rem;
}
.admin-impersonate__active {
  margin: var(--space-4) 0;
  border: 2px solid var(--color-warning);
}
.admin-impersonate__active-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.admin-impersonate__active-text {
  font-weight: var(--weight-semibold);
}
</style>
