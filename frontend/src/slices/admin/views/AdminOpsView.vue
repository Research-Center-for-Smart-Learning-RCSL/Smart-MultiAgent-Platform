<template>
  <section class="admin-ops">
    <SPageHeader :title="$t('admin.ops.title')" />

    <SCard class="admin-ops__section">
      <h2 class="admin-ops__heading">
        {{ $t('admin.ops.graphragReset') }}
      </h2>
      <form
        class="admin-ops__form"
        @submit.prevent="onResetGraphrag"
      >
        <SInput
          v-model="graphragConfigId"
          class="admin-ops__input"
          :placeholder="$t('admin.ops.configIdPlaceholder')"
          :aria-label="$t('admin.ops.configId')"
        />
        <SButton
          type="submit"
          variant="primary"
          :loading="actions.resetGraphrag.isPending.value"
        >
          {{ $t('admin.ops.reset') }}
        </SButton>
      </form>
    </SCard>

    <SCard class="admin-ops__section">
      <h2 class="admin-ops__heading">
        {{ $t('admin.ops.restore') }}
      </h2>
      <form
        class="admin-ops__form"
        @submit.prevent="onRestore"
      >
        <SSelect
          :model-value="restoreType"
          class="admin-ops__select"
          :options="restoreTypeOptions"
          :aria-label="$t('admin.ops.resourceType')"
          @update:model-value="onRestoreTypeChange"
        />
        <SInput
          v-model="restoreId"
          class="admin-ops__input"
          :placeholder="$t('admin.ops.resourceIdPlaceholder')"
          :aria-label="$t('admin.ops.resourceId')"
        />
        <SButton
          type="submit"
          variant="primary"
          :loading="actions.restoreResource.isPending.value"
        >
          {{ $t('admin.ops.restoreAction') }}
        </SButton>
      </form>
    </SCard>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SPageHeader, SCard, SInput, SSelect, SButton } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import type { RestoreResourceType } from '../api/admin'
import { useAdminActions } from '../composables/useAdminActions'

const { t } = useI18n()
const toast = useToast()

const graphragConfigId = ref('')
const restoreType = ref<RestoreResourceType>('org')
const restoreId = ref('')

const restoreTypeOptions = computed(() => [
  { value: 'user', label: t('admin.ops.typeUser') },
  { value: 'org', label: t('admin.ops.typeOrg') },
  { value: 'project', label: t('admin.ops.typeProject') },
  { value: 'agent', label: t('admin.ops.typeAgent') },
  { value: 'workflow', label: t('admin.ops.typeWorkflow') },
  { value: 'chatroom', label: t('admin.ops.typeChatroom') },
])

const { confirm } = useConfirmDialog()
const actions = useAdminActions()

async function onResetGraphrag(): Promise<void> {
  const ok = await confirm({
    title: t('admin.ops.resetConfirmTitle'),
    message: t('admin.ops.resetConfirmMessage'),
    confirmLabel: t('admin.ops.reset'),
    cancelLabel: t('app.cancel'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await actions.resetGraphrag.mutateAsync(graphragConfigId.value.trim())
    toast.success(t('admin.ops.graphragResetSuccess'))
    graphragConfigId.value = ''
  } catch {
    // The mutation owns the single failure toast.
  }
}

// SSelect emits the wider `string | number`; every option value is a RestoreResourceType,
// so narrowing here is safe (same SSelect-boundary pattern as OnErrorConfigForm).
function onRestoreTypeChange(value: string | number): void {
  restoreType.value = value as RestoreResourceType
}

async function onRestore(): Promise<void> {
  try {
    await actions.restoreResource.mutateAsync({ type: restoreType.value, id: restoreId.value.trim() })
    toast.success(t('admin.ops.restoreSuccess'))
    restoreId.value = ''
  } catch {
    // The mutation owns the single failure toast.
  }
}
</script>

<style scoped>
.admin-ops__section {
  margin: var(--space-6) 0;
}
.admin-ops__heading {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-semibold);
  margin: 0 0 var(--space-3);
}
.admin-ops__form {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}
.admin-ops__input {
  flex: 1 1 18rem;
  max-width: 28rem;
}
.admin-ops__select {
  width: 12rem;
}
</style>
