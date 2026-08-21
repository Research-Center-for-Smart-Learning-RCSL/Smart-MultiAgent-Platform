import { describe, it, expect, afterEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentDetailView from '../views/AgentDetailView.vue'

const routes = [
  { path: '/agents/:agentId', name: 'agents.detail', component: AgentDetailView },
  { path: '/projects/:projectId/agents', name: 'agents.list', component: { template: '<div />' } },
  { path: '/agents/:agentId/mcp', name: 'agents.mcp', component: { template: '<div />' } },
  {
    path: '/projects/:projectId/graphrag-configs',
    name: 'agents.graphragConfigs',
    component: { template: '<div />' },
  },
  {
    path: '/projects/:projectId/rag-configs/:configId',
    name: 'agents.ragConfig',
    component: { template: '<div />' },
  },
]

// agentCreateSchema requires a uuid, so the create-mode test cannot reuse the
// 'kg_1' placeholder the edit-mode fixture carries (edit mode never submits).
const KEY_GROUP_ID = '11111111-1111-4111-8111-111111111111'

const AGENT = {
  id: 'agent_1',
  project_id: 'proj_1',
  name: 'My Bot',
  model_hint: 'openai',
  model_id: null,
  effort: null,
  key_group_id: 'kg_1',
  system_prompt: 'You are helpful.',
  rag_config_id: null,
  context_mode: 'general',
  context_token_cap: null,
  temperature: null,
  top_p: null,
  seed: null,
  a2a_enabled: false,
  wakeup_config: {},
  workflow_capabilities: {},
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  deleted_at: null,
}

function seed(): void {
  server.use(
    http.get('/api/agents/agent_1', () => HttpResponse.json(AGENT)),
    http.get('/api/projects/proj_1/key-groups', () =>
      HttpResponse.json([
        { id: 'kg_1', project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
    http.get('/api/projects/proj_1/rag-configs', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([])),
    http.get('/api/agents/agent_1/mcp', () => HttpResponse.json([])),
    http.get('/api/agents/agent_1/concept-map-coverage', () =>
      HttpResponse.json({
        agent_id: 'agent_1',
        entries: [
          {
            config_id: 'gr_1',
            owner_kind: 'chatroom',
            owner_id: 'room_1',
            owner_name: 'General Room',
            active: true,
            last_build_state: 'idle',
            last_build_at: null,
            last_build_error: null,
          },
        ],
      }),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('AgentDetailView', () => {
  it('populates the form from the fetched agent', async () => {
    seed()
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)
    // Name is the first text input on the General tab.
    const nameInput = wrapper.find('.s-input__field').element as HTMLInputElement
    expect(nameInput.value).toBe('My Bot')
    // Model provider is the first select.
    const modelSelect = wrapper.find('.s-select__native').element as HTMLSelectElement
    expect(modelSelect.value).toBe('openai')
  })

  it('shows the tabbed configuration layout in edit mode', async () => {
    seed()
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('My Bot')
    // A delete button is present in edit mode (danger variant).
    expect(wrapper.find('button.s-btn--danger').exists()).toBe(true)
  })

  it('renders sampling controls and shows a pinned temperature of 0 (AC-1)', async () => {
    seed()
    // Later server.use wins: pin the reproducible-scoring config on the agent.
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, temperature: 0, top_p: 0.9, seed: 123 }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)
    // The sampling section and its OpenAI-only seed note render (i18n keys in test).
    expect(wrapper.text()).toContain('agents.form.samplingTitle')
    expect(wrapper.text()).toContain('agents.form.seedHelp')
    // temperature=0 must display as "0", not blank — 0 is a valid pinned value,
    // distinct from "unset" (provider default). top_p/seed round-trip too.
    const values = wrapper
      .findAll('.s-input__field')
      .map((i) => (i.element as HTMLInputElement).value)
    expect(values).toContain('0')
    expect(values).toContain('0.9')
    expect(values).toContain('123')
  })

  // Create mode reaches the API through a different path than edit (POST to the
  // project, key group defaulted from the query rather than loaded from the
  // agent), and it is what the 04-agent-rag-flow E2E spec drives. Pin the exact
  // payload so a schema or default change that would only surface as an opaque
  // E2E timeout fails here instead.
  it('posts the assembled payload in create mode', async () => {
    const posted = vi.fn()
    server.use(
      http.get('/api/projects/proj_1/key-groups', () =>
        HttpResponse.json([
          { id: KEY_GROUP_ID, project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get('/api/projects/proj_1/rag-configs', () => HttpResponse.json([])),
      http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json([])),
      http.get('/api/projects/proj_1/knowmap-configs', () => HttpResponse.json([])),
      http.post('/api/projects/proj_1/agents', async ({ request }) => {
        posted(await request.json())
        return HttpResponse.json({ ...AGENT, id: 'agent_new' }, { status: 201 })
      }),
    )

    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/new?projectId=proj_1',
    })
    await settle(wrapper)

    // Name is the first text input; model provider the first select. The key
    // group is never touched — it must default to the project's only group, or
    // key_group_id fails its uuid() check and the submit is silently dropped.
    await wrapper.findAll('.s-input__field')[0]!.setValue('e2e-agent')
    await wrapper.findAll('.s-select__native')[0]!.setValue('openai')
    await settle(wrapper)

    await wrapper.findAll('button.s-btn--primary')[0]!.trigger('click')
    await settle(wrapper)

    expect(posted).toHaveBeenCalledTimes(1)
    expect(posted.mock.calls[0]![0]).toMatchObject({
      name: 'e2e-agent',
      model_hint: 'openai',
      key_group_id: KEY_GROUP_ID,
    })
  })

  // soft_bounds (R15.08) is admin-set through the API and has no editor control,
  // so an unrelated save from this view used to drop it from the payload — and
  // the backend wrote that payload into wakeup_authored_snapshot too, erasing the
  // designer's bound with no recovery path.
  it('keeps unmodelled wakeup_config keys in the patch payload', async () => {
    const patched = vi.fn()
    seed()
    server.use(
      // The shared fixture's 'kg_1' is not a uuid, so the schema rejects the
      // submit before it reaches the mutation (see the create-mode note above).
      http.get('/api/projects/proj_1/key-groups', () =>
        HttpResponse.json([
          { id: KEY_GROUP_ID, project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          key_group_id: KEY_GROUP_ID,
          wakeup_config: {
            triggers: { every_n_messages: { enabled: true, n: 8 } },
            soft_bounds: { n_min: 5, n_max: 10 },
          },
        }),
      ),
      http.patch('/api/agents/agent_1', async ({ request }) => {
        patched(await request.json())
        return HttpResponse.json({ ...AGENT, version: 2 })
      }),
    )

    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    // Touch an unrelated field so the form is dirty, then save.
    await wrapper.findAll('.s-input__field')[0]!.setValue('Renamed Bot')
    await settle(wrapper)
    await wrapper.findAll('button.s-btn--primary')[0]!.trigger('click')
    await settle(wrapper)

    expect(patched).toHaveBeenCalledTimes(1)
    const body = patched.mock.calls[0]![0] as { wakeup_config: Record<string, unknown> }
    expect(body.wakeup_config.soft_bounds).toEqual({ n_min: 5, n_max: 10 })
  })

  // T-11 (workflow-capability-enforcement spec §7.6, AC-9): the default was a
  // stale 5 (R15.20 / SUBAGENT_MAX_CONCURRENT_DEFAULT says 3).
  it('defaults max_alive_subagents to 3 when unset', async () => {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, workflow_capabilities: { can_create_subagent: true } }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1?tab=orchestration',
    })
    await settle(wrapper)

    // SFormField assigns the control an id matching its `name` prop.
    const maxAliveInput = wrapper.find('#max_alive_subagents').element as HTMLInputElement
    expect(maxAliveInput.value).toBe('3')
  })

  // Clearing the box to retype used to persist 0 via SInput's type="number"
  // coercion (0 * 0 = 0 is out of R15.20's 1..20 range). The guarded model
  // leaves the in-memory value untouched on an empty/non-numeric edit, so a
  // save triggered without retyping still submits the last valid number, not 0.
  it('does not let clearing max_alive_subagents persist 0 on save', async () => {
    const patched = vi.fn()
    seed()
    server.use(
      http.get('/api/projects/proj_1/key-groups', () =>
        HttpResponse.json([
          { id: KEY_GROUP_ID, project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          key_group_id: KEY_GROUP_ID,
          workflow_capabilities: { can_create_subagent: true, max_alive_subagents: 7 },
        }),
      ),
      http.patch('/api/agents/agent_1', async ({ request }) => {
        patched(await request.json())
        return HttpResponse.json({ ...AGENT, version: 2 })
      }),
    )

    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1?tab=orchestration',
    })
    await settle(wrapper)

    await wrapper.find('#max_alive_subagents').setValue('')
    await settle(wrapper)

    // Touch an unrelated field so the form is dirty, then save.
    await wrapper.findAll('.s-input__field')[0]!.setValue('Renamed Bot')
    await settle(wrapper)
    await wrapper.findAll('button.s-btn--primary')[0]!.trigger('click')
    await settle(wrapper)

    expect(patched).toHaveBeenCalledTimes(1)
    const body = patched.mock.calls[0]![0] as { workflow_capabilities: Record<string, unknown> }
    expect(body.workflow_capabilities.max_alive_subagents).toBe(7)
  })

  // F-27: the loading branch rendered 1 + 5 + 2 flat skeletons against a
  // settled General tab of two SCards of form fields, so the page grew
  // downward under the cursor when the query landed. 06-agents.md §2.10 fixes
  // the shape: a 200px header line, five tab rects, two cards of four fields.
  it('renders the documented loading skeleton shape', async () => {
    seed()
    // Deliberately no settle — the query is still in flight here.
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })

    expect(wrapper.findAll('.s-card')).toHaveLength(2)
    // 1 header line + 5 tab rects + 2 cards x 4 field rects.
    expect(wrapper.findAll('.s-skeleton')).toHaveLength(14)
  })

  // F-16 and F-51 are the same attribute. F-16: below 1024px the panel's cell
  // had only a min-height, so its `h-full` resolved against an indefinite
  // height, its message list never engaged its own scroll region, and the
  // composer was pushed off the page. F-51: the lg constant counted the shell's
  // padding once (8rem) where the geometry needs the topbar plus a symmetric
  // 24px pair, leaving a 24px dead band. jsdom lays nothing out, so this pins
  // the declaration; the geometry is the e2e tier's job (T-14, T-15).
  it('gives the prompt assistant a definite height in both layout modes', async () => {
    seed()
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1?tab=prompt',
    })
    await settle(wrapper)

    const html = wrapper.html()
    expect(html).toContain('h-[32rem]')
    expect(html).not.toContain('min-h-[32rem]')
    // dvh, paired with the shell (App.vue's .app-root). The `100vh` spelling
    // below is the superseded F-51 constant and stays as written: it pins the
    // absence of a value that no longer exists in either unit.
    expect(html).toContain('lg:h-[calc(100dvh-3.5rem-3rem)]')
    expect(html).not.toContain('lg:h-[calc(100vh-8rem)]')
  })

  it('shows read-only Concept Map coverage on the Knowledge tab (AC-6)', async () => {
    seed()
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1?tab=knowledge',
    })
    await settle(wrapper)
    // The covering map's owner name renders (transparency), not an attach control.
    expect(wrapper.text()).toContain('General Room')
    expect(wrapper.text()).toContain('agents.graphragCoverage.active')
    // No Concept Map attach select was ever added — only the rag_config_id one.
    expect(wrapper.text()).not.toContain('agents.graphragForm.agent')
  })

  // T-4 of docs/tasks/2026-08-19-mobile-viewport-and-breakpoints (F-18). The
  // mobile action bar was `position: fixed`, so it took no part in flow and
  // contributed nothing to the scroll height of `main.app-shell__content` —
  // the scroll range ended roughly 57px (sm) / 65px (xs) before the content the
  // bar covers, and no amount of scrolling reached the last control.
  //
  // jsdom lays nothing out, so this is a structural assertion; the geometry is
  // AC-12's job in the e2e tier at 375x812.
  describe('mobile action bar', () => {
    const desktopWidth = window.innerWidth

    function setViewport(width: number): void {
      // useBreakpoint holds a module-level width ref that re-syncs on mount.
      Object.defineProperty(window, 'innerWidth', {
        value: width,
        configurable: true,
        writable: true,
      })
    }

    afterEach(() => setViewport(desktopWidth))

    async function renderMobile(route: string) {
      seed()
      setViewport(375)
      const wrapper = await renderView(AgentDetailView, { routes, initialRoute: route })
      await settle(wrapper)
      return wrapper
    }

    function actionBar(wrapper: Awaited<ReturnType<typeof renderMobile>>) {
      return wrapper.findAll('div').find((el) => el.classes().includes('sticky'))
    }

    it('reserves its own height instead of floating over the form', async () => {
      const wrapper = await renderMobile('/agents/agent_1?tab=prompt')

      const bar = actionBar(wrapper)
      expect(bar).toBeDefined()
      expect(bar!.classes()).toContain('bottom-0')
      // The three that put it out of flow. `left-0`/`right-0` only mean
      // anything to a fixed box and would stretch a sticky one edge to edge.
      expect(bar!.classes()).not.toContain('fixed')
      expect(bar!.classes()).not.toContain('left-0')
      expect(bar!.classes()).not.toContain('right-0')
    })

    // Already true before the fix and kept as a characterization pin: the bar
    // is the last flow child of the view root, which is itself inside the
    // shell's scroll container. Moving it out again would restore the
    // occlusion by a different route, with the class list still innocent.
    it('renders as the last flow child of the view root', async () => {
      const wrapper = await renderMobile('/agents/agent_1?tab=prompt')

      const last = (wrapper.element as HTMLElement).lastElementChild
      expect(last).not.toBeNull()
      expect(last!.className).toContain('sticky')
    })

    it('is absent on desktop, where the page header carries the actions', async () => {
      seed()
      const wrapper = await renderView(AgentDetailView, {
        routes,
        initialRoute: '/agents/agent_1?tab=prompt',
      })
      await settle(wrapper)

      expect(actionBar(wrapper)).toBeUndefined()
    })
  })
})
