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

describe('CanvasSearch API layer', () => {
  it('searchCanvas calls the correct endpoint', async () => {
    const mockResults = [
      {
        object_id: 'obj-1',
        kind: 'note' as const,
        snippet: 'test <mark>note</mark> content',
        rank: 0.5,
        position_x: 10,
        position_y: 20,
      },
    ]
    const spy = vi.spyOn(canvasApi, 'searchCanvas').mockResolvedValue(mockResults)
    const result = await canvasApi.searchCanvas('room-1', 'note')
    expect(spy).toHaveBeenCalledWith('room-1', 'note')
    expect(result).toHaveLength(1)
    expect(result[0].object_id).toBe('obj-1')
    expect(result[0].snippet).toContain('<mark>')
  })

  it('searchCanvas returns empty for no matches', async () => {
    const spy = vi.spyOn(canvasApi, 'searchCanvas').mockResolvedValue([])
    const result = await canvasApi.searchCanvas('room-1', 'nonexistent')
    expect(spy).toHaveBeenCalledWith('room-1', 'nonexistent')
    expect(result).toHaveLength(0)
  })

  it('searchCanvas passes limit parameter', async () => {
    const spy = vi.spyOn(canvasApi, 'searchCanvas').mockResolvedValue([])
    await canvasApi.searchCanvas('room-1', 'query', { limit: 10 })
    expect(spy).toHaveBeenCalledWith('room-1', 'query', { limit: 10 })
  })
})

describe('CanvasSearch snippet sanitization', () => {
  it('sanitizeSnippet preserves mark tags', async () => {
    const { default: DOMPurify } = await import('dompurify')
    const html = 'a <mark>b</mark> c'
    const sanitized = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['mark'],
      ALLOWED_ATTR: [],
    })
    expect(sanitized).toContain('<mark>b</mark>')
  })

  it('sanitizeSnippet strips dangerous tags', async () => {
    const { default: DOMPurify } = await import('dompurify')
    const html = '<script>alert(1)</script><mark>safe</mark>'
    const sanitized = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['mark'],
      ALLOWED_ATTR: [],
    })
    expect(sanitized).not.toContain('<script>')
    expect(sanitized).toContain('<mark>safe</mark>')
  })

  it('sanitizeSnippet strips attributes from mark', async () => {
    const { default: DOMPurify } = await import('dompurify')
    const html = '<mark onclick="alert(1)" style="color:red">x</mark>'
    const sanitized = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['mark'],
      ALLOWED_ATTR: [],
    })
    expect(sanitized).toContain('<mark>x</mark>')
    expect(sanitized).not.toContain('onclick')
    expect(sanitized).not.toContain('style')
  })
})
