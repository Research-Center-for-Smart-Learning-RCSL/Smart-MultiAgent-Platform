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

  it('renders page header and add button', async () => {
    const wrapper = await renderView(SearchKeyView, {
      routes,
      initialRoute: '/projects/proj_1/search-keys',
    })
    const text = wrapper.text()
    expect(text).toContain('keys.search.title')
    expect(text).toContain('keys.search.add')
  })
})
