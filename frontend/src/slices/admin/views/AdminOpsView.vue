<template>
  <section class="admin-ops">
    <h1>{{ $t('admin.ops.title') }}</h1>

    <div class="admin-ops__section">
      <h2>{{ $t('admin.ops.graphragReset') }}</h2>
      <form @submit.prevent="onResetGraphrag">
        <input
          v-model="graphragConfigId"
          :placeholder="$t('admin.ops.configIdPlaceholder')"
          required
        >
        <button type="submit">
          {{ $t('admin.ops.reset') }}
        </button>
      </form>
      <p v-if="resetResult">
        {{ resetResult }}
      </p>
    </div>

    <div class="admin-ops__section">
      <h2>{{ $t('admin.ops.restore') }}</h2>
      <form @submit.prevent="onRestore">
        <select v-model="restoreType">
          <option value="org">
            {{ $t('admin.ops.typeOrg') }}
          </option>
          <option value="project">
            {{ $t('admin.ops.typeProject') }}
          </option>
          <option value="agent">
            {{ $t('admin.ops.typeAgent') }}
          </option>
          <option value="workflow">
            {{ $t('admin.ops.typeWorkflow') }}
          </option>
          <option value="chatroom">
            {{ $t('admin.ops.typeChatroom') }}
          </option>
          <option value="user">
            {{ $t('admin.ops.typeUser') }}
          </option>
        </select>
        <input
          v-model="restoreId"
          :placeholder="$t('admin.ops.resourceIdPlaceholder')"
          required
        >
        <button type="submit">
          {{ $t('admin.ops.restoreAction') }}
        </button>
      </form>
      <p v-if="restoreResult">
        {{ restoreResult }}
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAdminActions } from '../composables/useAdminActions'

const { t } = useI18n()

const graphragConfigId = ref('')
const resetResult = ref<string | null>(null)
const restoreType = ref('org')
const restoreId = ref('')
const restoreResult = ref<string | null>(null)

const actions = useAdminActions()

async function onResetGraphrag(): Promise<void> {
  resetResult.value = null
  try {
    await actions.resetGraphrag.mutateAsync(graphragConfigId.value.trim())
    resetResult.value = t('admin.ops.graphragResetSuccess')
    graphragConfigId.value = ''
  } catch {
    resetResult.value = t('admin.ops.resetFailed')
  }
}

async function onRestore(): Promise<void> {
  restoreResult.value = null
  try {
    await actions.restoreResource.mutateAsync({ type: restoreType.value, id: restoreId.value.trim() })
    restoreResult.value = t('admin.ops.restoreSuccess')
    restoreId.value = ''
  } catch {
    restoreResult.value = t('admin.ops.restoreFailed')
  }
}
</script>

<style scoped>
.admin-ops__section { margin: 1.5rem 0; }
.admin-ops__section form { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
</style>
