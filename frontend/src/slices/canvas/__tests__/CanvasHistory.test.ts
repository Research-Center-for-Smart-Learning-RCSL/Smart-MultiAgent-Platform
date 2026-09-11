import { describe, it, expect, vi } from 'vitest'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
  createI18n: () => ({
    global: { t: (key: string) => key },
    install: vi.fn(),
  }),
}))

vi.mock('@shared/query-client', () => ({
  queryClient: {
    invalidateQueries: vi.fn(),
    defaultQueryOptions: vi.fn(() => ({})),
    getDefaultOptions: vi.fn(() => ({ queries: {} })),
  },
}))

vi.mock('@shared/transport/axios', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import * as canvasApi from '../api'

describe('CanvasHistory API layer', () => {
  it('getSnapshot calls the correct endpoint', async () => {
    const spy = vi.spyOn(canvasApi, 'getSnapshot').mockResolvedValue({
      id: 'snap-1',
      canvas_id: 'c-1',
      agent_digest: '2 notes',
      created_by_user_id: null,
      label: 'test',
      created_at: '2026-09-10T12:00:00Z',
      snapshot_data: { objects: [] },
    })
    const result = await canvasApi.getSnapshot('room-1', 'snap-1')
    expect(spy).toHaveBeenCalledWith('room-1', 'snap-1')
    expect(result.snapshot_data).toBeDefined()
  })

  it('restoreSnapshot calls the correct endpoint', async () => {
    const spy = vi.spyOn(canvasApi, 'restoreSnapshot').mockResolvedValue({
      id: 'auto-save-1',
      canvas_id: 'c-1',
      agent_digest: '1 note',
      created_by_user_id: null,
      label: 'Auto-save before restore',
      created_at: '2026-09-10T12:00:00Z',
    })
    const result = await canvasApi.restoreSnapshot('room-1', 'snap-1')
    expect(spy).toHaveBeenCalledWith('room-1', 'snap-1')
    expect(result.label).toBe('Auto-save before restore')
  })

  it('createSnapshot accepts optional label', async () => {
    const spy = vi.spyOn(canvasApi, 'createSnapshot').mockResolvedValue({
      id: 'snap-2',
      canvas_id: 'c-1',
      agent_digest: '3 notes',
      created_by_user_id: 'u-1',
      label: 'My checkpoint',
      created_at: '2026-09-10T12:00:00Z',
    })
    await canvasApi.createSnapshot('room-1', { label: 'My checkpoint' })
    expect(spy).toHaveBeenCalledWith('room-1', { label: 'My checkpoint' })
  })
})
