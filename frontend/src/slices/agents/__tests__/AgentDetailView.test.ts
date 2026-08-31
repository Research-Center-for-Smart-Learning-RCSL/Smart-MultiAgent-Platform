import { describe, it, expect, afterEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { i18n } from '@shared/i18n'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentDetailView from '../views/AgentDetailView.vue'
import agentsEn from '../locales/en.json'

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
    // The sampling section renders (i18n keys in test). This fixture's agent is
    // on an OpenAI model, whose Responses endpoint has no seed parameter, so
    // the seed field shows its disabled reason rather than the generic help.
    expect(wrapper.text()).toContain('agents.form.samplingTitle')
    expect(wrapper.text()).toContain('agents.form.seedDisabledReason')
    // temperature=0 must display as "0", not blank — 0 is a valid pinned value,
    // distinct from "unset" (provider default). top_p/seed round-trip too, and
    // the stored seed survives edit-load onto the disabled control (Q-7).
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
    expect(html).toContain('lg:h-[calc(100dvh-3.5rem-3rem)]')

    // The two superseded classes are assembled from fragments rather than
    // spelled out, and that is load-bearing, not style. Tailwind scans this
    // file - comments included - so a class name written anywhere in it is a
    // class name in the build. Spelled out, the superseded height class was
    // reaching dist/assets/index-*.css as a live rule that nothing renders,
    // and mobileViewportContract.test.ts, which exists to forbid that viewport
    // unit, excludes __tests__/ - so the only place still shipping it was the
    // only place its guard could not see. No fragment below is a valid
    // candidate on its own, which is what removes the rule while keeping the
    // assertion. Do not "tidy" these back into literals, and do not name either
    // class in a comment here: the first draft of this note did, and put the
    // rule straight back into the bundle.
    // `h-[32rem]` above stays spelled out: the view really does ship it
    // (AgentDetailView.vue:989).
    const joined = (...parts: string[]) => parts.join('')
    expect(html).not.toContain(joined('min-h-', '[32rem]'))
    expect(html).not.toContain(joined('lg:h-[calc(100', 'vh', '-8rem)]'))
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

  // R9.03a — per-model capability table. The mocked catalog (tests/mocks/handlers.ts)
  // mirrors the real backend table for the ids used below.
  it('disables the effort control for a model whose spec refuses it (AC-8)', async () => {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, model_hint: 'claude', model_id: 'claude-haiku-4-5' }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    const effortSelect = wrapper.find('#effort').element as HTMLSelectElement
    expect(effortSelect.disabled).toBe(true)
    expect(wrapper.text()).toContain('claude-haiku-4-5')
  })

  it('leaves the effort control enabled for a model whose spec accepts it', async () => {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, model_hint: 'claude', model_id: 'claude-sonnet-4-6' }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    const effortSelect = wrapper.find('#effort').element as HTMLSelectElement
    expect(effortSelect.disabled).toBe(false)
  })

  it('disables temperature and top_p for a model whose spec refuses sampling (AC-9)', async () => {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, model_hint: 'claude', model_id: 'claude-opus-4-8' }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    const temperature = wrapper.find('#temperature').element as HTMLInputElement
    const topP = wrapper.find('#top_p').element as HTMLInputElement
    expect(temperature.disabled).toBe(true)
    expect(topP.disabled).toBe(true)
    expect(wrapper.text()).toContain('claude-opus-4-8')
  })

  it('leaves sampling controls enabled for a model whose spec accepts them', async () => {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, model_hint: 'claude', model_id: 'claude-sonnet-4-6' }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    const temperature = wrapper.find('#temperature').element as HTMLInputElement
    const topP = wrapper.find('#top_p').element as HTMLInputElement
    expect(temperature.disabled).toBe(false)
    expect(topP.disabled).toBe(false)
  })

  // Seed used to render unconditionally enabled while no adapter forwarded it
  // anywhere, and the help named OpenAI -- the one provider whose endpoint has
  // no seed parameter. It is now gated on its own row flag.
  it('enables seed only for a model whose spec accepts it', async () => {
    seed()
    const cases: [string, string, boolean][] = [
      ['gemini', 'gemini-3.5-flash', false],
      ['claude', 'claude-sonnet-4-6', true],
      ['openai', 'gpt-5.4', true],
    ]
    for (const [hint, modelId, expectedDisabled] of cases) {
      server.use(
        http.get('/api/agents/agent_1', () =>
          HttpResponse.json({ ...AGENT, model_hint: hint, model_id: modelId }),
        ),
      )
      const wrapper = await renderView(AgentDetailView, {
        routes,
        initialRoute: '/agents/agent_1',
      })
      await settle(wrapper)
      expect((wrapper.find('#seed').element as HTMLInputElement).disabled, modelId).toBe(
        expectedDisabled,
      )
    }
  })

  it('disables seed for a custom model the catalog has no row for', async () => {
    // Q-2's conservative floor: an uncatalogued id gets no optional parameter,
    // and the control must say so rather than accept a value nothing will send.
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, model_hint: 'gemini', model_id: 'gemini-99-unlisted' }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    expect((wrapper.find('#seed').element as HTMLInputElement).disabled).toBe(true)
  })

  it('gates seed independently of temperature and top_p', async () => {
    // AC-7 at the UI end. claude-sonnet-4-6 accepts sampling and refuses seed;
    // gemini-3.5-flash accepts both. One flag governing both parameter families
    // is the defect, so each half below must disagree with the other control.
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, model_hint: 'claude', model_id: 'claude-sonnet-4-6' }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    expect((wrapper.find('#temperature').element as HTMLInputElement).disabled).toBe(false)
    expect((wrapper.find('#seed').element as HTMLInputElement).disabled).toBe(true)
  })

  it('preserves a legacy stored seed on edit-load even when the model refuses it', async () => {
    // Q-7: edit-load is not a user decision. Clearing here would silently
    // destroy a stored value just by opening the form.
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          model_hint: 'openai',
          model_id: 'gpt-5.4',
          seed: 123,
        }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    const field = wrapper.find('#seed').element as HTMLInputElement
    expect(field.disabled).toBe(true)
    expect(field.value).toBe('123')
  })

  it('clears seed when the user switches to a model that refuses it', async () => {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          model_hint: 'gemini',
          model_id: 'gemini-3.5-flash',
          seed: 123,
        }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)
    expect((wrapper.find('#seed').element as HTMLInputElement).value).toBe('123')

    // Another accepting row keeps it -- the clear is capability-driven, not a
    // blanket "any model change wipes the field".
    await wrapper.find('#model_id').setValue('gemini-2.5-flash')
    await settle(wrapper)
    expect((wrapper.find('#seed').element as HTMLInputElement).value).toBe('123')

    await wrapper.find('#model_hint').setValue('openai')
    await wrapper.find('#model_id').setValue('gpt-5.4')
    await settle(wrapper)
    expect((wrapper.find('#seed').element as HTMLInputElement).value).toBe('')
  })

  // AC-10: the context-token-cap bound follows the selected *model*, not the
  // provider -- claude-sonnet-4-6 (1M) and claude-haiku-4-5 (200k) are both
  // Claude, five-fold apart.
  it('bounds the context-token-cap placeholder by the selected model, not the provider', async () => {
    // This harness's shared i18n instance never loads message bundles (every
    // other assertion in this file checks for the raw key), but telling the
    // two models' bounds apart needs real `{cap}` interpolation -- merge the
    // real bundle for just this test and drop it again after, so no other
    // test in the file sees translated 'agents.form.*' text.
    i18n.global.mergeLocaleMessage('en', agentsEn as Record<string, unknown>)
    try {
      await runContextTokenCapPlaceholderCheck()
    } finally {
      i18n.global.setLocaleMessage('en', {})
    }
  })

  async function runContextTokenCapPlaceholderCheck(): Promise<void> {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          model_hint: 'claude',
          model_id: 'claude-sonnet-4-6',
          context_mode: 'compact',
        }),
      ),
    )
    const sonnetWrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(sonnetWrapper)
    const sonnetPlaceholder = (sonnetWrapper.find('#context_token_cap').element as HTMLInputElement)
      .placeholder

    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          model_hint: 'claude',
          model_id: 'claude-haiku-4-5',
          context_mode: 'compact',
        }),
      ),
    )
    const haikuWrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(haikuWrapper)
    const haikuPlaceholder = (haikuWrapper.find('#context_token_cap').element as HTMLInputElement)
      .placeholder

    expect(sonnetPlaceholder).not.toBe(haikuPlaceholder)
    // 75% of each model's own context_limit (1_000_000 vs 200_000).
    expect(sonnetPlaceholder).toContain('750,000')
    expect(haikuPlaceholder).toContain('150,000')
  }

  it('does not disable the effort control while the catalog is still loading', async () => {
    // NFR "Error handling UX": a slow catalog fetch must not lock a control.
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({ ...AGENT, model_hint: 'claude', model_id: 'claude-haiku-4-5' }),
      ),
      http.get('/api/model-catalog', async () => {
        await new Promise((r) => setTimeout(r, 5_000))
        return HttpResponse.json({ chat: [], embedding: [] })
      }),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    const effortSelect = wrapper.find('#effort').element as HTMLSelectElement
    expect(effortSelect.disabled).toBe(false)
  })

  // The UI-disable is not the only thing that must react to a mid-session
  // model switch: the field the disabled control is bound to must clear too,
  // or Save silently persists a value the model just stopped accepting.
  it('clears effort and sampling values a newly selected model does not accept', async () => {
    seed()
    server.use(
      http.get('/api/agents/agent_1', () =>
        HttpResponse.json({
          ...AGENT,
          model_hint: 'claude',
          model_id: 'claude-sonnet-4-6',
          effort: 'high',
          temperature: 0.5,
          top_p: 0.9,
        }),
      ),
    )
    const wrapper = await renderView(AgentDetailView, {
      routes,
      initialRoute: '/agents/agent_1',
    })
    await settle(wrapper)

    // Sonnet accepts both; the stored values render before any switch.
    expect((wrapper.find('#effort').element as HTMLSelectElement).value).toBe('high')
    expect((wrapper.find('#temperature').element as HTMLInputElement).value).toBe('0.5')

    // claude-opus-4-8 accepts effort but not sampling (per the mocked
    // catalog) -- switch to it and only the sampling fields should clear.
    await wrapper.find('#model_id').setValue('claude-opus-4-8')
    await settle(wrapper)
    expect((wrapper.find('#effort').element as HTMLSelectElement).value).toBe('high')
    expect((wrapper.find('#temperature').element as HTMLInputElement).value).toBe('')
    expect((wrapper.find('#top_p').element as HTMLInputElement).value).toBe('')

    // claude-haiku-4-5 refuses effort outright -- the select now clears too.
    await wrapper.find('#model_id').setValue('claude-haiku-4-5')
    await settle(wrapper)
    expect((wrapper.find('#effort').element as HTMLSelectElement).value).toBe('')
  })
})
