import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@shared/i18n'
import SCopyField from '../SCopyField.vue'

const LINK = 'https://smap.example/?invite=1#token=abc123'

function stubClipboard(writeText: unknown): void {
  Object.defineProperty(navigator, 'clipboard', {
    value: writeText === undefined ? undefined : { writeText },
    configurable: true,
    writable: true,
  })
}

function mountField() {
  return mount(SCopyField, {
    props: { label: 'Accept link', name: 'acceptUrl', value: LINK },
    global: { plugins: [i18n] },
  })
}

describe('SCopyField', () => {
  afterEach(() => stubClipboard(undefined))

  // The value must live in a real control, not in text: when the Clipboard API
  // is unavailable the browser's own select-and-copy is the only route left.
  it('renders the value in a readonly input the user can select', () => {
    const w = mountField()
    const input = w.find('input')
    expect(input.element.value).toBe(LINK)
    expect(input.attributes('readonly')).toBeDefined()
  })

  it('writes the value to the clipboard and shows the copied state', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    stubClipboard(writeText)

    const w = mountField()
    await w.find('button').trigger('click')
    // `common.copy` is a prefix of `common.copied`, so the label is compared
    // exactly — `toContain` would pass in both states.
    await vi.waitFor(() => {
      if (w.find('button').text() !== 'common.copied') throw new Error('not copied yet')
    })
    expect(writeText).toHaveBeenCalledWith(LINK)
  })

  it('stays out of the copied state when the clipboard refuses', async () => {
    stubClipboard(vi.fn().mockRejectedValue(new Error('NotAllowedError')))

    const w = mountField()
    await w.find('button').trigger('click')
    await w.vm.$nextTick()
    await w.vm.$nextTick()
    expect(w.find('button').text()).toBe('common.copy')
  })
})
