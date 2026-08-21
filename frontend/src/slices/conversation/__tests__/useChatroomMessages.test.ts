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
import { ApiError } from '@shared/api-client'
import { deferred } from '../../../../tests/utils'
import type { Message } from '../types'

function apiError(status: number): ApiError {
  return new ApiError(
    {} as never,
    { url: '', ok: false, status, statusText: '', body: {} },
    'request failed',
  )
}

const api = vi.hoisted(() => ({
  listMessages: vi.fn(),
  sendMessage: vi.fn(),
  deleteMessage: vi.fn(),
  editMessage: vi.fn(),
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
import { useChatroomMessageEditing } from '../composables/useChatroomMessageEditing'
import { convKeys } from '../queries'

const ROOM = 'cr_1'
type Composable = ReturnType<typeof useChatroomMessages>
let composable: Composable

// T-3 (F-49): the composable used to be handed the feed element so its send
// path could scroll it, which made it a second writer of state useChatroomScroll
// owns. It now reports the send and the view wires this to scrollToBottom.
const onSentSpy = vi.fn()

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

const HostComponent = defineComponent({
  props: { run: { type: Function, required: true } },
  setup(props) {
    ;(props.run as () => void)()
    return () => null
  },
})

function mountQueryHost(run: () => void, gcTime = 0) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime } },
  })
  mount(HostComponent, {
    props: { run },
    global: { plugins: [pinia, [VueQueryPlugin, { queryClient: qc }]] },
  })
  return qc
}

function mountHost() {
  const qc = mountQueryHost(() => {
    composable = useChatroomMessages(ROOM, onSentSpy)
  })
  const session = useSessionStore()
  session.me = {
    id: 'u1',
    email: 'u@smap.test',
    email_verified: true,
    is_admin: false,
    status: 'active',
  }
  return { qc }
}

let editing: ReturnType<typeof useChatroomMessageEditing>

function mountEditing() {
  // No useQuery observer lives in this host, so keep gcTime high — gcTime:0
  // would collect the seeded cache entry before saveEdit reads it.
  const qc = mountQueryHost(() => {
    editing = useChatroomMessageEditing(ROOM)
  }, Infinity)
  return { qc }
}

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  onSentSpy.mockClear()
  toast.error.mockClear()
  toast.success.mockClear()
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

  // T-3 (F-49). The composable no longer receives the feed element at all, so
  // "it does not touch the DOM" is enforced by the signature rather than
  // asserted; what is left to pin is that the view is told, and told once.
  it('reports the send exactly once, after the optimistic bubble is rendered', async () => {
    api.listMessages.mockResolvedValue([])
    const send = deferred<Message>()
    api.sendMessage.mockReturnValue(send.promise)

    mountHost()
    await flushPromises()
    expect(onSentSpy).not.toHaveBeenCalled()

    composable.draft.value = 'hello'
    const pending = composable.onSend([])
    await nextTick()

    // Fired on the optimistic insert, not on the round-trip: the feed has to
    // follow the bubble the user can already see.
    expect(onSentSpy).toHaveBeenCalledTimes(1)

    send.resolve(msg({ id: 'm_real', content_md: 'hello' }))
    await pending
    await flushPromises()

    // Reconciling the optimistic row against its persisted twin is not a second
    // send, so it must not scroll the reader again.
    expect(onSentSpy).toHaveBeenCalledTimes(1)
  })

  it('does not report a send that was refused before anything was inserted', async () => {
    api.listMessages.mockResolvedValue([])
    mountHost()
    await flushPromises()

    composable.draft.value = '   '
    await composable.onSend([])

    expect(onSentSpy).not.toHaveBeenCalled()
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
    // The user's text is restored so a failed send never loses it.
    expect(composable.draft.value).toBe('boom')
    expect(toast.error).toHaveBeenCalledTimes(1)
  })

  it('hides the persisted echo while the optimistic send is still in flight', async () => {
    api.listMessages.mockResolvedValue([])
    const send = deferred<Message>()
    api.sendMessage.mockReturnValue(send.promise)

    const { qc } = mountHost()
    await flushPromises()

    composable.draft.value = 'dup'
    const pending = composable.onSend([])
    await nextTick()
    expect(composable.messages.value).toHaveLength(1)

    // Simulate the WS echo refetch landing the persisted twin (same sender +
    // content) BEFORE the POST resolves — it must NOT render a second time.
    qc.setQueryData(convKeys.messages(ROOM), [
      msg({ id: 'm_real', sender_id: 'u1', content_md: 'dup' }),
    ])
    await nextTick()
    expect(composable.messages.value).toHaveLength(1)

    send.resolve(msg({ id: 'm_real', sender_id: 'u1', content_md: 'dup' }))
    await pending
    await flushPromises()
    expect(composable.messages.value.filter((m) => m.content_md === 'dup')).toHaveLength(1)
  })
})

describe('useChatroomMessages /compact outcome reporting (F-9)', () => {
  it('restores the draft and toasts when the request fails', async () => {
    api.listMessages.mockResolvedValue([])
    api.compactChatroom.mockRejectedValue(new Error('backend down'))

    mountHost()
    await flushPromises()

    composable.draft.value = '/compact'
    // Resolves false rather than rejecting: `send` in ChatroomView binds this
    // as an event handler, so a rejection here becomes an unhandled rejection.
    await expect(composable.onSend([])).resolves.toBe(false)
    await flushPromises()

    expect(composable.draft.value).toBe('/compact')
    expect(toast.error).toHaveBeenCalledWith('conversation.settings.compactFailed')
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('clears the draft and toasts on success', async () => {
    api.listMessages.mockResolvedValue([])
    api.compactChatroom.mockResolvedValue(undefined)

    mountHost()
    await flushPromises()

    composable.draft.value = '/compact'
    await expect(composable.onSend([])).resolves.toBe(true)
    await flushPromises()

    expect(composable.draft.value).toBe('')
    expect(toast.success).toHaveBeenCalledWith('conversation.settings.compactRequested')
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('never posts a /compact draft as a message', async () => {
    api.listMessages.mockResolvedValue([])
    api.compactChatroom.mockRejectedValue(new Error('backend down'))

    mountHost()
    await flushPromises()

    composable.draft.value = '/compact'
    await composable.onSend([])
    await flushPromises()

    expect(api.sendMessage).not.toHaveBeenCalled()
    expect(composable.messages.value).toHaveLength(0)
  })
})

describe('useChatroomMessageEditing optimistic edit', () => {
  it('reopens the editor with the user\'s text when the save fails', async () => {
    const { qc } = mountEditing()
    qc.setQueryData(convKeys.messages(ROOM), [msg({ id: 'm_e', content_md: 'old', version: 2 })])
    const edit = deferred<Message>()
    api.editMessage.mockReturnValue(edit.promise)

    editing.startEdit(msg({ id: 'm_e', content_md: 'old', version: 2 }))
    editing.editDraft.value = 'new text'
    const pending = editing.saveEdit()
    await nextTick()
    // Optimistic: editor closed, cache shows the new content.
    expect(editing.editingId.value).toBeNull()
    expect((qc.getQueryData(convKeys.messages(ROOM)) as Message[])[0]!.content_md).toBe('new text')

    edit.reject(new Error('conflict'))
    await pending
    await flushPromises()

    // Rollback: content reverted AND the editor is reopened holding the draft.
    expect((qc.getQueryData(convKeys.messages(ROOM)) as Message[])[0]!.content_md).toBe('old')
    expect(editing.editingId.value).toBe('m_e')
    expect(editing.editDraft.value).toBe('new text')
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

  it('discloses that a compaction summary keeps its own wording (R13.26)', async () => {
    // Deletion does not rewrite a summary the message was folded into, so the
    // limit has to reach the user while they can still decide - the confirm
    // dialog is the only surface that does.
    const target = msg({ id: 'm_del' })
    api.listMessages.mockResolvedValue([target])
    api.deleteMessage.mockResolvedValue(undefined)
    dialog.confirm.mockResolvedValue(false)

    mountHost()
    await flushPromises()
    await composable.confirmDelete(target)

    const { message } = dialog.confirm.mock.calls[0]![0] as { message: string }
    expect(message).toContain('conversation.chatroom.deleteSummaryNotice')
    expect(message).toContain('conversation.chatroom.deleteConfirm')
  })
})

describe('useChatroomMessages moderator affordances (V-4)', () => {
  // The backend already grants edit/delete to project and org owners
  // (`messages.py` delete gate, `message_service.edit`); the affordances stayed
  // hidden because the DTO carried no signal. `is_moderator` is that signal.
  function mountModerator(isModerator: boolean) {
    api.listMessages.mockResolvedValue([])
    mountQueryHost(() => {
      const listRef: Ref<HTMLElement | null> = ref(null)
      composable = useChatroomMessages(
        ROOM,
        onSentSpy,
        () => [],
        () => isModerator,
      )
    })
    const session = useSessionStore()
    session.me = {
      id: 'u1',
      email: 'u@smap.test',
      email_verified: true,
      is_admin: false,
      status: 'active',
    }
  }

  // Someone else's message, well past the five-minute author window.
  function aged(over: Partial<Message> = {}): Message {
    return msg({
      id: 'm_other',
      sender_id: 'u2',
      created_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
      ...over,
    })
  }

  it('lets a non-admin project owner edit another user\'s aged message', async () => {
    mountModerator(true)
    await flushPromises()
    expect(composable.canEdit(aged())).toBe(true)
  })

  it('lets a non-admin project owner delete another user\'s message', async () => {
    mountModerator(true)
    await flushPromises()
    expect(composable.canDelete(aged())).toBe(true)
  })

  it('grants a plain member neither affordance', async () => {
    mountModerator(false)
    await flushPromises()
    expect(composable.canEdit(aged())).toBe(false)
    expect(composable.canDelete(aged())).toBe(false)
  })

  it('never offers either affordance on an optimistic message', async () => {
    // An unsent bubble has no server id to PATCH or DELETE, so the `_status`
    // guard has to stay ahead of the moderator grant.
    mountModerator(true)
    await flushPromises()
    const optimistic = { ...aged({ id: 'pending-1' }), _status: 'sending' as const }
    expect(composable.canEdit(optimistic)).toBe(false)
    expect(composable.canDelete(optimistic)).toBe(false)
  })
})

describe('useChatroomMessages loading/error state (F-17)', () => {
  it('exposes isPending until the first fetch settles', async () => {
    const fetch = deferred<Message[]>()
    api.listMessages.mockReturnValue(fetch.promise)

    mountHost()
    await nextTick()
    expect(composable.isPending.value).toBe(true)

    fetch.resolve([])
    await flushPromises()
    expect(composable.isPending.value).toBe(false)
  })

  it('exposes isError after the query exhausts retries', async () => {
    api.listMessages.mockRejectedValue(new Error('boom'))

    mountHost()
    await flushPromises()

    expect(composable.isError.value).toBe(true)
    expect(composable.isPending.value).toBe(false)
  })

  it('recovers from isError once refetchMessages succeeds', async () => {
    api.listMessages.mockRejectedValueOnce(new Error('boom'))
    api.listMessages.mockResolvedValueOnce([])

    mountHost()
    await flushPromises()
    expect(composable.isError.value).toBe(true)

    composable.refetchMessages()
    await flushPromises()
    expect(composable.isError.value).toBe(false)
  })
})

describe('useChatroomMessages before-cursor 422 fallback (V-2)', () => {
  type Page = { before?: string; since?: string; limit?: number }

  // Seeds a recent-window message plus one paged-back "dead" anchor, matching
  // the real trigger shape: the poisoned row is the oldest known message, not
  // the sole message in the room.
  async function mountWithDeadAnchor(): Promise<{ recentMsg: Message; deadAnchor: Message }> {
    const recentMsg = msg({ id: 'm_recent', created_at: '2026-01-02T00:00:00.000Z' })
    const deadAnchor = msg({ id: 'm_dead', created_at: '2026-01-01T00:00:00.000Z' })
    api.listMessages.mockResolvedValueOnce([recentMsg])
    mountHost()
    await flushPromises()

    composable.hasOlderMessages.value = true
    api.listMessages.mockResolvedValueOnce([deadAnchor])
    await composable.loadEarlier()
    await flushPromises()
    expect(composable.messages.value.map((m) => m.id)).toEqual(['m_dead', 'm_recent'])

    composable.hasOlderMessages.value = true
    return { recentMsg, deadAnchor }
  }

  it('recovers from a 422 on the before cursor by dropping the dead anchor and refetching', async () => {
    const recovered = msg({ id: 'm_older2', created_at: '2025-12-31T00:00:00.000Z' })
    await mountWithDeadAnchor()

    api.listMessages.mockImplementation(async (_room: string, params: Page = {}) => {
      if (params.before === 'm_dead') throw apiError(422)
      if (params.before === 'm_recent') return [recovered]
      return [] // the invalidate-triggered recent-window refetch (no before/since)
    })

    await composable.loadEarlier()
    await flushPromises()

    expect(composable.messages.value.some((m) => m.id === 'm_dead')).toBe(false)
    expect(composable.messages.value.some((m) => m.id === 'm_older2')).toBe(true)
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('does not repeat the same failing before request on a second click', async () => {
    const recovered = msg({ id: 'm_older2', created_at: '2025-12-31T00:00:00.000Z' })
    const further = msg({ id: 'm_older3', created_at: '2025-12-30T00:00:00.000Z' })
    await mountWithDeadAnchor()

    api.listMessages.mockImplementation(async (_room: string, params: Page = {}) => {
      if (params.before === 'm_dead') throw apiError(422)
      if (params.before === 'm_recent') return [recovered]
      if (params.before === 'm_older2') return [further]
      return []
    })

    await composable.loadEarlier()
    await flushPromises()
    composable.hasOlderMessages.value = true

    await composable.loadEarlier()
    await flushPromises()

    const beforeParams = api.listMessages.mock.calls
      .map((c) => (c[1] as Page | undefined)?.before)
      .filter((id): id is string => Boolean(id))
    expect(beforeParams.filter((id) => id === 'm_dead')).toHaveLength(1)
    expect(beforeParams).toContain('m_older2')
    expect(composable.messages.value.some((m) => m.id === 'm_older3')).toBe(true)
  })

  it('still toasts on a genuine transport failure', async () => {
    await mountWithDeadAnchor()
    api.listMessages.mockRejectedValueOnce(new Error('network down'))

    await composable.loadEarlier()
    await flushPromises()

    expect(toast.error).toHaveBeenCalledWith('conversation.chatroom.loadEarlierFailed')
    expect(composable.hasOlderMessages.value).toBe(true)
    expect(composable.messages.value.some((m) => m.id === 'm_dead')).toBe(true)
  })
})
