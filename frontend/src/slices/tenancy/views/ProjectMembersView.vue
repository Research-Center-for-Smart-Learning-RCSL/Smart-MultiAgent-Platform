<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { projectsApi, type ProjectMember } from '../api/projects'

const route = useRoute()
const members = ref<ProjectMember[]>([])
const inviteEmail = ref('')
const inviteRole = ref<'owner' | 'member'>('member')

function projectId(): string {
  return route.params.id as string
}

async function load(): Promise<void> {
  const { data } = await projectsApi.listMembers(projectId())
  members.value = data
}

async function invite(): Promise<void> {
  if (!inviteEmail.value.trim()) return
  await projectsApi.invite(projectId(), inviteEmail.value.trim(), inviteRole.value)
  inviteEmail.value = ''
}

async function remove(uid: string): Promise<void> {
  await projectsApi.removeMember(projectId(), uid)
  await load()
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.members.title') }}</h1>
    <form @submit.prevent="invite">
      <input v-model="inviteEmail" type="email" required />
      <select v-model="inviteRole">
        <option value="member">member</option>
        <option value="owner">owner</option>
      </select>
      <button type="submit">{{ $t('tenancy.members.invite') }}</button>
    </form>
    <ul>
      <li v-for="m in members" :key="m.user_id">
        {{ m.email }} — {{ m.role }}
        <button @click="remove(m.user_id)">{{ $t('tenancy.members.remove') }}</button>
      </li>
    </ul>
  </main>
</template>
