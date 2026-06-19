<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { useToast } from '@shared/composables'
import { orgsApi, type OrgMember } from '../api/orgs'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const members = ref<OrgMember[]>([])
const inviteEmail = ref('')
const inviteRole = ref<'owner' | 'member'>('member')

function orgId(): string {
  return route.params.id as string
}

async function load(): Promise<void> {
  try {
    const { data } = await orgsApi.listMembers(orgId())
    members.value = data
  } catch {
    toast.error(t('tenancy.members.loadFailed'))
  }
}

async function invite(): Promise<void> {
  if (!inviteEmail.value.trim()) return
  try {
    await orgsApi.invite(orgId(), inviteEmail.value.trim(), inviteRole.value)
    inviteEmail.value = ''
  } catch {
    toast.error(t('tenancy.members.inviteError'))
  }
}

async function setRole(uid: string, role: 'owner' | 'member'): Promise<void> {
  try {
    await orgsApi.setRole(orgId(), uid, role)
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
    await orgsApi.removeMember(orgId(), uid)
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
        {{ m.user_id }} — {{ m.role }}
        <span v-if="m.is_original_creator">★ {{ $t('tenancy.orgs.originalCreator') }}</span>
        <template v-if="!m.is_original_creator">
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
        </template>
      </li>
    </ul>
  </main>
</template>
