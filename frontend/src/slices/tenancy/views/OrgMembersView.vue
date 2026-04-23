<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { orgsApi, type OrgMember } from '../api/orgs'

const route = useRoute()
const members = ref<OrgMember[]>([])
const inviteEmail = ref('')
const inviteRole = ref<'owner' | 'member'>('member')

function orgId(): string {
  return route.params.id as string
}

async function load(): Promise<void> {
  const { data } = await orgsApi.listMembers(orgId())
  members.value = data
}

async function invite(): Promise<void> {
  if (!inviteEmail.value.trim()) return
  await orgsApi.invite(orgId(), inviteEmail.value.trim(), inviteRole.value)
  inviteEmail.value = ''
}

async function setRole(uid: string, role: 'owner' | 'member'): Promise<void> {
  await orgsApi.setRole(orgId(), uid, role)
  await load()
}

async function remove(uid: string): Promise<void> {
  await orgsApi.removeMember(orgId(), uid)
  await load()
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.members.title') }}</h1>
    <form @submit.prevent="invite">
      <label>{{ $t('tenancy.members.inviteLabel') }}</label>
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
        <span v-if="m.is_original_creator">★ {{ $t('tenancy.orgs.originalCreator') }}</span>
        <template v-if="!m.is_original_creator">
          <button v-if="m.role === 'member'" @click="setRole(m.user_id, 'owner')">
            {{ $t('tenancy.members.promote') }}
          </button>
          <button v-else @click="setRole(m.user_id, 'member')">
            {{ $t('tenancy.members.demote') }}
          </button>
          <button @click="remove(m.user_id)">{{ $t('tenancy.members.remove') }}</button>
        </template>
      </li>
    </ul>
  </main>
</template>
