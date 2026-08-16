// AC-4/AC-14: the Project Owner's opt-in dialog for platform-installed examples.
//
// The consent notice is the assertion that matters most here. Enabling an example
// whose `expose_payload_to_agent` is set sends participant text to the project's
// LLM provider, and [R30.32]/§8 of the dossier make stating that at the point of
// choosing part of the feature rather than documentation someone might read.

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import { renderView } from '../../../../tests/utils'
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

  it('does not query while closed', async () => {
    listMock.mockResolvedValue([])

    await renderView(ExampleImportDialog, { props: { projectId: 'p1', open: false } })
    await flushPromises()

    expect(listMock).not.toHaveBeenCalled()
  })
})
