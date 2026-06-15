<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useKeyGroups } from '../composables/useKeyGroups'

const route = useRoute()
const projectId = computed(() => route.params.projectId as string)
const { groups, error, reload, create, remove } = useKeyGroups(() => projectId.value)
const newName = ref('')

async function onCreate() {
  const n = newName.value.trim()
  if (!n) return
  await create(n)
  newName.value = ''
}

onMounted(reload)
watch(projectId, reload)
</script>

<template>
  <main class="key-group-list-view">
    <h1>{{ $t('keys.groups.listTitle') }}</h1>
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
        <button @click="remove(g.id)">
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
