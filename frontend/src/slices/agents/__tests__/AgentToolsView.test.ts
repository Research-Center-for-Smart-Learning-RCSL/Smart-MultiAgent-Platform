import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AgentToolsView from '../views/AgentToolsView.vue'

const routes = [{ path: '/agents/:agentId/tools', name: 'agents.tools', component: AgentToolsView }]

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

const MCP_TOOL = {
  id: 'tool_1',
  agent_id: 'agent_1',
  tool_type: 'hosted_mcp',
  enabled: true,
  display_name: 'Weather',
  config: { source: 'url', reference: 'https://mcp.example.com/sse', allowed_tools: [] },
  config_warnings: [],
  created_at: '2026-01-01T00:00:00Z',
}

function seed(tools: unknown[] = []): void {
  server.use(
    http.get('/api/agents/agent_1', () => HttpResponse.json(AGENT)),
    http.get('/api/agents/agent_1/tools', () => HttpResponse.json(tools)),
  )
}

async function settle(): Promise<void> {
  await new Promise((r) => setTimeout(r, 50))
}

async function waitForCodeMirror(getHost: () => { exists(): boolean }): Promise<void> {
  await vi.waitFor(() => {
    if (!getHost().exists()) throw new Error('CodeMirror not mounted yet')
  })
}

// Locale bundles aren't registered in this test environment (matching the
// rest of the agents slice's test suite, e.g. AgentDetailView.test.ts) — $t()
// renders the raw key, so assertions match those keys rather than display
// copy.
async function clickByText(wrapper: Awaited<ReturnType<typeof renderView>>, text: string): Promise<void> {
  const button = wrapper.findAll('button').find((b) => b.text().includes(text))
  expect(button, `no button containing "${text}"`).toBeTruthy()
  await button!.trigger('click')
  await wrapper.vm.$nextTick()
}

describe('AgentToolsView — JSON field syntax highlighting (AC-1/AC-2/AC-3/AC-5)', () => {
  it('renders CodeMirror for the MCP config JSON field and surfaces a positioned lint diagnostic', async () => {
    seed([])
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    await clickByText(wrapper, 'agents.tools.mcp.add')

    // AC-1: the JSON field renders CodeMirror, not the plain textarea.
    await waitForCodeMirror(() => wrapper.find('.cm-content'))
    expect(wrapper.find('.cm-content').exists()).toBe(true)

    // AC-2: malformed JSON gets a live, positioned diagnostic without submitting.
    const { EditorView } = await import('@codemirror/view')
    const host = wrapper.find('.code-editor-cm-host').element as HTMLElement
    const view = EditorView.findFromDOM(host)
    expect(view).not.toBeNull()
    view!.dispatch({ changes: { from: 0, to: view!.state.doc.length, insert: '{"a":}' } })
    await vi.waitFor(() => {
      if (!wrapper.find('.cm-lint-marker').exists()) throw new Error('lint marker not rendered yet')
    })
    expect(wrapper.find('.cm-lint-marker').exists()).toBe(true)
    // Submit-time validation (AC-3) was not triggered by typing alone.
    expect(wrapper.text()).not.toContain('agents.tools.mcp.invalidJson')
  })

  it('keeps the existing submit-time invalidJson validation for the MCP config field', async () => {
    seed([])
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    await clickByText(wrapper, 'agents.tools.mcp.add')
    await waitForCodeMirror(() => wrapper.find('.cm-content'))

    const inputs = wrapper.findAll('input.s-input__field')
    expect(inputs.length).toBeGreaterThan(0)
    await inputs[0]!.setValue('https://mcp.example.com/sse')
    await wrapper.vm.$nextTick()

    const { EditorView } = await import('@codemirror/view')
    const host = wrapper.find('.code-editor-cm-host').element as HTMLElement
    const view = EditorView.findFromDOM(host)!
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: '{not valid json' } })
    await wrapper.vm.$nextTick()

    await wrapper.find('form').trigger('submit')

    // AC-3: submit-time validation still fires, with its existing i18n key,
    // unchanged by the linter's presence. vee-validate's handleSubmit resolves
    // asynchronously, so poll rather than assert on the very next tick.
    await vi.waitFor(() => {
      if (!wrapper.text().includes('agents.tools.mcp.invalidJson')) throw new Error('validation error not rendered yet')
    })
    expect(wrapper.text()).toContain('agents.tools.mcp.invalidJson')
  })

  it('renders CodeMirror for the function parameters JSON field', async () => {
    seed([])
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    await clickByText(wrapper, 'agents.tools.functions.add')

    await waitForCodeMirror(() => wrapper.find('.cm-content'))
    expect(wrapper.find('.cm-content').exists()).toBe(true)
  })

  it('keeps the existing submit-time invalidParamsJson validation for the function parameters field', async () => {
    seed([])
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    await clickByText(wrapper, 'agents.tools.functions.add')
    await waitForCodeMirror(() => wrapper.find('.cm-content'))

    const inputs = wrapper.findAll('input.s-input__field')
    // Function form field order: name, then http.url (method is an SSelect).
    await inputs[0]!.setValue('lookup_order')
    await wrapper.find('textarea.s-textarea').setValue('Looks up an order.')
    await inputs[1]!.setValue('https://api.example.com/orders')
    await wrapper.vm.$nextTick()

    const { EditorView } = await import('@codemirror/view')
    const host = wrapper.find('.code-editor-cm-host').element as HTMLElement
    const view = EditorView.findFromDOM(host)!
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'not json at all' } })
    await wrapper.vm.$nextTick()

    await wrapper.find('form').trigger('submit')

    // AC-3: the distinct parse-vs-schema checks both still fire on their
    // existing conditions with their existing i18n keys.
    await vi.waitFor(() => {
      if (!wrapper.text().includes('agents.tools.functions.invalidParamsJson')) throw new Error('validation error not rendered yet')
    })
    expect(wrapper.text()).toContain('agents.tools.functions.invalidParamsJson')
  })

  // Both of these used to leave the dialog open with nothing visible: the JSON
  // error rendered inside an SAccordion panel that is collapsed by default (and
  // hidden with CSS, so it is in the DOM but off-screen), and a server field
  // error whose path has no SFormField was set invisibly while useServerErrors
  // suppressed the toast.
  it('surfaces the invalidJson error outside the collapsed advanced-config panel', async () => {
    seed([])
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    await clickByText(wrapper, 'agents.tools.mcp.add')
    await waitForCodeMirror(() => wrapper.find('.cm-content'))

    await wrapper.findAll('input.s-input__field')[0]!.setValue('https://mcp.example.com/sse')
    await wrapper.vm.$nextTick()

    const { EditorView } = await import('@codemirror/view')
    const host = wrapper.find('.code-editor-cm-host').element as HTMLElement
    const view = EditorView.findFromDOM(host)!
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: '{not valid json' } })
    await wrapper.vm.$nextTick()

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => {
      if (!wrapper.find('.s-alert').exists()) throw new Error('form-level alert not rendered yet')
    })
    expect(wrapper.find('.s-alert').text()).toContain('agents.tools.mcp.invalidJson')
    // The accordion panel must no longer be the only place it appears.
    expect(wrapper.find('.s-accordion__panel').text()).not.toContain('agents.tools.mcp.invalidJson')
  })

  it('surfaces a server field error whose path has no form field of its own', async () => {
    seed([])
    server.use(
      http.post('/api/agents/agent_1/tools', () =>
        HttpResponse.json(
          {
            type: 'https://smap.local/problems/validation',
            title: 'Validation Error',
            status: 422,
            detail: 'Invalid tool config',
            field_errors: [
              { location: 'body', path: 'config.allowed_tools', message: 'Unknown tool name.' },
            ],
          },
          { status: 422, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    )
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    await clickByText(wrapper, 'agents.tools.mcp.add')
    await waitForCodeMirror(() => wrapper.find('.cm-content'))

    await wrapper.findAll('input.s-input__field')[0]!.setValue('https://mcp.example.com/sse')
    await wrapper.find('textarea.s-textarea').setValue('read_file')
    await wrapper.vm.$nextTick()

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => {
      if (!wrapper.find('.s-alert').exists()) throw new Error('server error not surfaced yet')
    })
    expect(wrapper.find('.s-alert').text()).toContain('Unknown tool name.')
  })

  it('leaves the readonly MCP test-failure field on the plain textarea (unchanged, AC-5)', async () => {
    seed([MCP_TOOL])
    server.use(
      http.post('/api/agents/agent_1/tools/tool_1/test', () =>
        HttpResponse.json({ ok: false, tool_names: [], duration_ms: 12, error: 'connection refused' }),
      ),
    )
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    await clickByText(wrapper, 'agents.tools.mcp.test')
    await settle()
    await wrapper.vm.$nextTick()

    // language="text" never mounts CodeMirror (Q-4), so the failure detail
    // stays on the plain textarea and is read from its value, not text
    // content.
    const textarea = wrapper.find('textarea.code-editor')
    expect(textarea.exists()).toBe(true)
    expect((textarea.element as HTMLTextAreaElement).value).toBe('connection refused')
    expect(textarea.attributes('readonly')).toBeDefined()
    expect(wrapper.find('.cm-content').exists()).toBe(false)
  })

  // 2026-07-22-mcp-tool-contract: capture-staleness advisories (Q-7) ride the
  // same config_warnings channel the function-tools list already renders --
  // the MCP table had no equivalent indicator at all before this fix.
  it('renders a warning indicator for an MCP tool carrying config_warnings', async () => {
    seed([
      {
        ...MCP_TOOL,
        config_warnings: [
          "tool 'beta' was not found in the last capture; it may no longer be offered by the server. Re-run Test to confirm.",
        ],
      },
    ])
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    expect(wrapper.find('[title*="was not found in the last capture"]').exists()).toBe(true)
  })

  it('renders no warning indicator for an MCP tool with an empty config_warnings', async () => {
    seed([MCP_TOOL])
    const wrapper = await renderView(AgentToolsView, { routes, initialRoute: '/agents/agent_1/tools' })
    await settle()

    expect(wrapper.find('[title*="was not found in the last capture"]').exists()).toBe(false)
  })
})
