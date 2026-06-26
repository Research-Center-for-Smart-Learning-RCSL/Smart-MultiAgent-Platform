import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import { useConfigModel } from '../useConfigModel'

// Mirrors the real editor path: node.config is read from the reactive flowNodes
// graph, so the modelValue handed to a config form is a Vue reactive proxy.
// structuredClone throws DataCloneError on such a proxy; the form must still work.
function reactiveConfig(obj: Record<string, unknown>): Record<string, unknown> {
  return ref([{ data: { config: obj } }]).value[0]!.data.config
}

describe('useConfigModel', () => {
  it('clones a reactive-proxy modelValue without throwing (regression: DataCloneError)', () => {
    const cfg = reactiveConfig({ description: 'hello', n: 3, branches: [{ port: 'p1' }] })
    const emit = vi.fn()
    expect(() => useConfigModel({ modelValue: cfg }, emit)).not.toThrow()
  })

  it('exposes the cloned values and emits the full config on update', () => {
    const cfg = reactiveConfig({ description: 'hello', n: 3 })
    const emit = vi.fn()
    const { local, update } = useConfigModel({ modelValue: cfg }, emit)
    expect(local.description).toBe('hello')
    expect(local.n).toBe(3)

    update('n', 5)
    expect(emit).toHaveBeenCalledWith('update:modelValue', { description: 'hello', n: 5 })
  })

  it('does not mutate the source modelValue when editing local', () => {
    const cfg = reactiveConfig({ description: 'hello' })
    const emit = vi.fn()
    const { update } = useConfigModel({ modelValue: cfg }, emit)
    update('description', 'changed')
    expect(cfg.description).toBe('hello')
  })
})
