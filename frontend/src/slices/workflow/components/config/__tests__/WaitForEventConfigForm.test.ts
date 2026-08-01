import { describe, it, expect, beforeAll } from 'vitest'
import { i18n } from '@shared/i18n'
import { renderView } from '../../../../../../tests/utils'
import WaitForEventConfigForm from '../WaitForEventConfigForm.vue'
import en from '../../../locales/en.json'
import zhTW from '../../../locales/zh-TW.json'

/**
 * F-2 (docs/tasks/2026-07-22-wait-for-event-timer-and-join-ports/spec.md, found in the
 * task's ultrareview): `timeout_seconds` never governed a timer wait (C-1 made that fact
 * true at the executor level), but the form still rendered the field unconditionally for
 * every event type, including timer, with nothing telling an author it does nothing there.
 */

type Bundle = Record<string, unknown>

const NOTICE_KEYS = ['timerTimeoutNotApplicableTitle', 'timerTimeoutNotApplicableBody'] as const

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

async function mountForm(eventType: string) {
  return renderView(WaitForEventConfigForm, {
    props: {
      modelValue: { event_type: eventType, timeout_seconds: 300 },
      agents: [],
      chatrooms: [],
      allNodeIds: ['n1'],
    },
  })
}

describe('WaitForEventConfigForm timer timeout notice', () => {
  it('hides the Timeout field when event_type is timer', async () => {
    const wrapper = await mountForm('timer')

    expect(wrapper.find('#wait-timeout').exists()).toBe(false)
  })

  it('shows the Timeout field for every non-timer event type', async () => {
    const wrapper = await mountForm('message_in_room')

    expect(wrapper.find('#wait-timeout').exists()).toBe(true)
  })

  it('renders an info alert explaining timeout does not apply, for timer only', async () => {
    const timerWrapper = await mountForm('timer')
    const alert = timerWrapper.find('.s-alert')

    expect(alert.exists()).toBe(true)
    expect(alert.classes()).toContain('s-alert--info')

    const messageWrapper = await mountForm('message_in_room')
    expect(messageWrapper.find('.s-alert').exists()).toBe(false)
  })

  it('resolves its copy through $t rather than a template literal', async () => {
    const wrapper = await mountForm('timer')
    const text = wrapper.find('.s-alert').text()
    const block = configBlock(en as Bundle)

    expect(text).toContain(block.timerTimeoutNotApplicableTitle as string)
    expect(text).toContain(block.timerTimeoutNotApplicableBody as string)
    expect(text).not.toContain('workflow.config.timerTimeoutNotApplicable')
  })

  it.each(locales)('%s: defines every notice key', (_name, bundle) => {
    const block = configBlock(bundle)
    for (const key of NOTICE_KEYS) {
      expect(block[key]).toBeTruthy()
    }
  })
})
