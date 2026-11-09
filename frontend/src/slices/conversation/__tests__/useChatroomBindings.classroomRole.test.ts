// onSetClassroomRole ([R34.01]): the 409 for a second teacher gets its own message.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent } from 'vue'
import { createI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'

const setRoleMock = vi.hoisted(() => vi.fn())
const listBoundMock = vi.hoisted(() => vi.fn())

vi.mock('@shared/composables', () => ({ useToast: () => ({ error: vi.fn() }) }))
vi.mock('@slices/activities', () => ({
  activityKeys: { types: (id: string) => ['types', id] },
  listActivityTypes: vi.fn(async () => []),
}))
vi.mock('../api', () => ({
  addChatroomAgent: vi.fn(),
  getWorkspace: vi.fn(async () => ({ project_id: 'p1' })),
  listChatroomAgents: listBoundMock,
  listProjectAgentNames: vi.fn(async () => [{ id: 'ag_1', name: 'TA' }]),
  listProjectAgents: vi.fn(async () => []),
  removeChatroomAgent: vi.fn(),
  setChatroomAgentActivityControl: vi.fn(),
  setChatroomAgentCanvasAccess: vi.fn(),
  setChatroomAgentCanvasWriteAccess: vi.fn(),
  setChatroomAgentClassroomRole: setRoleMock,
  setChatroomAgentDraftAccess: vi.fn(),
  setChatroomAgentRole: vi.fn(),
  setChatroomAgentAppearance: vi.fn(),
}))

import { useChatroomBindings } from '../composables/useChatroomBindings'
import type { Chatroom } from '../types'

function mountBindings() {
  let api!: ReturnType<typeof useChatroomBindings>
  const room = { id: 'cr_1', workspace_id: 'w1', is_moderator: false } as Chatroom
  const Host = defineComponent({
    setup() {
      api = useChatroomBindings('cr_1', () => room, {
        projectId: () => 'p1',
        needsActivityTypes: () => false,
      })
      return () => null
    },
  })
  mount(Host, {
    global: {
      plugins: [
        createI18n({ legacy: false, locale: 'en', messages: {} }),
        [VueQueryPlugin, { queryClient: new QueryClient() }],
      ],
    },
  })
  return api
}

describe('useChatroomBindings.onSetClassroomRole', () => {
  beforeEach(() => {
    setRoleMock.mockReset()
    listBoundMock.mockResolvedValue([{ agent_id: 'ag_1', role: 'normal', classroom_role: 'none' }])
  })

  it('writes the role and reloads the bindings', async () => {
    const api = mountBindings()
    await api.loadBindings()
    expect(api.boundAgents.value[0]?.classroom_role).toBe('none')

    setRoleMock.mockResolvedValue(undefined)
    listBoundMock.mockResolvedValue([{ agent_id: 'ag_1', role: 'normal', classroom_role: 'teacher' }])
    await api.onSetClassroomRole('ag_1', 'teacher')
    await flushPromises()

    expect(setRoleMock).toHaveBeenCalledWith('cr_1', 'ag_1', 'teacher')
    expect(api.boundAgents.value[0]?.classroom_role).toBe('teacher')
    expect(api.bindingError.value).toBeNull()
  })

  it('names the taken teacher seat on a 409', async () => {
    const api = mountBindings()
    await api.loadBindings()
    setRoleMock.mockRejectedValue(new ApiError({ type: 't', title: 'Conflict', status: 409 }))
    await api.onSetClassroomRole('ag_1', 'teacher')
    expect(api.bindingError.value).toBe('conversation.classroomRole.teacherTaken')
  })

  it('falls back to the generic message otherwise', async () => {
    const api = mountBindings()
    await api.loadBindings()
    setRoleMock.mockRejectedValue(new ApiError({ type: 't', title: 'Bad', status: 422 }))
    await api.onSetClassroomRole('ag_1', 'analyst')
    expect(api.bindingError.value).toBe('conversation.classroomRole.failed')
  })

  it('skips a write that would not change anything', async () => {
    const api = mountBindings()
    await api.loadBindings()
    await api.onSetClassroomRole('ag_1', 'none')
    expect(setRoleMock).not.toHaveBeenCalled()
  })
})
