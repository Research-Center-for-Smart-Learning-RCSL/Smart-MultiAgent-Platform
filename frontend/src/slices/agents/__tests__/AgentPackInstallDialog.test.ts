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
// Hoisted rather than fresh per call: `useToast()` returning new `vi.fn()`s each
// time made every toast in this file unassertable, which is why the dialog could
// ship reporting neither the resolved provider nor the group it created.
const toastSuccess = vi.hoisted(() => vi.fn())
const toastInfo = vi.hoisted(() => vi.fn())
const toastError = vi.hoisted(() => vi.fn())
const toastWarning = vi.hoisted(() => vi.fn())

vi.mock('@shared/composables', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    info: toastInfo,
    warning: toastWarning,
  }),
}))
// The harness loads no messages, so the real `t` returns the bare key and an
// assertion on it cannot see interpolation params -- which is precisely what
// AC-5 (the provider actually used) is about. This fake appends them. Safe for
// this tree: every component in it (SModal, SBadge) destructures only `t`.
vi.mock('vue-i18n', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params === undefined ? key : `${key}|${JSON.stringify(params)}`,
  }),
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

function installedAgent(over: Record<string, unknown> = {}) {
  return { key: 'ta-guidance-teacher', name: 'TA', agent_id: 'a1', model_hint: 'claude', ...over }
}

function installReport(over: Record<string, unknown> = {}) {
  return {
    pack_key: 'creative-thinking-room',
    created: [installedAgent()],
    already_present: [],
    group_id: 'g1',
    group_created: true,
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
  toastSuccess.mockReset()
  toastInfo.mockReset()
  toastError.mockReset()
  toastWarning.mockReset()
  listKeyGroupsMock.mockResolvedValue([{ id: 'kg1', name: 'Group one' }])
  installMock.mockResolvedValue(installReport())
})

/** Click install on the room pack and let the mutation settle. */
async function installRoomPack(wrapper: Awaited<ReturnType<typeof mountDialog>>): Promise<void> {
  await wrapper.find('[data-testid="install-creative-thinking-room"]').trigger('click')
  await flushPromises()
}

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

  it('labels a design agent as belonging in no class room, and says its drafts are copied by hand (AC-14)', async () => {
    listPacksMock.mockResolvedValue([
      pack({ pack_key: 'creative-thinking-design', agents: [packAgent({ room_role: null })] }),
    ])

    const wrapper = await mountDialog()
    await flushPromises()

    // Both halves of AC-14. The badge alone satisfied the first and left the
    // second -- that applying a draft is a manual copy and paste -- unstated,
    // which is the misreading a "design agent" invites.
    expect(wrapper.text()).toContain('agents.examplePacks.roleDesign')
    expect(wrapper.text()).toContain('agents.examplePacks.designNotice')
  })

  it('omits the design-agent note when no listed pack carries one', async () => {
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    expect(wrapper.text()).not.toContain('agents.examplePacks.designNotice')
  })

  it('names each agent preferred provider and the activities it is written for', async () => {
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()

    // A preference, not the resolved value: which provider an agent ends up on
    // is decided server-side against the chosen key group, and no endpoint
    // answers that before the install runs.
    expect(wrapper.text()).toContain('agents.examplePacks.prefersProvider')
    expect(wrapper.text()).toContain('agents.examplePacks.bindsActivities')
  })

  it('reports the provider actually used once the install returns', async () => {
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()
    await installRoomPack(wrapper)

    // The resolved hint off the report, not the pack's preference: the two
    // diverge whenever the chosen key group cannot serve what the pack asked for.
    expect(toastSuccess).toHaveBeenCalledWith(
      expect.stringContaining('agents.examplePacks.installed'),
    )
    expect(toastSuccess).toHaveBeenCalledWith(expect.stringContaining('claude'))
  })

  it('reports every distinct provider when agents resolved differently', async () => {
    listPacksMock.mockResolvedValue([pack()])
    installMock.mockResolvedValue(
      installReport({
        created: [
          installedAgent({ key: 'ta', model_hint: 'claude' }),
          installedAgent({ key: 'sa', name: 'SA', agent_id: 'a2', model_hint: 'openai' }),
          installedAgent({ key: 'aa', name: 'AA', agent_id: 'a3', model_hint: 'claude' }),
        ],
        group_created: false,
      }),
    )

    const wrapper = await mountDialog()
    await flushPromises()
    await installRoomPack(wrapper)

    const message = toastSuccess.mock.calls.at(-1)?.[0] as string
    expect(message).toContain('claude')
    expect(message).toContain('openai')
    // Distinct, not one entry per agent.
    expect(message.match(/claude/g)).toHaveLength(1)
  })

  it('names the group when the install created one', async () => {
    listPacksMock.mockResolvedValue([pack()])

    const wrapper = await mountDialog()
    await flushPromises()
    await installRoomPack(wrapper)

    expect(toastSuccess).toHaveBeenCalledWith(
      expect.stringContaining('agents.examplePacks.groupCreated'),
    )
    // Named, not just announced -- the owner needs to know which group to look at.
    expect(toastSuccess).toHaveBeenCalledWith(expect.stringContaining('Course agents'))
  })

  it('says nothing about a group when the install reused one', async () => {
    listPacksMock.mockResolvedValue([pack()])
    installMock.mockResolvedValue(installReport({ group_created: false }))

    const wrapper = await mountDialog()
    await flushPromises()
    await installRoomPack(wrapper)

    expect(toastSuccess).not.toHaveBeenCalledWith(
      expect.stringContaining('agents.examplePacks.groupCreated'),
    )
  })

  it('says both halves when a run created only a group', async () => {
    // F-8 at the surface the installer actually reads. Every agent was already
    // present, so `created` is empty -- but a second group now exists. Reporting
    // only "nothing was installed" was the defect; reporting only the group
    // drops the reason there is nothing else to report.
    listPacksMock.mockResolvedValue([pack()])
    installMock.mockResolvedValue(
      installReport({ created: [], already_present: ['TA'], group_id: 'g2' }),
    )

    const wrapper = await mountDialog()
    await flushPromises()
    await installRoomPack(wrapper)

    expect(toastInfo).not.toHaveBeenCalled()
    const message = toastSuccess.mock.calls.at(-1)?.[0] as string
    expect(message).toContain('agents.examplePacks.nothingNewButGroupCreated')
    expect(message).toContain('Course agents')
    // Not the bare group message, which would say nothing about the agents.
    expect(toastSuccess).not.toHaveBeenCalledWith(
      expect.stringContaining('agents.examplePacks.groupCreated'),
    )
  })

  it('still says nothing was installed when neither an agent nor a group was created', async () => {
    listPacksMock.mockResolvedValue([pack()])
    installMock.mockResolvedValue(
      installReport({ created: [], already_present: ['TA'], group_created: false }),
    )

    const wrapper = await mountDialog()
    await flushPromises()
    await installRoomPack(wrapper)

    expect(toastInfo).toHaveBeenCalledWith(
      expect.stringContaining('agents.examplePacks.nothingToInstall'),
    )
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
