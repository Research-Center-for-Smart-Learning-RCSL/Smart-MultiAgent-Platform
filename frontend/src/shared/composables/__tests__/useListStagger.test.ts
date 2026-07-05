import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref, nextTick } from 'vue'
import { useListStagger } from '../useListStagger'

describe('useListStagger', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('arms immediately when not loading, then clears after the window', () => {
    const cls = useListStagger(ref(false))
    expect(cls.value).toBe('list-stagger')

    vi.advanceTimersByTime(700)
    expect(cls.value).toBeNull()
  })

  it('arms on the first loading -> false transition only', async () => {
    const loading = ref(true)
    const cls = useListStagger(loading)
    expect(cls.value).toBeNull()

    loading.value = false
    await nextTick()
    expect(cls.value).toBe('list-stagger')

    vi.advanceTimersByTime(700)
    expect(cls.value).toBeNull()

    // A refetch cycle must not re-arm the entrance.
    loading.value = true
    await nextTick()
    loading.value = false
    await nextTick()
    expect(cls.value).toBeNull()
  })
})
