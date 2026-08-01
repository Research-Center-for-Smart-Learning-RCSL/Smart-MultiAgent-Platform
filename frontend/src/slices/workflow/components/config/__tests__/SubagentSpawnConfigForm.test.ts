import { describe, it, expect, beforeAll } from 'vitest'
import { i18n } from '@shared/i18n'
import { renderView } from '../../../../../../tests/utils'
import SubagentSpawnConfigForm from '../SubagentSpawnConfigForm.vue'
import en from '../../../locales/en.json'
import zhTW from '../../../locales/zh-TW.json'

/**
 * AC-7 — the editor must say the node is unavailable, in both locales, with no
 * hardcoded strings.
 *
 * The node fails immediately on its `failure` port because sub-agent execution was
 * never built (2026-07-22-subagent-spawn-fail-fast). Nothing in the editor said so:
 * the form rendered beside working node types with no qualification at all.
 */

type Bundle = Record<string, unknown>

const CONFIG_KEYS = ['subagentUnavailableTitle', 'subagentUnavailableBody'] as const

const locales: ReadonlyArray<readonly [string, Bundle]> = [
  ['en', en as Bundle],
  ['zh-TW', zhTW as Bundle],
]

function configBlock(bundle: Bundle): Record<string, unknown> {
  return (bundle as { workflow: { config: Record<string, unknown> } }).workflow.config
}

function paletteBlock(bundle: Bundle): Record<string, unknown> {
  return (bundle as { workflow: { palette: Record<string, unknown> } }).workflow.palette
}

beforeAll(() => {
  // Slice bundles are lazy-loaded in the app; tests must merge them explicitly or
  // vue-i18n's `missing` handler resolves every key to the key string.
  i18n.global.mergeLocaleMessage('en', en as Bundle)
})

async function mountForm() {
  return renderView(SubagentSpawnConfigForm, {
    props: {
      modelValue: { parent_agent_id: '', task_template: '' },
      agents: [],
      chatrooms: [],
      allNodeIds: ['n1'],
    },
  })
}

describe('SubagentSpawnConfigForm unavailability notice', () => {
  it('renders a warning alert at the top of the form', async () => {
    const wrapper = await mountForm()
    const alert = wrapper.find('.s-alert')

    expect(alert.exists()).toBe(true)
    expect(alert.classes()).toContain('s-alert--warning')
  })

  it('resolves its copy through $t rather than a template literal', async () => {
    const wrapper = await mountForm()
    const text = wrapper.find('.s-alert').text()
    const block = configBlock(en as Bundle)

    expect(text).toContain(block.subagentUnavailableTitle as string)
    expect(text).toContain(block.subagentUnavailableBody as string)
    // A hardcoded literal would still "contain" the copy; an unresolved key would
    // not. This is what distinguishes $t() from a literal in the template.
    expect(text).not.toContain('workflow.config.subagentUnavailable')
  })

  it('says the capability is deferred, not cancelled (R6)', async () => {
    const wrapper = await mountForm()
    expect(wrapper.find('.s-alert').text()).toMatch(/deferred, not cancelled/i)
  })

  it.each(locales)('%s: defines every unavailability key', (_name, bundle) => {
    const block = configBlock(bundle)
    for (const key of CONFIG_KEYS) {
      expect(block[key]).toBeTruthy()
    }
    expect(paletteBlock(bundle).unavailable).toBeTruthy()
  })
})
