import { describe, it, expect } from 'vitest'
import { http, delay, HttpResponse } from 'msw'
import { renderView } from '../../../../tests/utils'
import { server } from '../../../../tests/mocks/server'
import STable from '@shared/ui/STable.vue'
import STabs from '@shared/ui/STabs.vue'
import ProjectKeysView from '../views/ProjectKeysView.vue'

const routes = [
  { path: '/projects/:projectId/keys', name: 'keys.projectKeys', component: ProjectKeysView },
]

function render() {
  return renderView(ProjectKeysView, { routes, initialRoute: '/projects/proj_1/keys' })
}

/** Hold the my-keys list in flight for the whole test. */
function pendMyKeys(): void {
  server.use(http.get('/api/keys', async () => {
    await delay('infinite')
    return HttpResponse.json([])
  }))
}

describe('ProjectKeysView', () => {
  it('renders without errors', async () => {
    const wrapper = await render()
    expect(wrapper.exists()).toBe(true)
  })

  it('renders page header and tabs', async () => {
    const wrapper = await render()
    const text = wrapper.text()
    expect(text).toContain('keys.project.title')
    expect(text).toContain('keys.project.carried')
    expect(text).toContain('keys.project.carry')
  })

  // F-33: `carriable` derives from useMyKeys().keys, which is [] until that
  // query resolves, and the view destructured only `keys` - so the flag the
  // available table needed was never in scope. The `loading` it did have
  // belongs to useProjectKeys, the carried query, which is unrelated.
  //
  // The badge is the half that is always visible: STabs renders only the
  // active panel, so on first load the available table is not even mounted
  // while the badge already asserts "0 keys available".
  it('does not claim zero available keys while the list is in flight', async () => {
    pendMyKeys()
    const wrapper = await render()

    const available = wrapper.getComponent(STabs).props('tabs')
      .find((tab: { key: string }) => tab.key === 'available')
    expect(available).toBeDefined()
    expect(available.badge).not.toBe('0')
  })

  it('shows skeleton rows in the available table while the list is in flight', async () => {
    pendMyKeys()
    const wrapper = await render()

    await wrapper.getComponent(STabs).vm.$emit('update:modelValue', 'available')
    await wrapper.vm.$nextTick()

    const tables = wrapper.findAllComponents(STable)
    expect(tables.length).toBeGreaterThan(0)
    expect(tables[tables.length - 1]?.props('loading')).toBe(true)
  })
})
