import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import SearchKeyView from '../views/SearchKeyView.vue'

const routes = [
  { path: '/projects/:projectId/search-keys', name: 'keys.searchKeys', component: SearchKeyView },
]

describe('SearchKeyView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(SearchKeyView, {
      routes,
      initialRoute: '/projects/proj_1/search-keys',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows provider select with all options and upload form', async () => {
    const wrapper = await renderView(SearchKeyView, {
      routes,
      initialRoute: '/projects/proj_1/search-keys',
    })
    const select = wrapper.find('[data-testid="search-provider"]')
    expect(select.exists()).toBe(true)
    const options = select.findAll('option')
    const values = options.map((o) => o.element.value)
    expect(values).toContain('brave')
    expect(values).toContain('serper')
    expect(values).toContain('tavily')
    expect(values).toContain('google_cse')
    expect(wrapper.find('[data-testid="search-upload"]').exists()).toBe(true)
  })
})
