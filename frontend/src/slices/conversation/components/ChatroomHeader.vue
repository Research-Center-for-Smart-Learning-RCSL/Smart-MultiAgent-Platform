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

    <!-- Agents toggle: mobile (drawer) and compact desktop (overlay panel). -->
    <SButton
      v-if="isMobile || isCompact"
      variant="ghost"
      icon-only
      size="sm"
      :aria-label="t('conversation.chatroom.agents')"
      :aria-expanded="isCompact ? agentsOpen : undefined"
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

    <ObserverDisclosureChip v-if="observersPresent" />

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
        v-if="showExport"
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

    <!-- People toggle: mobile + tablet as a drawer, compact desktop as an
         overlay panel. Only the full three-column layout has a standing rail. -->
    <SButton
      v-if="!isDesktop || isCompact"
      variant="ghost"
      icon-only
      size="sm"
      :aria-label="t('conversation.chatroom.people')"
      :aria-expanded="isCompact ? peopleOpen : undefined"
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
import ObserverDisclosureChip from './ObserverDisclosureChip.vue'

const props = withDefaults(
  defineProps<{
    roomName: string
    connectionState: 'connecting' | 'live' | 'reconnecting' | 'degraded' | 'limited'
    isMobile: boolean
    isDesktop: boolean
    // 1024-1279 (07-conversation.md:238-252): both rails are overlay panels
    // rather than tracks, so the header carries their toggles even though this
    // band is `isDesktop`. Without it the panels would have no way to be
    // opened, because the agents toggle is otherwise mobile-only and the people
    // toggle is otherwise below-desktop only.
    isCompact?: boolean
    // Only meaningful in the compact band, where the toggles drive an overlay
    // panel rather than an SDrawer: a toggle that shows and hides a panel has
    // to say which state it is in. Below lg the SDrawer owns that semantics.
    agentsOpen?: boolean
    peopleOpen?: boolean
    observersPresent?: boolean
    // Advisory (R5.05): guests may not export (docs/UI/07-conversation.md); the
    // server enforces the 403 regardless. Defaults to shown — an absent Boolean
    // prop is coerced to false by Vue, so the default must be explicit.
    canExport?: boolean
  }>(),
  { canExport: true, isCompact: false, agentsOpen: false, peopleOpen: false },
)

const emit = defineEmits<{
  back: []
  search: []
  settings: []
  export: []
  'toggle-agents': []
  'toggle-people': []
}>()

const { t } = useI18n()

const showExport = computed(() => props.canExport)

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
    case 'degraded':
      // Repeated reconnects failed — we are on the REST polling fallback. Red
      // (offline-toned) but still spinning, since the socket keeps retrying.
      return {
        icon: ArrowPathIcon,
        label: t('conversation.chatroom.degraded'),
        cls: 'chat-header__pill--off',
        spin: true,
      }
    case 'limited':
      // The server is refusing this user's excess connections (R19.03).
      // Deliberately not spinning: the socket does keep retrying, but waiting
      // is not what resolves this — closing another tab is.
      return {
        icon: SignalSlashIcon,
        label: t('conversation.chatroom.limited'),
        cls: 'chat-header__pill--reconnecting',
        spin: false,
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
  ...(showExport.value
    ? [{ key: 'export', label: t('conversation.chatroom.export'), icon: ArrowDownTrayIcon }]
    : []),
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
  gap: var(--space-2);
  height: 48px;
  padding: 0 var(--space-4);
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
  font-size: var(--font-size-md);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-header__pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-0-5) var(--space-2-5);
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
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
