import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { i18n } from '@shared/i18n'
import { SSegmentedControl, SStatTile, STooltip } from '@shared/ui'

const { usage, toastError } = vi.hoisted(() => ({
  usage: vi.fn(),
  toastError: vi.fn(),
}))

vi.mock('../api/project-keys', () => ({
  projectKeysApi: { usage },
}))

vi.mock('@shared/composables', () => ({
  useToast: () => ({ error: toastError }),
}))

import UsageDashboard from '../components/UsageDashboard.vue'

describe('UsageDashboard shared primitives', () => {
  beforeEach(() => {
    usage.mockResolvedValue({
      window: '1h',
      input_tokens: 1_200,
      output_tokens: 2_400,
      requests: 12,
      errors: 1,
    })
    toastError.mockReset()
  })

  it('renders its window selector and counters through shared primitives', async () => {
    const wrapper = mount(UsageDashboard, {
      props: { projectId: 'project-1', keyId: 'key-1' },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    expect(wrapper.findComponent(SSegmentedControl).exists()).toBe(true)
    expect(wrapper.findAllComponents(SStatTile)).toHaveLength(4)
    expect(wrapper.findAllComponents(STooltip)).toHaveLength(4)
  })
})
