<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { projectsApi, type Project } from '../api/projects'

const route = useRoute()
const router = useRouter()
const project = ref<Project | null>(null)

async function load(): Promise<void> {
  const { data } = await projectsApi.get(route.params.id as string)
  project.value = data
}

async function remove(): Promise<void> {
  if (!project.value) return
  await projectsApi.remove(project.value.id)
  router.push({ name: 'tenancy.projectList' })
}

onMounted(load)
</script>

<template>
  <main v-if="project">
    <h1>{{ project.name }}</h1>
    <p>{{ project.owner_type }} / {{ project.owner_id }}</p>
    <router-link :to="{ name: 'tenancy.projectMembers', params: { id: project.id } }">
      {{ $t('tenancy.projects.members') }}
    </router-link>
    <button @click="remove">{{ $t('tenancy.orgs.delete') }}</button>
  </main>
</template>
