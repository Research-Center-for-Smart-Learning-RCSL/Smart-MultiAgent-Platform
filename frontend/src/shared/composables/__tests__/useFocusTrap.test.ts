import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { i18n } from '@shared/i18n'
import SModal from '@shared/ui/SModal.vue'

// jsdom does not focus an element that is not in the live document, so the
// wrapper has to be attached rather than rendered detached.
// `closable: false` removes SModal's own header close button, which would
// otherwise be the first focusable element in the panel and hide which element
// the trap actually chose.
function mountModal(open: boolean) {
  return mount(SModal, {
    props: { open, title: 'Links', closable: false },
    slots: { default: '<button id="inner">Copy</button>' },
    attachTo: document.body,
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

describe('useFocusTrap (through SModal)', () => {
  afterEach(() => {
    document.body.style.overflow = ''
  })

  // Regression: the watcher had no `immediate`, so a dialog mounted already open
  // — `v-if="result"` on the wrapper with a constant `:open="true"` inside, which
  // is how the admin activation-links dialog is rendered — never fired it at all.
  // Focus stayed outside the panel, so Tab walked the page behind the modal, and
  // body scroll was never locked.
  it('traps focus and locks scroll when mounted already open', async () => {
    const w = mountModal(true)
    await nextTick()
    await nextTick()

    expect(document.activeElement?.id).toBe('inner')
    expect(document.body.style.overflow).toBe('hidden')

    w.unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('is a no-op when mounted closed, and still traps once opened', async () => {
    const w = mountModal(false)
    await nextTick()
    expect(document.body.style.overflow).toBe('')

    await w.setProps({ open: true })
    await nextTick()
    await nextTick()
    expect(document.activeElement?.id).toBe('inner')
    expect(document.body.style.overflow).toBe('hidden')

    await w.setProps({ open: false })
    await nextTick()
    expect(document.body.style.overflow).toBe('')
    w.unmount()
  })
})
