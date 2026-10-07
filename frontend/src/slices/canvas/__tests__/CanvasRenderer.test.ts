// docs/tasks/2026-10-07-canvas-awareness-names (AC-1, AC-2, AC-4): a remote cursor
// was labelled with whatever name its sender broadcast. The viewer now prefers the
// room roster's name for the cursor's user id, and relabels when the roster changes.

import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import * as Y from 'yjs'
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate } from 'y-protocols/awareness'
import CanvasRenderer from '../components/CanvasRenderer.vue'

const scenes: Array<{ collaborators?: Map<string, { username?: string }> }> = []
let mounted = 0

vi.mock('@excalidraw/excalidraw', () => ({
  Excalidraw: (props: { excalidrawAPI: (api: unknown) => void }) => {
    props.excalidrawAPI({ updateScene: (scene: (typeof scenes)[number]) => scenes.push(scene) })
    mounted += 1
    return null
  },
}))
vi.mock('@excalidraw/excalidraw/index.css', () => ({}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

function remoteAwareness(doc: Y.Doc, user: Record<string, string>): Awareness {
  const remote = new Awareness(new Y.Doc())
  remote.setLocalStateField('user', user)
  const local = new Awareness(doc)
  applyAwarenessUpdate(local, encodeAwarenessUpdate(remote, [remote.clientID]), 'remote')
  return local
}

function lastLabels(): string[] {
  const scene = [...scenes].reverse().find((s) => s.collaborators)
  return [...(scene?.collaborators?.values() ?? [])].map((c) => c.username ?? '')
}

async function render(names: Record<string, string>, user: Record<string, string>) {
  scenes.length = 0
  const before = mounted
  const doc = new Y.Doc()
  const awareness = remoteAwareness(doc, user)
  const wrapper = mount(CanvasRenderer, { props: { doc, awareness, names }, attachTo: document.body })
  await vi.waitFor(() => expect(mounted).toBeGreaterThan(before), { timeout: 5000 })
  await flushPromises()
  awareness.emit('change', [{ added: [], updated: [], removed: [] }, 'remote'])
  return { wrapper, awareness }
}

describe('CanvasRenderer collaborator labels', () => {
  it("labels a cursor with the roster's name for its user id, not the broadcast name", async () => {
    const { wrapper } = await render({ 'u-1': 'Alice' }, { userId: 'u-1', name: 'spoof', color: '#FF6B6B' })

    expect(lastLabels()).toEqual(['Alice'])
    wrapper.unmount()
  })

  it('relabels when the roster changes, without an awareness change', async () => {
    const { wrapper } = await render({ 'u-1': 'Alice' }, { userId: 'u-1', name: 'spoof', color: '#FF6B6B' })

    await wrapper.setProps({ names: { 'u-1': 'Alicia' } })

    expect(lastLabels()).toEqual(['Alicia'])
    wrapper.unmount()
  })

  it('falls back to the broadcast name, then to the eight-character id', async () => {
    const { wrapper } = await render({}, { userId: '3f2a9c1e-0000-4000-8000-000000000001', name: 'Bob' })
    expect(lastLabels()).toEqual(['Bob'])
    wrapper.unmount()

    const second = await render({}, { userId: '3f2a9c1e-0000-4000-8000-000000000001' })
    expect(lastLabels()).toEqual(['3f2a9c1e'])
    second.wrapper.unmount()
  })
})
