<script setup lang="ts">
import { useI18n } from 'vue-i18n'

export interface MemberItem {
  user_id: string
  email: string
  role: 'owner' | 'member'
  is_original_creator?: boolean
}

const props = withDefaults(defineProps<{
  members: MemberItem[]
  inviteEmail: string
  inviteRole: 'owner' | 'member'
  isLoading?: boolean
  invitePending?: boolean
  errors?: Record<string, string>
}>(), {
  isLoading: false,
  invitePending: false,
  errors: () => ({}),
})

const emit = defineEmits<{
  invite: []
  remove: [userId: string]
  'change-role': [payload: { userId: string; role: 'owner' | 'member' }]
  'update:inviteEmail': [value: string]
  'update:inviteRole': [value: 'owner' | 'member']
}>()

const { t } = useI18n()
</script>

<template>
  <section>
    <form @submit.prevent="$emit('invite')">
      <label>
        {{ t('tenancy.members.inviteLabel') }}
        <input
          :value="inviteEmail"
          type="email"
          required
          :disabled="invitePending"
          @input="emit('update:inviteEmail', ($event.target as HTMLInputElement).value)"
        >
      </label>
      <p
        v-if="props.errors.email"
        class="field-error"
        role="alert"
      >
        {{ props.errors.email }}
      </p>
      <select
        :value="inviteRole"
        :aria-label="t('tenancy.members.role')"
        :disabled="invitePending"
        @change="emit('update:inviteRole', ($event.target as HTMLSelectElement).value as 'owner' | 'member')"
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
      {{ t('app.save') }}…
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
