<script setup lang="ts">
import { computed, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  Cog6ToothIcon,
  ComputerDesktopIcon,
  ShieldExclamationIcon,
  ArrowRightOnRectangleIcon,
  UserCircleIcon,
  SunIcon,
  MoonIcon,
} from '@heroicons/vue/24/outline'
import { SAvatar, SDropdown } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'
import { useBreakpoint } from '@shared/composables/useBreakpoint'
import { useTheme, type Theme } from '@shared/composables/useTheme'

const { t } = useI18n()
const router = useRouter()
const session = useSessionStore()
const { isMobile } = useBreakpoint()
const { theme, cycleTheme } = useTheme()

const themeIcons: Record<Theme, Component> = {
  light: SunIcon,
  dark: MoonIcon,
  system: ComputerDesktopIcon,
}

const displayName = computed(() => session.me?.display_name?.trim() ?? '')
// Avatar initial and menu header prefer the display name; email is the fallback
// label (and a secondary line) since it is always present.
const avatarName = computed(() => displayName.value || session.me?.email || '')

const menuItems = computed(() => {
  const items: Array<{
    key: string
    label: string
    icon?: Component
    danger?: boolean
    disabled?: boolean
    divider?: boolean
  }> = [
    {
      key: 'name-header',
      label: displayName.value || (session.me?.email ?? ''),
      disabled: true,
    },
  ]

  if (displayName.value) {
    items.push({
      key: 'email-subheader',
      label: session.me?.email ?? '',
      disabled: true,
    })
  }

  items.push(
    { key: 'div-header', label: '', divider: true },
    {
      key: 'profile',
      label: t('app.userMenu.profile'),
      icon: UserCircleIcon,
    },
    {
      key: 'account',
      label: t('app.userMenu.account'),
      icon: Cog6ToothIcon,
    },
    {
      key: 'sessions',
      label: t('app.userMenu.sessions'),
      icon: ComputerDesktopIcon,
    },
  )

  if (isMobile.value) {
    items.push({ key: 'div-theme', label: '', divider: true })
    items.push({
      key: 'theme',
      label: t('app.userMenu.theme', { theme: t(`app.theme.${theme.value}`) }),
      icon: themeIcons[theme.value],
    })
  }

  if (session.me?.is_admin) {
    items.push({ key: 'div-admin', label: '', divider: true })
    items.push({
      key: 'admin',
      label: t('app.userMenu.admin'),
      icon: ShieldExclamationIcon,
    })
  }

  items.push({ key: 'div-logout', label: '', divider: true })
  items.push({
    key: 'logout',
    label: t('app.userMenu.logout'),
    icon: ArrowRightOnRectangleIcon,
    danger: true,
  })

  return items
})

async function onSelect(key: string) {
  switch (key) {
    case 'profile':
      router.push({ name: 'identity.profile' })
      break
    case 'account':
      router.push({ name: 'identity.changePassword' })
      break
    case 'sessions':
      router.push({ name: 'identity.sessions' })
      break
    case 'admin':
      router.push({ name: 'admin.home' })
      break
    case 'theme':
      cycleTheme()
      break
    case 'logout':
      await session.logout()
      router.push({ name: 'identity.login' })
      break
  }
}
</script>

<template>
  <SDropdown
    :items="menuItems"
    placement="bottom-end"
    width="220px"
    @select="onSelect"
  >
    <template #trigger>
      <button
        class="user-menu__trigger"
        type="button"
        :aria-label="t('app.userMenu.account')"
      >
        <SAvatar
          :name="avatarName"
          size="sm"
        />
      </button>
    </template>
  </SDropdown>
</template>

<style scoped>
.user-menu__trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  margin: 0;
  background: none;
  border: none;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: box-shadow var(--transition-fast);
}

.user-menu__trigger:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
</style>
