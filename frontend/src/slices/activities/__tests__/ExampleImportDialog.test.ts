// AC-4/AC-14: the Project Owner's opt-in dialog for platform-installed examples.
//
// The consent notice is the assertion that matters most here. Enabling an example
// whose `expose_payload_to_agent` is set sends participant text to the project's
// LLM provider, and [R30.32]/§8 of the dossier make stating that at the point of
// choosing part of the feature rather than documentation someone might read.

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import { renderView, deferred } from '../../../../tests/utils'
import { i18n } from '@shared/i18n'
import sharedZhTW from '@shared/locales/zh-TW.json'
import ExampleImportDialog from '../components/ExampleImportDialog.vue'

const listMock = vi.hoisted(() => vi.fn())
const optInMock = vi.hoisted(() => vi.fn())
const optOutMock = vi.hoisted(() => vi.fn())
const confirmMock = vi.hoisted(() => vi.fn())
const toastWarningMock = vi.hoisted(() => vi.fn())

vi.mock('../api', () => ({
  listPlatformExamples: listMock,
  optIntoActivityType: optInMock,
  optOutOfActivityType: optOutMock,
}))
vi.mock('@shared/composables', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useConfirmDialog: () => ({ confirm: confirmMock }),
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: toastWarningMock,
  }),
}))

function example(over: Record<string, unknown> = {}) {
  return {
    id: 'pt1',
    key: 'mandala-9grid',
    name: 'Mandala',
    expose_payload_to_agent: true,
    echo_includes_content: false,
    retention_days: null,
    enabled: false,
    ...over,
  }
}

function mountDialog() {
  return renderView(ExampleImportDialog, { props: { projectId: 'p1', open: true } })
}

beforeEach(() => {
  listMock.mockReset()
  optInMock.mockReset()
  optOutMock.mockReset()
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(true)
  toastWarningMock.mockReset()
})

describe('ExampleImportDialog', () => {
  it('states that answers reach the AI provider before anything is enabled (AC-14)', async () => {
    listMock.mockResolvedValue([example()])

    const wrapper = await mountDialog()
    await flushPromises()

    // Present on load, not after a toggle: the owner needs it while choosing.
    expect(wrapper.text()).toContain('activities.examples.agentExposureNotice')
    expect(wrapper.text()).toContain('activities.examples.exposesToAgent')
    expect(optInMock).not.toHaveBeenCalled()
  })

  it('omits the notice when no example exposes answers to an agent', async () => {
    listMock.mockResolvedValue([example({ expose_payload_to_agent: false })])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).not.toContain('activities.examples.agentExposureNotice')
  })

  it('enables an example and refreshes the list (AC-4)', async () => {
    listMock.mockResolvedValue([example()])
    optInMock.mockResolvedValue({ shadows_owned_key: false })

    const wrapper = await mountDialog()
    await flushPromises()

    const enable = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('activities.examples.enable'))
    expect(enable).toHaveLength(1)

    await enable[0].trigger('click')
    await flushPromises()

    expect(optInMock).toHaveBeenCalledWith('p1', 'pt1')
    expect(toastWarningMock).not.toHaveBeenCalled()
  })

  it('warns when enabling shadows a key the project already owns (AC-2)', async () => {
    // One click is all it takes to leave the project holding two live types
    // under one key ([R30.02]) — permitted, but not something to discover later
    // from a workflow rule firing twice.
    listMock.mockResolvedValue([example()])
    optInMock.mockResolvedValue({ shadows_owned_key: true })

    const wrapper = await mountDialog()
    await flushPromises()

    const enable = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('activities.examples.enable'))
    await enable[0].trigger('click')
    await flushPromises()

    // Enabled, not refused: the success path still ran.
    expect(optInMock).toHaveBeenCalledWith('p1', 'pt1')
    expect(toastWarningMock).toHaveBeenCalledWith('activities.examples.shadowsOwnedKey')
  })

  it('confirms before disabling, because it ends running activations', async () => {
    listMock.mockResolvedValue([example({ enabled: true })])
    optOutMock.mockResolvedValue(undefined)

    const wrapper = await mountDialog()
    await flushPromises()

    const disable = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('activities.examples.disable'))
    await disable[0].trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledOnce()
    expect(optOutMock).toHaveBeenCalledWith('p1', 'pt1')
  })

  it('does not disable when the confirm is declined', async () => {
    listMock.mockResolvedValue([example({ enabled: true })])
    confirmMock.mockResolvedValue(false)

    const wrapper = await mountDialog()
    await flushPromises()

    const disable = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('activities.examples.disable'))
    await disable[0].trigger('click')
    await flushPromises()

    expect(optOutMock).not.toHaveBeenCalled()
  })

  // -- Concurrency: one request at a time, for the whole dialog (AC-2/AC-3) --
  //
  // Both mutations invalidate the same two query keys, so two in-flight requests
  // race on the same cache entry whichever rows they touch. Gating on "is this
  // row the pending one" was only ever equivalent to "is anything pending" while
  // a second request could not start, and starting one is what broke it.

  /** The four platform examples a fully installed course lists. */
  function fourExamples(over: Record<string, unknown> = {}) {
    return ['pt1', 'pt2', 'pt3', 'pt4'].map((id) =>
      example({ id, key: `unit-${id}`, name: `Unit ${id}`, ...over }),
    )
  }

  function actionButtons(wrapper: Awaited<ReturnType<typeof mountDialog>>) {
    return wrapper
      .findAll('button')
      .filter(
        (b) =>
          b.text().includes('activities.examples.enable') ||
          b.text().includes('activities.examples.disable'),
      )
  }

  it('disables every action button while any request is in flight (AC-2)', async () => {
    listMock.mockResolvedValue(fourExamples())
    const gate = deferred<{ shadows_owned_key: boolean }>()
    optInMock.mockReturnValue(gate.promise)

    const wrapper = await mountDialog()
    await flushPromises()

    const before = actionButtons(wrapper)
    expect(before).toHaveLength(4)
    expect(before.every((b) => b.attributes('disabled') === undefined)).toBe(true)

    await before[0].trigger('click')
    await flushPromises()

    // Not only row 1's: rows 2-4 are what the identity check left clickable.
    const during = actionButtons(wrapper)
    expect(during).toHaveLength(4)
    for (const button of during) expect(button.attributes('disabled')).toBeDefined()

    // With every button inert, the spinner is the only thing left saying which
    // row the click landed on, so it has to be on that row and no other.
    expect(during.map((b) => b.classes('s-btn--loading'))).toEqual([true, false, false, false])

    gate.resolve({ shadows_owned_key: false })
    await flushPromises()

    for (const button of actionButtons(wrapper)) {
      expect(button.attributes('disabled')).toBeUndefined()
    }
  })

  it('issues no second request from a click on another row mid-flight (AC-3)', async () => {
    listMock.mockResolvedValue(fourExamples())
    const gate = deferred<{ shadows_owned_key: boolean }>()
    optInMock.mockReturnValue(gate.promise)

    const wrapper = await mountDialog()
    await flushPromises()

    await actionButtons(wrapper)[0].trigger('click')
    await flushPromises()
    await actionButtons(wrapper)[1].trigger('click')
    await flushPromises()

    // The second click moved `pendingId` off row 1, which re-enabled row 1's
    // button while its own POST was still outstanding.
    expect(optInMock).toHaveBeenCalledTimes(1)
    expect(optInMock).toHaveBeenCalledWith('p1', 'pt1')

    gate.resolve({ shadows_owned_key: false })
    await flushPromises()
  })

  it('blocks a duplicate opt-out, which the backend reports as a failure (AC-3)', async () => {
    // The worse half of the same defect: opt_out is not idempotent, so a second
    // one raises ActivityTypeNotOptedIn and the user is shown disableFailed for
    // a disable that in fact succeeded.
    listMock.mockResolvedValue(fourExamples({ enabled: true }))
    const gate = deferred<void>()
    optOutMock.mockReturnValue(gate.promise)

    const wrapper = await mountDialog()
    await flushPromises()

    await actionButtons(wrapper)[0].trigger('click')
    await flushPromises()

    const during = actionButtons(wrapper)
    for (const button of during) expect(button.attributes('disabled')).toBeDefined()
    expect(during.map((b) => b.classes('s-btn--loading'))).toEqual([true, false, false, false])

    await actionButtons(wrapper)[0].trigger('click')
    await actionButtons(wrapper)[1].trigger('click')
    await flushPromises()

    expect(optOutMock).toHaveBeenCalledTimes(1)
    expect(confirmMock).toHaveBeenCalledTimes(1)

    gate.resolve()
    await flushPromises()
  })

  it('keeps the buttons inert until the invalidated list has been re-read', async () => {
    // The second window, and the one that still misreports. `invalidate()` fires
    // `void qc.invalidateQueries(...)` and `onSuccess` returns void, so vue-query
    // never awaits the refetch: `isPending` is already false while the list still
    // says `enabled: true`. Acting on that stale row issues a second opt-out,
    // which `opt_out` refuses with ActivityTypeNotOptedIn, and the user is shown
    // `disableFailed` for a disable that succeeded.
    const refetch = deferred<ReturnType<typeof example>[]>()
    let call = 0
    listMock.mockImplementation(() => {
      call += 1
      return call === 1 ? Promise.resolve([example({ enabled: true })]) : refetch.promise
    })
    optOutMock.mockResolvedValue(undefined)

    const wrapper = await mountDialog()
    await flushPromises()

    const disable = () =>
      wrapper.findAll('button').filter((b) => b.text().includes('activities.examples.disable'))[0]

    await disable().trigger('click')
    await flushPromises()

    expect(optOutMock).toHaveBeenCalledTimes(1)
    // The mutation has settled, but the row on screen is still the pre-opt-out one.
    expect(disable().attributes('disabled')).toBeDefined()

    refetch.resolve([example({ enabled: false })])
    await flushPromises()

    // Re-read, and the row is now an Enable button that accepts a click again.
    const enable = wrapper
      .findAll('button')
      .filter((b) => b.text().includes('activities.examples.enable'))
    expect(enable).toHaveLength(1)
    expect(enable[0].attributes('disabled')).toBeUndefined()
    expect(optOutMock).toHaveBeenCalledTimes(1)
  })

  it('shows the empty state naming who can install one', async () => {
    listMock.mockResolvedValue([])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('activities.examples.emptyTitle')
    expect(wrapper.text()).toContain('activities.examples.emptyText')
  })

  it('shows an error state with a retry when the listing fails', async () => {
    listMock.mockRejectedValue(new Error('boom'))

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('activities.examples.errorText')
    expect(wrapper.text()).not.toContain('activities.examples.emptyTitle')
  })

  it('translates the footer button under zh-TW rather than falling back to English', async () => {
    // The reported symptom, pinned at the surface it was reported on: every
    // string on this dialog is Chinese except the footer, which read "Close".
    // The call site was never wrong -- it passes the key and an English default,
    // so the default rendered for want of a `common` namespace to resolve.
    listMock.mockResolvedValue([example()])
    const previous = i18n.global.locale.value
    i18n.global.mergeLocaleMessage('zh-TW', sharedZhTW)
    i18n.global.locale.value = 'zh-TW'

    try {
      const wrapper = await mountDialog()
      await flushPromises()

      const close = sharedZhTW.common.close
      expect(close).not.toBe('Close')
      expect(wrapper.findAll('button').filter((b) => b.text() === close)).toHaveLength(1)
      expect(wrapper.text()).not.toContain('Close')
    } finally {
      i18n.global.locale.value = previous
    }
  })

  it('does not query while closed', async () => {
    listMock.mockResolvedValue([])

    await renderView(ExampleImportDialog, { props: { projectId: 'p1', open: false } })
    await flushPromises()

    expect(listMock).not.toHaveBeenCalled()
  })
})
