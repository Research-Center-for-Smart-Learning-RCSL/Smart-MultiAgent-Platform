import { describe, it, expect, beforeAll } from 'vitest'
import { i18n } from '@shared/i18n'
import { renderView } from '../../../../../../tests/utils'
import JoinConfigForm from '../JoinConfigForm.vue'
import { NODE_DEFAULTS } from '../../../constants'
import en from '../../../locales/en.json'
import zhTW from '../../../locales/zh-TW.json'

/**
 * F-36 (docs/tasks/2026-07-22-wait-for-event-timer-and-join-ports/spec.md): a join's
 * `timeout` port has never had a producer and Q-2 decided not to build one. The
 * config field that fed it is dead weight authors could set with no effect, so it
 * is removed here and replaced with a notice explaining the absence (C-3).
 */

type Bundle = Record<string, unknown>

const NOTICE_KEYS = ['joinTimeoutNotImplementedTitle', 'joinTimeoutNotImplementedBody'] as const

const locales: ReadonlyArray<readonly [string, Bundle]> = [
  ['en', en as Bundle],
  ['zh-TW', zhTW as Bundle],
]

function configBlock(bundle: Bundle): Record<string, unknown> {
  return (bundle as { workflow: { config: Record<string, unknown> } }).workflow.config
}

beforeAll(() => {
  i18n.global.mergeLocaleMessage('en', en as Bundle)
})

async function mountForm() {
  return renderView(JoinConfigForm, {
    props: {
      modelValue: { mode: 'all' },
      agents: [],
      chatrooms: [],
      allNodeIds: ['n1'],
    },
  })
}

describe('JoinConfigForm timeout removal', () => {
  it('renders no input bound to timeout_seconds', async () => {
    const wrapper = await mountForm()
    const timeoutInput = wrapper.find('#join-timeout')

    expect(timeoutInput.exists()).toBe(false)
  })

  it('renders a warning alert explaining the timeout is not implemented', async () => {
    const wrapper = await mountForm()
    const alert = wrapper.find('.s-alert')

    expect(alert.exists()).toBe(true)
    expect(alert.classes()).toContain('s-alert--warning')
  })

  it('resolves its copy through $t rather than a template literal', async () => {
    const wrapper = await mountForm()
    const text = wrapper.find('.s-alert').text()
    const block = configBlock(en as Bundle)

    expect(text).toContain(block.joinTimeoutNotImplementedTitle as string)
    expect(text).toContain(block.joinTimeoutNotImplementedBody as string)
    expect(text).not.toContain('workflow.config.joinTimeoutNotImplemented')
  })

  it.each(locales)('%s: defines every notice key', (_name, bundle) => {
    const block = configBlock(bundle)
    for (const key of NOTICE_KEYS) {
      expect(block[key]).toBeTruthy()
    }
  })

  it('NODE_DEFAULTS.join has no timeout_seconds key', () => {
    expect(NODE_DEFAULTS.join).not.toHaveProperty('timeout_seconds')
  })
})
