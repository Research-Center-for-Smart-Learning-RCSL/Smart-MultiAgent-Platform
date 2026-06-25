<template>
  <header class="chat-header">
    <SButton
      variant="ghost"
      icon-only
      size="sm"
      :aria-label="t('conversation.chatroom.back')"
      @click="emit('back')"
    >
      <ArrowLeftIcon class="w-5 h-5" />
    </SButton>

    <SButton
      v-if="isMobile"
      variant="ghost"
      icon-only
      size="sm"
      :aria-label="t('conversation.chatroom.agents')"
      @click="emit('toggle-agents')"
    >
      <CpuChipIcon class="w-5 h-5" />
    </SButton>

    <ChatBubbleLeftRightIcon
      v-else
      class="chat-header__icon"
    />
    <h1 class="chat-header__name">
      {{ roomName }}
    </h1>

    <span
      class="chat-header__pill"
      :class="pill.cls"
    >
      <component
        :is="pill.icon"
        class="chat-header__pill-icon"
        :class="{ 'chat-header__pill-icon--spin': pill.spin }"
      />
      {{ pill.label }}
    </span>

    <div class="chat-header__spacer" />

    <!-- Desktop: individual action buttons. -->
    <template v-if="!isMobile">
      <SButton
        variant="ghost"
        icon-only
        size="sm"
        :aria-label="t('conversation.chatroom.search')"
        @click="emit('search')"
      >
        <MagnifyingGlassIcon class="w-5 h-5" />
      </SButton>
      <SButton
        variant="ghost"
        icon-only
        size="sm"
        :aria-label="t('conversation.chatroom.settingsLabel')"
        @click="emit('settings')"
      >
        <Cog6ToothIcon class="w-5 h-5" />
      </SButton>
      <SButton
        variant="ghost"
        icon-only
        size="sm"
        data-testid="open-export"
        :aria-label="t('conversation.chatroom.export')"
        @click="emit('export')"
      >
        <ArrowDownTrayIcon class="w-5 h-5" />
      </SButton>
    </template>

    <!-- People drawer toggle: mobile + tablet (presence rail only exists at lg+). -->
    <SButton
      v-if="!isDesktop"
      variant="ghost"
      icon-only
      size="sm"
      :aria-label="t('conversation.chatroom.people')"
      @click="emit('toggle-people')"
    >
      <UsersIcon class="w-5 h-5" />
    </SButton>

    <!-- Overflow menu: mobile only (its actions have dedicated buttons above
         on tablet/desktop). -->
    <SDropdown
      v-if="isMobile"
      :items="overflowItems"
      placement="bottom-end"
      @select="onOverflow"
    >
      <template #trigger>
        <SButton
          variant="ghost"
          icon-only
          size="sm"
          :aria-label="t('conversation.chatroom.more')"
        >
          <EllipsisVerticalIcon class="w-5 h-5" />
        </SButton>
      </template>
    </SDropdown>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeftIcon,
  ChatBubbleLeftRightIcon,
  MagnifyingGlassIcon,
  Cog6ToothIcon,
  ArrowDownTrayIcon,
  CpuChipIcon,
  UsersIcon,
  EllipsisVerticalIcon,
  SignalIcon,
  SignalSlashIcon,
  ArrowPathIcon,
} from '@heroicons/vue/24/outline'
import { SButton, SDropdown } from '@shared/ui'

const props = defineProps<{
  roomName: string
  connectionState: 'connecting' | 'live' | 'reconnecting'
  isMobile: boolean
  isDesktop: boolean
}>()

const emit = defineEmits<{
  back: []
  search: []
  settings: []
  export: []
  'toggle-agents': []
  'toggle-people': []
}>()

const { t } = useI18n()

// 'connecting' (never opened yet) reuses the Offline visual — the channel is
// not yet usable; 'reconnecting' (was live, dropped) gets its own spinning,
// warning-toned state so a transient drop does not read as a hard failure.
const pill = computed(() => {
  switch (props.connectionState) {
    case 'live':
      return {
        icon: SignalIcon,
        label: t('conversation.chatroom.live'),
        cls: 'chat-header__pill--on',
        spin: false,
      }
    case 'reconnecting':
      return {
        icon: ArrowPathIcon,
        label: t('conversation.chatroom.reconnecting'),
        cls: 'chat-header__pill--reconnecting',
        spin: true,
      }
    default:
      return {
        icon: SignalSlashIcon,
        label: t('conversation.chatroom.offline'),
        cls: 'chat-header__pill--off',
        spin: false,
      }
  }
})

const overflowItems = computed(() => [
  { key: 'search', label: t('conversation.chatroom.search'), icon: MagnifyingGlassIcon },
  { key: 'settings', label: t('conversation.chatroom.settingsLabel'), icon: Cog6ToothIcon },
  { key: 'export', label: t('conversation.chatroom.export'), icon: ArrowDownTrayIcon },
])

function onOverflow(key: string): void {
  if (key === 'search') emit('search')
  else if (key === 'settings') emit('settings')
  else if (key === 'export') emit('export')
}
</script>

<style scoped>
.chat-header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 48px;
  padding: 0 16px;
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
}

.chat-header__icon {
  width: 20px;
  height: 20px;
  color: var(--color-accent);
  flex-shrink: 0;
}

.chat-header__name {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-fg);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-header__pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: 12px;
}

.chat-header__pill-icon {
  width: 12px;
  height: 12px;
}

.chat-header__pill--on {
  color: var(--color-success);
  background: var(--color-success-tint, #dcfce7);
}

.chat-header__pill--off {
  color: var(--color-danger);
  background: var(--color-danger-tint, #fee2e2);
}

.chat-header__pill--reconnecting {
  color: var(--color-warning);
  background: var(--color-warning-tint, #fef3c7);
}

.chat-header__pill-icon--spin {
  animation: chat-header-pill-spin 1s linear infinite;
}

@keyframes chat-header-pill-spin {
  to {
    transform: rotate(360deg);
  }
}

.chat-header__spacer {
  flex: 1;
}
</style>
