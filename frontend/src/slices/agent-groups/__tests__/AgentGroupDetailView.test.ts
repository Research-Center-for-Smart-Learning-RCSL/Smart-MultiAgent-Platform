import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import type * as Composables from '@shared/composables'

// Captures the removal confirmation (and answers "no") so a test can read the
// message without a dialog host.
const confirmMock = vi.fn(async (_opts: { message: string }) => false)
vi.mock('@shared/composables', async (importOriginal) => ({
  ...(await importOriginal<typeof Composables>()),
  useConfirmDialog: () => ({ confirm: confirmMock }),
}))
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import AgentGroupDetailView from '../views/AgentGroupDetailView.vue'

const routes = [
  { path: '/agent-groups/:groupId', name: 'agentGroups.detail', component: AgentGroupDetailView },
  {
    path: '/projects/:projectId/graphrag-configs/:configId/graph',
    name: 'agents.graphragGraph',
    component: { template: '<div />' },
  },
]

const GROUP = {
  id: 'g_1',
  project_id: 'proj_1',
  name: 'Research Team',
  concept_map_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
}

const AGENTS = [
  { id: 'a_1', project_id: 'proj_1', name: 'Alice', key_group_id: 'kg_1' },
  { id: 'a_2', project_id: 'proj_1', name: 'Bob', key_group_id: 'kg_1' },
]

function signInAs(userId: string): void {
  const session = useSessionStore()
  session.me = { id: userId, email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
}

type Reads = Record<string, string[] | null>

function seed(role: 'owner' | 'member', members: string[] = ['a_1'], reads: Reads = {}): void {
  server.use(
    http.get('/api/agent-groups/g_1', () => HttpResponse.json(GROUP)),
    http.get('/api/agent-groups/g_1/members', () =>
      HttpResponse.json({
        members,
        read_members: members.map((id) => ({ agent_id: id, read_member_ids: reads[id] ?? null })),
      }),
    ),
    http.get('/api/projects/proj_1/agents', () => HttpResponse.json(AGENTS)),
    http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([{ user_id: 'u_1', email: 'u@smap.test', role, joined_at: '2026-01-01T00:00:00Z' }]),
    ),
    // `useProjectRole` reads the server's verdict, not the member list — see
    // its comment: ownership is inherited, so the list cannot answer it.
    http.get('/api/projects/proj_1', () =>
      HttpResponse.json({ id: 'proj_1', name: 'Test Project', is_moderator: role === 'owner' }),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 160))
  await wrapper.vm.$nextTick()
}

describe('AgentGroupDetailView', () => {
  it('resolves member agent names', async () => {
    seed('owner')
    const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
    signInAs('u_1')
    await settle(wrapper)
    expect(wrapper.text()).toContain('Research Team')
    expect(wrapper.text()).toContain('Alice')
  })

  it('enables the member add form + privacy toggle for an owner', async () => {
    seed('owner')
    const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
    signInAs('u_1')
    await settle(wrapper)
    // The add-member SSelect (a native select) is present and enabled for owners.
    const select = wrapper.find('.s-select__native')
    expect(select.exists()).toBe(true)
    expect((select.element as HTMLSelectElement).disabled).toBe(false)
    // The privacy toggle (a switch button, role="switch") is present and enabled.
    const toggle = wrapper.find('.s-toggle__track')
    expect(toggle.exists()).toBe(true)
    expect((toggle.element as HTMLButtonElement).disabled).toBe(false)
  })

  it('renders but disables mutating controls for a non-owner', async () => {
    // Hardened pattern: controls always render (a real owner never sees them
    // flash in) but stay disabled until authorization resolves true — a
    // non-owner gets inert controls, not hidden ones.
    seed('member')
    const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
    signInAs('u_1')
    await settle(wrapper)
    const select = wrapper.find('.s-select__native')
    expect(select.exists()).toBe(true)
    expect((select.element as HTMLSelectElement).disabled).toBe(true)
    const toggle = wrapper.find('.s-toggle__track')
    expect(toggle.exists()).toBe(true)
    expect((toggle.element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('agentGroups.detail.ownerOnly')
    expect(wrapper.text()).toContain('agentGroups.detail.privacyOwnerOnly')
    // The per-member read setting is inert for a non-owner too.
    const edit = wrapper.find('[aria-label="agentGroups.detail.editReads"]')
    expect((edit.element as HTMLButtonElement).disabled).toBe(true)
  })

  describe('reads from (R11.22)', () => {
    it('shows each member reading the whole group, chosen members, or nothing', async () => {
      seed('owner', ['a_1', 'a_2'], { a_1: ['a_2'], a_2: [] })
      const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
      signInAs('u_1')
      await settle(wrapper)
      const rows = wrapper.findAll('tbody tr')
      // Alice reads Bob's contributions; Bob's list was emptied by a removal.
      expect(rows[0]?.text()).toContain('Bob')
      expect(rows[1]?.text()).toContain('agentGroups.detail.readsNothing')
      expect(rows[1]?.text()).toContain('agentGroups.detail.readsNothingHelp')
    })

    it('saves a chosen member list, and null for the whole group', async () => {
      const bodies: unknown[] = []
      seed('owner', ['a_1', 'a_2'])
      server.use(
        http.put('/api/agent-groups/g_1/members/:agentId/read-members', async ({ request, params }) => {
          const body = (await request.json()) as { member_ids: string[] | null }
          bodies.push(body)
          return HttpResponse.json({ agent_id: params.agentId, read_member_ids: body.member_ids })
        }),
      )
      const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
      signInAs('u_1')
      await settle(wrapper)

      await wrapper.findAll('[aria-label="agentGroups.detail.editReads"]')[0]!.trigger('click')
      await wrapper.find('input[type="radio"][value="some"]').setValue(true)
      const save = () => wrapper.findAll('button').find((b) => b.text() === 'agentGroups.detail.save')!
      // Nothing chosen yet: the empty list cannot be saved.
      expect(wrapper.text()).toContain('agentGroups.detail.readsChooseOne')
      expect((save().element as HTMLButtonElement).disabled).toBe(true)

      const boxes = wrapper.findAll('input[type="checkbox"]')
      await boxes[1]!.setValue(true)
      await save().trigger('click')
      await settle(wrapper)
      expect(bodies).toEqual([{ member_ids: ['a_2'] }])

      await wrapper.findAll('[aria-label="agentGroups.detail.editReads"]')[1]!.trigger('click')
      await wrapper.find('input[type="radio"][value="all"]').setValue(true)
      await save().trigger('click')
      await settle(wrapper)
      expect(bodies[1]).toEqual({ member_ids: null })
    })

    it('warns before a removal that leaves another member reading nothing', async () => {
      confirmMock.mockClear()
      seed('owner', ['a_1', 'a_2'], { a_1: ['a_2'] })
      const wrapper = await renderView(AgentGroupDetailView, { routes, initialRoute: '/agent-groups/g_1' })
      signInAs('u_1')
      await settle(wrapper)
      // Remove Bob, the only member Alice reads.
      await wrapper.findAll('[aria-label="agentGroups.detail.removeMember"]')[1]!.trigger('click')
      expect(confirmMock).toHaveBeenCalledOnce()
      expect(confirmMock.mock.calls[0]![0].message).toContain('agentGroups.detail.removeConfirmEmpties')

      // Removing Alice empties nobody's list: the plain confirmation.
      await wrapper.findAll('[aria-label="agentGroups.detail.removeMember"]')[0]!.trigger('click')
      expect(confirmMock.mock.calls[1]![0].message).toContain('agentGroups.detail.removeConfirm')
      expect(confirmMock.mock.calls[1]![0].message).not.toContain('removeConfirmEmpties')
    })
  })
})
