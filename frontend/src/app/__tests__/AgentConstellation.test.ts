import { describe, it, expect } from 'vitest'
import { renderView } from '../../../tests/utils'
import AgentConstellation from '../components/AgentConstellation.vue'

describe('AgentConstellation', () => {
  it('renders a decorative svg hidden from assistive tech', async () => {
    const wrapper = await renderView(AgentConstellation)
    const svg = wrapper.find('svg.constellation')
    expect(svg.exists()).toBe(true)
    expect(svg.attributes('aria-hidden')).toBe('true')
    expect(svg.attributes('role')).toBe('presentation')
  })

  it('draws one node and one flow edge per satellite', async () => {
    const wrapper = await renderView(AgentConstellation)
    expect(wrapper.findAll('.node')).toHaveLength(6)
    expect(wrapper.findAll('.edge-flow')).toHaveLength(6)
  })

  it('flags primary nodes and alternating inward flow', async () => {
    const wrapper = await renderView(AgentConstellation)
    expect(wrapper.findAll('.node--primary')).toHaveLength(2)
    expect(wrapper.findAll('.edge-flow--inward')).toHaveLength(3)
  })
})
