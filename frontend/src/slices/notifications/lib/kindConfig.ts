// Central map of notification `kind` -> presentation (icon, tint circle colors,
// meta label) and optional navigational action. Keyed on the *production*
// NotificationKind values emitted by the backend
// (backend/contexts/notification/domain/models.py), which are dot-namespaced
// (e.g. "key.usage_threshold"), not the underscore forms used in early specs.
import type { Component } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import {
  BellIcon,
  ClipboardDocumentCheckIcon,
  EnvelopeIcon,
  ExclamationTriangleIcon,
  NoSymbolIcon,
  XCircleIcon,
} from '@heroicons/vue/24/outline'
import type { Notification } from '../api'

export interface KindAction {
  // i18n key for the link label; resolved in the component via $t().
  labelKey: string
  to: RouteLocationRaw
}

export interface KindConfig {
  icon: Component
  // CSS custom-property references so light/dark themes resolve automatically.
  tintBg: string
  iconColor: string
  // i18n key for the short kind label shown in the card meta line.
  labelKey: string
  // Optional contextual link; returns null when the metadata lacks the ids it
  // needs (e.g. a key notification with no key_id).
  action?: (n: Notification) => KindAction | null
}

function keyAction(n: Notification): KindAction | null {
  const keyId = n.metadata?.key_id
  if (typeof keyId !== 'string' || !keyId) return null
  return {
    labelKey: 'notifications.viewKey',
    to: { name: 'keys.detail', params: { id: keyId } },
  }
}

const KIND_CONFIG: Record<string, KindConfig> = {
  'key.usage_threshold': {
    icon: ExclamationTriangleIcon,
    tintBg: 'var(--color-warning-tint)',
    iconColor: 'var(--color-warning)',
    labelKey: 'notifications.kindKeyUsage',
    action: keyAction,
  },
  'key.test_failed': {
    icon: XCircleIcon,
    tintBg: 'var(--color-danger-tint)',
    iconColor: 'var(--color-danger)',
    labelKey: 'notifications.kindKeyTest',
    action: keyAction,
  },
  'invite.received': {
    icon: EnvelopeIcon,
    tintBg: 'var(--color-info-tint)',
    iconColor: 'var(--color-accent)',
    labelKey: 'notifications.kindInvite',
    action: () => ({ labelKey: 'notifications.viewInvites', to: { name: 'tenancy.inbox' } }),
  },
  'admin.ban_reason': {
    icon: NoSymbolIcon,
    tintBg: 'var(--color-danger-tint)',
    iconColor: 'var(--color-danger)',
    labelKey: 'notifications.kindBan',
  },
  'approval.human_requested': {
    icon: ClipboardDocumentCheckIcon,
    tintBg: 'var(--color-info-tint)',
    iconColor: 'var(--color-accent)',
    labelKey: 'notifications.kindApproval',
  },
}

const FALLBACK_KIND: KindConfig = {
  icon: BellIcon,
  tintBg: 'var(--color-neutral-tint)',
  iconColor: 'var(--color-muted)',
  labelKey: 'notifications.kindGeneric',
}

export function kindConfig(kind: string): KindConfig {
  const cfg = KIND_CONFIG[kind]
  if (cfg) return cfg
  // Surface drift in dev: a backend NotificationKind without a UI mapping renders
  // the generic icon. Warning here turns that silent degradation into a visible
  // signal during development without adding prod noise.
  if (import.meta.env.DEV) {
    console.warn(`[notifications] unmapped notification kind "${kind}" — add it to KIND_CONFIG`)
  }
  return FALLBACK_KIND
}
