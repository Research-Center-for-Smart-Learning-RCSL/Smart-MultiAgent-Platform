// Optimistic send / delete behaviour of useChatroomMessages (§7.2).
//
// The api module, toast, confirm dialog and i18n are mocked so the test drives
// the composable's cache reconciliation in isolation; a real QueryClient backs
// the message cache so optimistic setQueryData / rollback are exercised for real.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { defineComponent, nextTick, ref, type Ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { useSessionStore } from '@shared/stores/session'
import type { Message } from '../types'

const api = vi.hoisted(() => ({
  listMessages: vi.fn(),
  sendMessage: vi.fn(),
  deleteMessage: vi.fn(),
  getMessage: vi.fn(),
  getAttachment: vi.fn(),
  compactChatroom: vi.fn(),
}))
vi.mock('../api', () => api)

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
const dialog = vi.hoisted(() => ({ confirm: vi.fn(async () => true) }))
vi.mock('@shared/composables', () => ({
  useToast: () => toast,
  useConfirmDialog: () => ({ confirm: dialog.confirm }),
}))
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, useI18n: () => ({ t: (k: string) => k }) }
})

import { useChatroomMessages } from '../composables/useChatroomMessages'

const ROOM = 'cr_1'
type Composable = ReturnType<typeof useChatroomMessages>
let composable: Composable

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function msg(over: Partial<Message> = {}): Message {
  return {
    id: 'm1',
    chatroom_id: ROOM,
    sender_type: 'user',
    sender_id: 'u1',
    content_md: 'hi',
    metadata: {},
    version: 1,
    created_at: '2026-01-01T00:00:00.000Z',
    edited_at: null,
    deleted_at: null,
    ...over,
  }
}

function mountHost() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const wrapper = mount(
    defineComponent({
      setup() {
        const listRef: Ref<HTMLElement | null> = ref(null)
        composable = useChatroomMessages(ROOM, listRef)
        return () => null
      },
    }),
    { global: { plugins: [pinia, [VueQueryPlugin, { queryClient: qc }]] } },
  )
  const session = useSessionStore()
  session.me = {
    id: 'u1',
    email: 'u@smap.test',
    email_verified: true,
    is_admin: false,
    status: 'active',
  }
  return { wrapper }
}

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  toast.error.mockClear()
  dialog.confirm.mockClear()
  dialog.confirm.mockResolvedValue(true)
})

describe('useChatroomMessages optimistic send', () => {
  it('shows a sending bubble immediately and reconciles on success', async () => {
    api.listMessages.mockResolvedValue([])
    const send = deferred<Message>()
    api.sendMessage.mockReturnValue(send.promise)

    mountHost()
    await flushPromises()

    composable.draft.value = 'hello'
    const pending = composable.onSend([])
    await nextTick()

    // Optimistic: bubble present in "sending" state, composer already cleared.
    const optimistic = composable.messages.value
    expect(optimistic).toHaveLength(1)
    expect(optimistic[0]._status).toBe('sending')
    expect(optimistic[0].content_md).toBe('hello')
    expect(composable.draft.value).toBe('')

    send.resolve(msg({ id: 'm_real', content_md: 'hello' }))
    await pending
    await flushPromises()

    const after = composable.messages.value
    expect(after.some((m) => m.id === 'm_real')).toBe(true)
    expect(after.some((m) => m._status === 'sending')).toBe(false)
  })

  it('rolls back the optimistic message and toasts on failure', async () => {
    api.listMessages.mockResolvedValue([])
    api.sendMessage.mockRejectedValue(new Error('network'))

    mountHost()
    await flushPromises()

    composable.draft.value = 'boom'
    await composable.onSend([])
    await flushPromises()

    expect(composable.messages.value).toHaveLength(0)
    expect(toast.error).toHaveBeenCalledTimes(1)
  })
})

describe('useChatroomMessages optimistic delete', () => {
  it('removes the message immediately and restores it if the server rejects', async () => {
    const target = msg({ id: 'm_del' })
    api.listMessages.mockResolvedValue([target])
    const del = deferred<void>()
    api.deleteMessage.mockReturnValue(del.promise)

    mountHost()
    await flushPromises()
    expect(composable.messages.value.some((m) => m.id === 'm_del')).toBe(true)

    const pending = composable.confirmDelete(target)
    await flushPromises() // confirm resolves true -> optimistic removal applied

    expect(composable.messages.value.some((m) => m.id === 'm_del')).toBe(false)

    del.reject(new Error('boom'))
    await pending
    await flushPromises()

    expect(composable.messages.value.some((m) => m.id === 'm_del')).toBe(true)
    expect(toast.error).toHaveBeenCalledTimes(1)
  })
})
