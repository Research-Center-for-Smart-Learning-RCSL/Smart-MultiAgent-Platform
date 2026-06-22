<script setup lang="ts">
import { SPageHeader } from '@shared/ui'
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { projectsApi, type Project, type ProjectOwnerType } from '../api/projects'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const scope = ref<ProjectOwnerType>((route.query.scope as ProjectOwnerType) || 'user')
const ownerId = ref<string>((route.query.id as string) || '')
const projects = ref<Project[]>([])
const name = ref('')
const loading = ref(false)

async function load(): Promise<void> {
  if (!ownerId.value) {
    projects.value = []
    return
  }
  loading.value = true
  try {
    const { data } = await projectsApi.list(scope.value, ownerId.value)
    projects.value = data
  } catch {
    toast.error(t('tenancy.projects.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function create(): Promise<void> {
  if (!name.value.trim() || !ownerId.value) return
  try {
    await projectsApi.create(scope.value, ownerId.value, name.value.trim())
    name.value = ''
    await load()
  } catch {
    toast.error(t('tenancy.projects.createFailed'))
  }
}

watch([scope, ownerId], load)
onMounted(load)
</script>

<template>
  <main>
    <SPageHeader :title="$t('tenancy.projects.listTitle')" />
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
      <button
        type="submit"
        class="btn btn-primary"
      >
        {{ $t('tenancy.projects.create') }}
      </button>
    </form>
    <p v-if="loading">
      {{ $t('tenancy.projects.loading') }}
    </p>
    <ul v-else-if="projects.length">
      <li
        v-for="p in projects"
        :key="p.id"
      >
        <router-link :to="{ name: 'tenancy.projectDetail', params: { id: p.id } }">
          {{ p.name }}
        </router-link>
      </li>
    </ul>
    <p v-else>
      {{ $t('tenancy.projects.empty') }}
    </p>
  </main>
</template>
