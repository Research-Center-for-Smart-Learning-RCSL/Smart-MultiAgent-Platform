// AC-14/AC-16: the install dialog for shipped agent packs.
//
// Two assertions carry weight beyond "the component renders". Installing creates
// agents and nothing else -- no chatroom, no room binding -- and a reader who
// assumes otherwise will look for a class that was never set up, so the scope
// notice is part of the feature rather than documentation. And a pack containing
// a silent observer says so before anything is installed, because an agent that
// reads a room without appearing in it is not a property to discover afterwards.

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import { renderView } from '../../../../tests/utils'
import AgentPackInstallDialog from '../components/AgentPackInstallDialog.vue'

const listPacksMock = vi.hoisted(() => vi.fn())
const installMock = vi.hoisted(() => vi.fn())
const listKeyGroupsMock = vi.hoisted(() => vi.fn())

vi.mock('../api', () => ({
  agentsApi: {
    listExamplePacks: listPacksMock,
    installExamplePack: installMock,
  },
}))
// Partial: the shared render harness pulls `keysRoutes` from this module, so a
// wholesale replacement breaks every mount before a single assertion runs.
vi.mock('@slices/keys', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  keyGroupsApi: { listForProject: listKeyGroupsMock },
}))
vi.mock('@shared/composables', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

function packAgent(over: Record<string, unknown> = {}) {
  return {
    key: 'ta-guidance-teacher',
    name: 'TA',
    room_role: 'normal',
    preferred_model_hint: 'claude',
    binds_activity_types: ['mandala-9grid'],
    installed: false,
    ...over,
  }
}

function pack(over: Record<string, unknown> = {}) {
  return {
    pack_key: 'creative-thinking-room',
    title: 'Classroom trio',
    source: 'Ke Pei-jung (2019)',
    for_course: 'creative-thinking',
    group_name: 'Course agents',
    agents: [packAgent()],
    fully_installed: false,
    ...over,
  }
}

function mountDialog() {
  return renderView(AgentPackInstallDialog, { props: { projectId: 'p1', open: true } })
}

beforeEach(() => {
  listPacksMock.mockReset()
  installMock.mockReset()
  listKeyGroupsMock.mockReset()
  listKeyGroupsMock.mockResolvedValue([{ id: 'kg1', name: 'Group one' }])
  installMock.mockResolvedValue({
    pack_key: 'creative-thinking-room',
    created: [{ key: 'ta-guidance-teacher', name: 'TA', agent_id: 'a1', model_hint: 'claude' }],
    already_present: [],
    group_id: 'g1',
  })
})

describe('AgentPackInstallDialog', () => {
  it('states what installing does and does not create, before anything is installed', async () => {
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('agents.examplePacks.installScopeNotice')
    expect(wrapper.text()).toContain('agents.examplePacks.nextSteps')
    expect(installMock).not.toHaveBeenCalled()
  })

  it('warns when a pack carries a silent observer', async () => {
    listPacksMock.mockResolvedValue([
      pack({ agents: [packAgent({ room_role: 'observer' })] }),
    ])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('agents.examplePacks.observerNotice')
  })

  it('omits the observer warning when no pack has one', async () => {
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).not.toContain('agents.examplePacks.observerNotice')
  })

  it('labels a design agent as belonging in no class room (AC-14)', async () => {
    listPacksMock.mockResolvedValue([
      pack({ pack_key: 'creative-thinking-design', agents: [packAgent({ room_role: null })] }),
    ])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('agents.examplePacks.roleDesign')
  })

  it('cannot install until a key group is chosen', async () => {
    listKeyGroupsMock.mockResolvedValue([
      { id: 'kg1', name: 'Group one' },
      { id: 'kg2', name: 'Group two' },
    ])
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    // Two groups, so nothing is preselected: which keys an agent runs on is a
    // real decision, not something to default past.
    const button = wrapper.find('[data-testid="install-creative-thinking-room"]')
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(installMock).not.toHaveBeenCalled()
  })

  it('installs with the only key group when there is exactly one', async () => {
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    await wrapper.find('[data-testid="install-creative-thinking-room"]').trigger('click')
    await flushPromises()

    expect(installMock).toHaveBeenCalledWith('p1', 'creative-thinking-room', {
      key_group_id: 'kg1',
      model_hint: null,
    })
  })

  it('says why nothing can be installed when the project has no key group', async () => {
    listKeyGroupsMock.mockResolvedValue([])
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('agents.examplePacks.noKeyGroups')
    expect(
      wrapper.find('[data-testid="install-creative-thinking-room"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('says why nothing can be installed when the key groups fail to load', async () => {
    listKeyGroupsMock.mockRejectedValue(new Error('boom'))
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('agents.examplePacks.keyGroupsFailed')
  })

  it('disables every install button while one install is in flight', async () => {
    listPacksMock.mockResolvedValue([
      pack(),
      pack({ pack_key: 'creative-thinking-design', title: 'Design agent' }),
    ])
    // Never settles, so the in-flight state is observable.
    installMock.mockReturnValue(new Promise(() => {}))

    const wrapper = await mountDialog()
    await flushPromises()

    await wrapper.find('[data-testid="install-creative-thinking-room"]').trigger('click')
    await flushPromises()

    // The other pack's button too: `pendingPack` is single-valued, so a second
    // install started here would overwrite it and the first completion would
    // clear the pending state for the wrong pack.
    expect(
      wrapper.find('[data-testid="install-creative-thinking-design"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('shows the empty state when the deployment ships no packs', async () => {
    listPacksMock.mockResolvedValue([])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('agents.examplePacks.emptyTitle')
  })

  it('surfaces a load failure with a retry rather than an empty list', async () => {
    listPacksMock.mockRejectedValue(new Error('boom'))

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('agents.examplePacks.errorText')
  })
})
