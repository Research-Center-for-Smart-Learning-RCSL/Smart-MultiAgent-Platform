<template>
  <aside class="presence">
    <p class="presence__header">
      {{ t('conversation.chatroom.onlineCount', { count: onlineUsers.length }) }}
    </p>
    <ul class="presence__list">
      <li
        v-for="u in onlineUsers"
        :key="u.id"
        class="presence-user"
      >
        <span class="presence-user__avatar">
          <SAvatar
            :name="u.displayName || u.id"
            size="sm"
          />
          <span class="presence-user__dot" />
        </span>

        <template v-if="u.isYou && viewerIsGuest && !editingName">
          <span class="presence-user__name">{{ viewerName || labelOf(u) }}</span>
          <SButton
            variant="ghost"
            icon-only
            size="sm"
            :aria-label="t('conversation.guest.editName')"
            @click="startEditName"
          >
            <PencilIcon class="w-3.5 h-3.5" />
          </SButton>
        </template>

        <template v-else-if="u.isYou && viewerIsGuest && editingName">
          <form
            class="presence-user__edit"
            @submit.prevent="saveDisplayName"
          >
            <SInput
              ref="editInputRef"
              v-model="editNameValue"
              size="sm"
              :maxlength="100"
              :placeholder="t('conversation.guest.displayNamePlaceholder')"
            />
            <SButton
              type="submit"
              variant="primary"
              size="sm"
              :disabled="!editNameValue.trim()"
            >
              {{ t('conversation.chatroom.save') }}
            </SButton>
            <SButton
              variant="ghost"
              size="sm"
              @click="cancelEditName"
            >
              {{ t('conversation.chatroom.cancel') }}
            </SButton>
          </form>
        </template>

        <template v-else>
          <span class="presence-user__name">{{ labelOf(u) }}</span>
        </template>

        <SBadge
          v-if="u.isGuest && !(u.isYou && editingName)"
          variant="neutral"
          size="sm"
          data-testid="presence-guest-badge"
        >
          {{ t('conversation.chatroom.guestBadge') }}
        </SBadge>

        <span
          v-if="u.isYou && !editingName"
          class="presence-user__you"
        >{{ t('conversation.chatroom.you') }}</span>

        <span
          v-if="u.removable"
          class="presence-user__actions"
        >
          <SButton
            variant="ghost"
            icon-only
            size="sm"
            data-testid="presence-remove-guest"
            :aria-label="t('conversation.chatroom.removeGuestNamed', { name: labelOf(u) })"
            @click="emit('remove-guest', u.id, labelOf(u))"
          >
            <UserMinusIcon class="w-3.5 h-3.5" />
          </SButton>
          <SButton
            variant="ghost"
            icon-only
            size="sm"
            data-testid="presence-ban-guest"
            :aria-label="t('conversation.chatroom.banGuestNamed', { name: labelOf(u) })"
            @click="emit('ban-guest', u.id, labelOf(u))"
          >
            <NoSymbolIcon class="w-3.5 h-3.5" />
          </SButton>
        </span>
      </li>
    </ul>

    <SDivider />

    <p class="presence__header">
      {{ t('conversation.chatroom.agentStatusHeader') }}
    </p>
    <ul class="presence__list">
      <ChatroomAgentStatusItem
        v-for="a in agents"
        :key="a.id"
        :agent="a"
      />
    </ul>
  </aside>
</template>

<script setup lang="ts">
import { nextTick, ref, useTemplateRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { NoSymbolIcon, PencilIcon, UserMinusIcon } from '@heroicons/vue/24/outline'
import { SAvatar, SBadge, SButton, SDivider, SInput } from '@shared/ui'
import ChatroomAgentStatusItem, {
  type AgentStatusEntry,
} from './ChatroomAgentStatusItem.vue'

const props = withDefaults(
  defineProps<{
    onlineUsers: Array<{
      id: string
      isYou: boolean
      displayName?: string | null
      isGuest?: boolean
      // A moderator viewing an anonymous guest session (roster kind
      // `guest_session`); a registered guest is never removable here ([R13.07a]).
      removable?: boolean
    }>
    agents: AgentStatusEntry[]
    viewerIsGuest?: boolean
    viewerName?: string
  }>(),
  { viewerIsGuest: false, viewerName: '' },
)

const emit = defineEmits<{
  'update-display-name': [name: string]
  'remove-guest': [id: string, name: string]
  'ban-guest': [id: string, name: string]
}>()

const { t } = useI18n()

type Row = (typeof props.onlineUsers)[number]

function labelOf(u: Row): string {
  return u.displayName || u.id.slice(0, 8)
}

const editingName = ref(false)
const editNameValue = ref('')
const editInputRef = useTemplateRef<InstanceType<typeof SInput>>('editInputRef')

function startEditName(): void {
  editNameValue.value = props.viewerName ?? ''
  editingName.value = true
  void nextTick(() => {
    const el = editInputRef.value?.$el as HTMLElement | null
    const input = el?.querySelector('input') ?? el
    input?.focus()
  })
}

function cancelEditName(): void {
  editingName.value = false
}

function saveDisplayName(): void {
  const trimmed = editNameValue.value.trim()
  if (!trimmed) return
  emit('update-display-name', trimmed)
  editingName.value = false
}
</script>

<style scoped>
.presence {
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  padding: var(--space-4);
  overflow-y: auto;
  height: 100%;
}

.presence__header {
  font-size: 11px;
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
  margin: var(--space-3) 0 var(--space-2);
}

.presence__header:first-child {
  margin-top: 0;
}

.presence__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.presence-user {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 36px;
}

.presence-user__avatar {
  position: relative;
  display: inline-flex;
  flex-shrink: 0;
}

.presence-user__dot {
  position: absolute;
  right: -1px;
  bottom: -1px;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-success);
  border: 1px solid var(--color-surface);
}

.presence-user__name {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}

.presence-user__you {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.presence-user__actions {
  display: inline-flex;
  margin-left: auto;
}

.presence-user__edit {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex: 1;
  min-width: 0;
}
</style>
