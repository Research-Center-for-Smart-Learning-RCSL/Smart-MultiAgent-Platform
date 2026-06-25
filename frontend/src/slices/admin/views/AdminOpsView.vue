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
      <SAlert
        v-if="resetResult"
        :variant="resetResult.ok ? 'success' : 'danger'"
        class="mt-2"
      >
        {{ resetResult.text }}
      </SAlert>
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
          v-model="restoreType"
          class="admin-ops__select"
          :options="restoreTypeOptions"
          :aria-label="$t('admin.ops.resourceType')"
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
      <SAlert
        v-if="restoreResult"
        :variant="restoreResult.ok ? 'success' : 'danger'"
        class="mt-2"
      >
        {{ restoreResult.text }}
      </SAlert>
    </SCard>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SPageHeader, SCard, SInput, SSelect, SButton, SAlert } from '@shared/ui'
import { useAdminActions } from '../composables/useAdminActions'

interface OpResult {
  text: string
  ok: boolean
}

const { t } = useI18n()

const graphragConfigId = ref('')
const resetResult = ref<OpResult | null>(null)
const restoreType = ref('org')
const restoreId = ref('')
const restoreResult = ref<OpResult | null>(null)

const restoreTypeOptions = computed(() => [
  { value: 'user', label: t('admin.ops.typeUser') },
  { value: 'org', label: t('admin.ops.typeOrg') },
  { value: 'project', label: t('admin.ops.typeProject') },
])

const actions = useAdminActions()

async function onResetGraphrag(): Promise<void> {
  resetResult.value = null
  try {
    await actions.resetGraphrag.mutateAsync(graphragConfigId.value.trim())
    resetResult.value = { text: t('admin.ops.graphragResetSuccess'), ok: true }
    graphragConfigId.value = ''
  } catch {
    resetResult.value = { text: t('admin.ops.resetFailed'), ok: false }
  }
}

async function onRestore(): Promise<void> {
  restoreResult.value = null
  try {
    await actions.restoreResource.mutateAsync({ type: restoreType.value, id: restoreId.value.trim() })
    restoreResult.value = { text: t('admin.ops.restoreSuccess'), ok: true }
    restoreId.value = ''
  } catch {
    restoreResult.value = { text: t('admin.ops.restoreFailed'), ok: false }
  }
}
</script>

<style scoped>
.admin-ops__section {
  margin: 1.5rem 0;
}
.admin-ops__heading {
  font-size: 1.125rem;
  font-weight: 600;
  margin: 0 0 0.75rem;
}
.admin-ops__form {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
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
