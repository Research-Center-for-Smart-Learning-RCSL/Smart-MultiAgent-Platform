import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import KeyListView from '../views/KeyListView.vue'

describe('KeyListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(KeyListView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows key table with rows when keys are returned', async () => {
    server.use(
      http.get('/api/keys', () =>
        HttpResponse.json([
          {
            id: 'key_1',
            provider: 'openai',
            name: 'Test Key',
            masked_preview: 'sk-****abcd',
            test_status: 'pass',
            test_error: null,
            last_test_at: '2026-04-01T00:00:00Z',
          },
        ]),
      ),
    )
    const wrapper = await renderView(KeyListView)
    // Wait for the onMounted reload to complete
    await new Promise((r) => setTimeout(r, 50))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="key-row-key_1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="retest"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="delete"]').exists()).toBe(true)
  })
})
