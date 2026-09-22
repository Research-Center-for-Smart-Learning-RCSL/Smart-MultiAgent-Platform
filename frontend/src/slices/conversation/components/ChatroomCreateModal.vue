<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  SModal,
  SFormField,
  SInput,
  SToggle,
  SButton,
} from '@shared/ui'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'

defineProps<{
  open: boolean
  name: string
  error: string | null
  flags: {
    allow_org_members: boolean
    allow_project_members: boolean
    allow_project_owners_only: boolean
    allow_guest_links: boolean
  }
  isPending: boolean
}>()

const emit = defineEmits<{
  close: []
  submit: []
  'update:name': [value: string]
  'update:flags': [key: string, value: boolean]
}>()

const { t } = useI18n()
</script>

<template>
  <SModal
    :open="open"
    :title="t('conversation.chatrooms.createTitle')"
    size="md"
    @close="emit('close')"
  >
    <form @submit.prevent="emit('submit')">
      <SFormField
        :label="t('conversation.chatrooms.colName')"
        name="chatroomName"
        v-bind="error ? { error } : {}"
        required
      >
        <SInput
          :model-value="name"
          :error="!!error"
          :disabled="isPending"
          :maxlength="INPUT_LIMITS.NAME"
          @update:model-value="emit('update:name', String($event))"
        />
      </SFormField>

      <fieldset class="access-fieldset">
        <legend class="access-fieldset__legend">
          {{ t('conversation.chatrooms.colAccess') }}
        </legend>

        <div
          class="access-row"
          :class="{ 'access-row--dimmed': flags.allow_project_owners_only }"
        >
          <span>{{ t('conversation.settings.allowOrgMembers') }}</span>
          <SToggle
            :model-value="flags.allow_org_members"
            :disabled="flags.allow_project_owners_only"
            @update:model-value="emit('update:flags', 'allow_org_members', $event)"
          />
        </div>
        <div
          class="access-row"
          :class="{ 'access-row--dimmed': flags.allow_project_owners_only }"
        >
          <span>{{ t('conversation.settings.allowProjectMembers') }}</span>
          <SToggle
            :model-value="flags.allow_project_members"
            :disabled="flags.allow_project_owners_only"
            @update:model-value="emit('update:flags', 'allow_project_members', $event)"
          />
        </div>
        <div class="access-row">
          <span>{{ t('conversation.settings.allowProjectOwnersOnly') }}</span>
          <SToggle
            :model-value="flags.allow_project_owners_only"
            @update:model-value="emit('update:flags', 'allow_project_owners_only', $event)"
          />
        </div>
        <div class="access-row">
          <span>{{ t('conversation.settings.allowGuestLinks') }}</span>
          <SToggle
            :model-value="flags.allow_guest_links"
            @update:model-value="emit('update:flags', 'allow_guest_links', $event)"
          />
        </div>
      </fieldset>
    </form>

    <template #footer>
      <SButton
        variant="secondary"
        :disabled="isPending"
        @click="emit('close')"
      >
        {{ t('conversation.chatrooms.cancel') }}
      </SButton>
      <SButton
        variant="primary"
        :loading="isPending"
        :disabled="isPending || !name.trim()"
        @click="emit('submit')"
      >
        {{ t('conversation.chatrooms.create') }}
      </SButton>
    </template>
  </SModal>
</template>

<style scoped>
.access-fieldset {
  border: none;
  margin: var(--space-2) 0 0;
  padding: 0;
}

.access-fieldset__legend {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
  margin-bottom: var(--space-2);
}

.access-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) 0;
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}

.access-row--dimmed {
  opacity: 0.5;
}
</style>
