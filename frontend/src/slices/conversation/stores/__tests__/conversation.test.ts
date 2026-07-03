// clearTyping regression (B4): the store field is `typingUsers`, not `typing`.
// A reference to the wrong name throws a ReferenceError at runtime, which
// resyncPresence's best-effort catch swallows — so stale typing indicators
// never clear on reconnect.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useConversationStore } from '../conversation'

const ROOM = 'cr_1'

describe('conversation store — clearTyping', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('empties the room typing set without throwing', () => {
    const store = useConversationStore()
    store.addTyping(ROOM, 'u1')
    store.addTyping(ROOM, 'u2')
    expect(store.typingUsers[ROOM]?.size).toBe(2)

    expect(() => store.clearTyping(ROOM)).not.toThrow()

    expect(store.typingUsers[ROOM]?.size).toBe(0)
  })

  it('is a no-op for a room with no typing state', () => {
    const store = useConversationStore()
    expect(() => store.clearTyping(ROOM)).not.toThrow()
    expect(store.typingUsers[ROOM]).toBeUndefined()
  })
})
