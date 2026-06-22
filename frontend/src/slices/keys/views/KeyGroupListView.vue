<script setup lang="ts">
import { SPageHeader } from '@shared/ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables'
import { useKeyGroups } from '../composables/useKeyGroups'

const { t } = useI18n()
const route = useRoute()
const { confirm } = useConfirmDialog()
const projectId = computed(() => route.params.projectId as string)
const { groups, error, reload, create, remove } = useKeyGroups(() => projectId.value)
const newName = ref('')
const busy = ref(false)

async function onCreate() {
  const n = newName.value.trim()
  if (!n) return
  await create(n)
  newName.value = ''
}

async function onRemove(id: string): Promise<void> {
  const ok = await confirm({
    title: t('keys.groups.deleteConfirmTitle'),
    message: t('keys.groups.deleteConfirm'),
    confirmLabel: t('keys.groups.delete'),
    variant: 'error',
  })
  if (!ok) return
  busy.value = true
  try { await remove(id) } finally { busy.value = false }
}

onMounted(reload)
watch(projectId, reload)
</script>

<template>
  <main class="key-group-list-view">
    <SPageHeader :title="$t('keys.groups.listTitle')" />
    <p
      v-if="error"
      class="error"
      role="alert"
    >
      {{ error }}
    </p>
    <form @submit.prevent="onCreate">
      <input
        v-model="newName"
        :placeholder="$t('keys.groups.namePlaceholder')"
        data-testid="group-name"
      >
      <button
        type="submit"
        class="btn btn-primary"
        data-testid="group-create"
      >
        {{ $t('keys.groups.create') }}
      </button>
    </form>
    <ul data-testid="group-list">
      <li
        v-for="g in groups"
        :key="g.id"
      >
        <router-link
          :to="{ name: 'keys.groupDetail', params: { projectId, id: g.id } }"
        >
          {{ g.name }}
        </router-link>
        <button
          class="btn btn-danger btn-sm"
          :disabled="busy"
          @click="onRemove(g.id)"
        >
          {{ $t('keys.groups.delete') }}
        </button>
      </li>
      <li
        v-if="groups.length === 0"
        class="empty"
      >
        {{ $t('keys.groups.empty') }}
      </li>
    </ul>
  </main>
</template>
