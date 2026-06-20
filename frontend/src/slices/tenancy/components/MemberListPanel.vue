<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

export interface MemberItem {
  user_id: string
  email: string
  role: 'owner' | 'member'
  is_original_creator?: boolean
}

withDefaults(defineProps<{
  members: MemberItem[]
  isLoading?: boolean
  invitePending?: boolean
  errors?: Record<string, string>
}>(), {
  isLoading: false,
  invitePending: false,
  errors: () => ({}),
})

const emit = defineEmits<{
  invite: [payload: { email: string; role: 'owner' | 'member' }]
  remove: [userId: string]
  'change-role': [payload: { userId: string; role: 'owner' | 'member' }]
}>()

const { t } = useI18n()
const inviteEmail = ref('')
const inviteRole = ref<'owner' | 'member'>('member')

function onInvite(): void {
  if (!inviteEmail.value.trim()) return
  emit('invite', { email: inviteEmail.value.trim(), role: inviteRole.value })
  inviteEmail.value = ''
}
</script>

<template>
  <section>
    <form @submit.prevent="onInvite">
      <label>
        {{ t('tenancy.members.inviteLabel') }}
        <input
          v-model="inviteEmail"
          type="email"
          required
          :disabled="invitePending"
        >
      </label>
      <select
        v-model="inviteRole"
        :aria-label="t('tenancy.members.role')"
        :disabled="invitePending"
      >
        <option value="member">
          {{ t('tenancy.members.roleMember') }}
        </option>
        <option value="owner">
          {{ t('tenancy.members.roleOwner') }}
        </option>
      </select>
      <button
        type="submit"
        :disabled="invitePending"
      >
        {{ t('tenancy.members.invite') }}
      </button>
    </form>

    <p v-if="isLoading">
      {{ t('app.loading') ?? 'Loading...' }}
    </p>

    <ul v-else>
      <li
        v-for="m in members"
        :key="m.user_id"
      >
        {{ m.email }} — {{ m.role }}
        <span v-if="m.is_original_creator">★ {{ t('tenancy.orgs.originalCreator') }}</span>
        <template v-if="!m.is_original_creator">
          <button
            v-if="m.role === 'member'"
            @click="emit('change-role', { userId: m.user_id, role: 'owner' })"
          >
            {{ t('tenancy.members.promote') }}
          </button>
          <button
            v-else
            @click="emit('change-role', { userId: m.user_id, role: 'member' })"
          >
            {{ t('tenancy.members.demote') }}
          </button>
          <button @click="emit('remove', m.user_id)">
            {{ t('tenancy.members.remove') }}
          </button>
        </template>
      </li>
    </ul>
  </section>
</template>
