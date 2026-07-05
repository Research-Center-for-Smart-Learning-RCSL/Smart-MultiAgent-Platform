import { describe, it, expect } from 'vitest'
import { renderView } from '../../../tests/utils'
import AgentConstellation from '../components/AgentConstellation.vue'

describe('AgentConstellation', () => {
  it('renders a decorative figure hidden from assistive tech', async () => {
    const wrapper = await renderView(AgentConstellation)
    // The wrapper carries the decorative semantics for the whole figure
    // (svg + particle canvas layers beneath it).
    const root = wrapper.find('.constellation-wrap')
    expect(root.exists()).toBe(true)
    expect(root.attributes('aria-hidden')).toBe('true')
    expect(root.attributes('role')).toBe('presentation')
    expect(wrapper.find('svg.constellation').exists()).toBe(true)
    expect(wrapper.find('canvas.particle-field').exists()).toBe(true)
  })

  it('draws a shell, a fill core, and one flow edge per satellite', async () => {
    const wrapper = await renderView(AgentConstellation)
    expect(wrapper.findAll('.node-shell')).toHaveLength(6)
    expect(wrapper.findAll('.node-fill')).toHaveLength(6)
    expect(wrapper.findAll('.edge-flow')).toHaveLength(6)
  })

  it('gives the hub its own shell and fill core for the cycle', async () => {
    const wrapper = await renderView(AgentConstellation)
    expect(wrapper.findAll('.hub-shell')).toHaveLength(1)
    expect(wrapper.findAll('.hub-fill')).toHaveLength(1)
  })

  it('staggers fill phases across satellites and alternates inward flow', async () => {
    const wrapper = await renderView(AgentConstellation)
    const delays = wrapper.findAll('.node-fill').map((c) => c.attributes('style') ?? '')
    expect(new Set(delays).size).toBe(6)
    expect(wrapper.findAll('.edge-flow--inward')).toHaveLength(3)
  })
})
