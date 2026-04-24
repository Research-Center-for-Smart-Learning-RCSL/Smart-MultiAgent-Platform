import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import KeyDetailView from '../views/KeyDetailView.vue'

const routes = [
  { path: '/keys/:id', name: 'keys.detail', component: KeyDetailView },
]

describe('KeyDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(KeyDetailView, {
      routes,
      initialRoute: '/keys/key_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows key details when key is found', async () => {
    server.use(
      http.get('/api/keys', () =>
        HttpResponse.json([
          {
            id: 'key_1',
            provider: 'openai',
            name: 'My Key',
            masked_preview: 'sk-****abcd',
            test_status: 'pass',
            test_error: null,
            last_test_at: '2026-04-01T00:00:00Z',
          },
        ]),
      ),
    )
    const wrapper = await renderView(KeyDetailView, {
      routes,
      initialRoute: '/keys/key_1',
    })
    await new Promise((r) => setTimeout(r, 50))
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('My Key')
    expect(wrapper.text()).toContain('sk-****abcd')
  })
})
