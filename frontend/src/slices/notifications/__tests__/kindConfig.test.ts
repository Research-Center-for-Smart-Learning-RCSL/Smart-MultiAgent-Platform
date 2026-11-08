import { describe, it, expect, vi } from 'vitest'
import { kindConfig } from '../lib/kindConfig'
import type { Notification } from '../api'

function notif(kind: string, metadata: Record<string, unknown> = {}): Notification {
  return {
    id: 'n',
    kind,
    title: 't',
    body: null,
    metadata,
    read_at: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('kindConfig', () => {
  it('maps each production NotificationKind to a distinct config', () => {
    // These are the exact values emitted by the backend
    // (contexts/notification/domain/models.py).
    const kinds = [
      'key.usage_threshold',
      'key.test_failed',
      'invite.received',
      'admin.ban_reason',
      'approval.human_requested',
      'export.ready',
    ]
    for (const k of kinds) {
      expect(kindConfig(k).labelKey).not.toBe('notifications.kindGeneric')
    }
  })

  it('falls back to a generic config for unknown kinds', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const cfg = kindConfig('something.unexpected')
    expect(cfg.labelKey).toBe('notifications.kindGeneric')
    expect(cfg.action).toBeUndefined()
    warn.mockRestore()
  })

  it('wires key notifications to the keys.detail route from metadata.key_id', () => {
    const cfg = kindConfig('key.test_failed')
    expect(cfg.action?.(notif('key.test_failed', { key_id: 'key_99' }))).toEqual({
      labelKey: 'notifications.viewKey',
      to: { name: 'keys.detail', params: { id: 'key_99' } },
    })
  })

  it('returns no key action when metadata lacks key_id', () => {
    const cfg = kindConfig('key.usage_threshold')
    expect(cfg.action?.(notif('key.usage_threshold'))).toBeNull()
  })

  it('wires invite notifications to the tenancy.inbox route', () => {
    const cfg = kindConfig('invite.received')
    expect(cfg.action?.(notif('invite.received'))).toEqual({
      labelKey: 'notifications.viewInvites',
      to: { name: 'tenancy.inbox' },
    })
  })

  it('wires a research export notification to its job on the dashboard', () => {
    const cfg = kindConfig('export.ready')
    const n = notif('export.ready', { export: 'research', job_id: 'j1', workspace_id: 'w1' })
    expect(cfg.action?.(n)).toEqual({
      labelKey: 'notifications.viewExport',
      to: { name: 'dashboard', params: { workspaceId: 'w1' }, query: { researchExport: 'j1' } },
    })
  })

  it.each([
    { job_id: 'j1', workspace_id: 'w1' },
    { export: 'research', workspace_id: 'w1' },
    { export: 'research', job_id: 'j1' },
  ])('returns no export action without a research job and workspace (%o)', (metadata) => {
    expect(kindConfig('export.ready').action?.(notif('export.ready', metadata))).toBeNull()
  })
})
