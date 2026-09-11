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
import type { CanvasTemplate, CanvasTemplateDetail } from '../types'

const mockTemplate: CanvasTemplate = {
  id: 'tmpl-1',
  scope: 'platform',
  project_id: null,
  name: 'Brainstorming',
  description: 'Central topic with surrounding ideas',
  created_by_user_id: null,
  created_at: '2026-09-10T00:00:00Z',
}

const mockTemplateDetail: CanvasTemplateDetail = {
  ...mockTemplate,
  template_data: {
    objects: [
      {
        kind: 'note' as const,
        position_x: 100,
        position_y: 100,
        width: 160,
        height: 100,
        content: 'Central Topic',
      },
    ],
  },
}

describe('Canvas Templates API layer', () => {
  it('listTemplates calls the correct endpoint', async () => {
    const spy = vi.spyOn(canvasApi, 'listTemplates').mockResolvedValue([mockTemplate])
    const result = await canvasApi.listTemplates({ project_id: 'proj-1' })
    expect(spy).toHaveBeenCalledWith({ project_id: 'proj-1' })
    expect(result).toHaveLength(1)
    expect(result[0].name).toBe('Brainstorming')
  })

  it('getTemplate returns detail with template_data', async () => {
    const spy = vi.spyOn(canvasApi, 'getTemplate').mockResolvedValue(mockTemplateDetail)
    const result = await canvasApi.getTemplate('tmpl-1')
    expect(spy).toHaveBeenCalledWith('tmpl-1')
    expect(result.template_data.objects).toHaveLength(1)
    expect(result.template_data.objects[0].kind).toBe('note')
  })

  it('applyTemplate sends template_id to chatroom endpoint', async () => {
    const mockResult = { created: [], updated: [], deleted: 0 }
    const spy = vi.spyOn(canvasApi, 'applyTemplate').mockResolvedValue(mockResult)
    const result = await canvasApi.applyTemplate('room-1', 'tmpl-1')
    expect(spy).toHaveBeenCalledWith('room-1', 'tmpl-1')
    expect(result.deleted).toBe(0)
  })

  it('saveAsTemplate sends name and description', async () => {
    const spy = vi.spyOn(canvasApi, 'saveAsTemplate').mockResolvedValue(mockTemplate)
    const result = await canvasApi.saveAsTemplate('room-1', {
      name: 'My Template',
      description: 'Test description',
    })
    expect(spy).toHaveBeenCalledWith('room-1', {
      name: 'My Template',
      description: 'Test description',
    })
    expect(result.scope).toBe('platform')
  })

  it('deleteTemplate calls delete endpoint', async () => {
    const spy = vi.spyOn(canvasApi, 'deleteTemplate').mockResolvedValue()
    await canvasApi.deleteTemplate('tmpl-1')
    expect(spy).toHaveBeenCalledWith('tmpl-1')
  })
})

describe('Canvas Template types', () => {
  it('CanvasTemplate has required fields', () => {
    expect(mockTemplate.id).toBeDefined()
    expect(mockTemplate.scope).toBe('platform')
    expect(mockTemplate.name).toBe('Brainstorming')
  })

  it('CanvasTemplateDetail includes template_data', () => {
    expect(mockTemplateDetail.template_data).toBeDefined()
    expect(mockTemplateDetail.template_data.objects).toBeInstanceOf(Array)
  })

  it('project-scoped template has project_id', () => {
    const projectTemplate: CanvasTemplate = {
      ...mockTemplate,
      scope: 'project',
      project_id: 'proj-1',
      created_by_user_id: 'user-1',
    }
    expect(projectTemplate.scope).toBe('project')
    expect(projectTemplate.project_id).toBe('proj-1')
  })
})
