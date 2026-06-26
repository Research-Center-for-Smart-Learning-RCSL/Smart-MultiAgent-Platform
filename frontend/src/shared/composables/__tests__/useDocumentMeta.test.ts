import { describe, it, expect, afterEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { useDocumentMeta } from '../useDocumentMeta'

function makeHarness(title: string, description: string) {
  return defineComponent({
    setup() {
      useDocumentMeta({ title: () => title, description: () => description })
    },
    render: () => null,
  })
}

describe('useDocumentMeta', () => {
  afterEach(() => {
    document.head.querySelector('meta[name="description"]')?.remove()
  })

  it('sets the document title and description on mount', () => {
    mount(makeHarness('Hello Title', 'Hello Description'))
    expect(document.title).toBe('Hello Title')
    expect(
      document.head.querySelector<HTMLMetaElement>('meta[name="description"]')?.content,
    ).toBe('Hello Description')
  })

  it('restores the previous title on unmount', () => {
    document.title = 'Previous'
    const wrapper = mount(makeHarness('Temporary', 'Desc'))
    expect(document.title).toBe('Temporary')
    wrapper.unmount()
    expect(document.title).toBe('Previous')
  })

  it('reuses an existing description tag rather than duplicating it', () => {
    const existing = document.createElement('meta')
    existing.name = 'description'
    existing.content = 'original'
    document.head.appendChild(existing)

    mount(makeHarness('T', 'updated'))
    const tags = document.head.querySelectorAll('meta[name="description"]')
    expect(tags).toHaveLength(1)
    expect(tags[0]?.getAttribute('content')).toBe('updated')
  })
})
