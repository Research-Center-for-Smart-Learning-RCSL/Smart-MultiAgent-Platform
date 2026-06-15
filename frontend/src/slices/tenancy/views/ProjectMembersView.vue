<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { useToast } from '@shared/composables'
import { projectsApi, type ProjectMember } from '../api/projects'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
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
  try {
    await projectsApi.invite(projectId(), inviteEmail.value.trim(), inviteRole.value)
    inviteEmail.value = ''
  } catch {
    toast.error(t('tenancy.members.inviteError'))
  }
}

async function setRole(uid: string, role: 'owner' | 'member'): Promise<void> {
  try {
    await projectsApi.setRole(projectId(), uid, role)
    await load()
  } catch {
    toast.error(t('tenancy.members.roleError'))
  }
}

async function remove(uid: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('tenancy.members.removeConfirm'),
      t('tenancy.members.removeConfirmTitle'),
      { confirmButtonText: t('tenancy.members.remove'), cancelButtonText: t('app.cancel'), type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await projectsApi.removeMember(projectId(), uid)
    await load()
  } catch {
    toast.error(t('identity.errors.generic'))
  }
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.members.title') }}</h1>
    <form @submit.prevent="invite">
      <label>
        {{ $t('tenancy.members.inviteLabel') }}
        <input
          v-model="inviteEmail"
          type="email"
          required
        >
      </label>
      <select
        v-model="inviteRole"
        :aria-label="$t('tenancy.members.role')"
      >
        <option value="member">
          {{ $t('tenancy.members.roleMember') }}
        </option>
        <option value="owner">
          {{ $t('tenancy.members.roleOwner') }}
        </option>
      </select>
      <button type="submit">
        {{ $t('tenancy.members.invite') }}
      </button>
    </form>
    <ul>
      <li
        v-for="m in members"
        :key="m.user_id"
      >
        {{ m.email }} — {{ m.role }}
        <button
          v-if="m.role === 'member'"
          @click="setRole(m.user_id, 'owner')"
        >
          {{ $t('tenancy.members.promote') }}
        </button>
        <button
          v-else
          @click="setRole(m.user_id, 'member')"
        >
          {{ $t('tenancy.members.demote') }}
        </button>
        <button @click="remove(m.user_id)">
          {{ $t('tenancy.members.remove') }}
        </button>
      </li>
    </ul>
  </main>
</template>
