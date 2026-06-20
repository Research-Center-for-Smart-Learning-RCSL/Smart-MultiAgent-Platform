<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog, useToast } from '@shared/composables'
import { orgsApi, type OrgMember } from '../api/orgs'
import MemberListPanel from '../components/MemberListPanel.vue'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const { confirm } = useConfirmDialog()
const members = ref<OrgMember[]>([])
const inviteEmail = ref('')
const inviteRole = ref<'owner' | 'member'>('member')
const invitePending = ref(false)

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

async function onInvite(): Promise<void> {
  if (!inviteEmail.value.trim()) return
  invitePending.value = true
  try {
    await orgsApi.invite(orgId(), inviteEmail.value.trim(), inviteRole.value)
    inviteEmail.value = ''
    await load()
  } catch {
    toast.error(t('tenancy.members.inviteError'))
  } finally {
    invitePending.value = false
  }
}

async function onChangeRole(payload: { userId: string; role: 'owner' | 'member' }): Promise<void> {
  try {
    await orgsApi.setRole(orgId(), payload.userId, payload.role)
    await load()
  } catch {
    toast.error(t('tenancy.members.roleError'))
  }
}

async function onRemove(uid: string): Promise<void> {
  const ok = await confirm({ title: t('tenancy.members.removeConfirmTitle'), message: t('tenancy.members.removeConfirm'), variant: 'warning', confirmLabel: t('tenancy.members.remove'), cancelLabel: t('app.cancel') })
  if (!ok) return
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
    <MemberListPanel
      v-model:invite-email="inviteEmail"
      v-model:invite-role="inviteRole"
      :members="members"
      :invite-pending="invitePending"
      @invite="onInvite"
      @remove="onRemove"
      @change-role="onChangeRole"
    />
  </main>
</template>
