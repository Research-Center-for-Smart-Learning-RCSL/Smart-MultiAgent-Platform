<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { projectsApi, type Project, type ProjectOwnerType } from '../api/projects'
import { useRoute } from 'vue-router'

const route = useRoute()
const scope = ref<ProjectOwnerType>((route.query.scope as ProjectOwnerType) || 'user')
const ownerId = ref<string>((route.query.id as string) || '')
const projects = ref<Project[]>([])
const name = ref('')

async function load(): Promise<void> {
  if (!ownerId.value) {
    projects.value = []
    return
  }
  const { data } = await projectsApi.list(scope.value, ownerId.value)
  projects.value = data
}

async function create(): Promise<void> {
  if (!name.value.trim() || !ownerId.value) return
  await projectsApi.create(scope.value, ownerId.value, name.value.trim())
  name.value = ''
  await load()
}

watch([scope, ownerId], load)
onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.projects.listTitle') }}</h1>
    <label>
      {{ $t('tenancy.projects.scope') }}
      <select v-model="scope">
        <option value="user">{{ $t('tenancy.projects.user') }}</option>
        <option value="org">{{ $t('tenancy.projects.org') }}</option>
      </select>
    </label>
    <input
      v-model="ownerId"
      :placeholder="$t('tenancy.projects.ownerIdPlaceholder')"
    >
    <form @submit.prevent="create">
      <input
        v-model="name"
        :placeholder="$t('tenancy.projects.namePlaceholder')"
      >
      <button type="submit">
        {{ $t('tenancy.projects.create') }}
      </button>
    </form>
    <ul>
      <li
        v-for="p in projects"
        :key="p.id"
      >
        <router-link :to="{ name: 'tenancy.projectDetail', params: { id: p.id } }">
          {{ p.name }}
        </router-link>
      </li>
    </ul>
  </main>
</template>
