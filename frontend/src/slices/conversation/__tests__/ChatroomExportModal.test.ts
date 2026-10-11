import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'

import { i18n } from '@shared/i18n'
import en from '../locales/en.json'
import ChatroomExportModal from '../components/ChatroomExportModal.vue'
import type { ExportPhase } from '../composables/useChatroomExport'

beforeAll(() => {
  i18n.global.mergeLocaleMessage('en', en as Record<string, unknown>)
})

function mountModal(phase: ExportPhase) {
  return mount(ChatroomExportModal, {
    props: { open: true, phase },
    global: { plugins: [i18n], stubs: { teleport: true } },
    attachTo: document.body,
  })
}

const submitButton = (w: ReturnType<typeof mountModal>) => w.get('[data-testid="submit-export"]')

async function chooseCustomRange(w: ReturnType<typeof mountModal>, start: string, end: string) {
  await w.get('select').setValue('custom')
  const [startInput, endInput] = w.findAll('input[type="date"]')
  await startInput!.setValue(start)
  await endInput!.setValue(end)
}

describe('ChatroomExportModal', () => {
  it('cannot submit a custom range whose end precedes its start, and says why', async () => {
    const w = mountModal({ kind: 'idle' })
    await chooseCustomRange(w, '2026-10-11', '2026-10-01')

    expect(submitButton(w).attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('The end date cannot be before the start date.')
    await submitButton(w).trigger('click')
    expect(w.emitted('submit')).toBeUndefined()

    await w.findAll('input[type="date"]')[1]!.setValue('2026-10-12')
    expect(submitButton(w).attributes('disabled')).toBeUndefined()
    await submitButton(w).trigger('click')
    expect(w.emitted('submit')?.[0]).toEqual([
      { format: 'markdown', date_range: 'custom', start: '2026-10-11', end: '2026-10-12' },
    ])
    w.unmount()
  })

  it('disables submit while a create is pending', () => {
    const w = mountModal({ kind: 'starting' })
    expect(submitButton(w).attributes('disabled')).toBeDefined()
    w.unmount()
  })

  it('keeps the form open with its values on a server 422 that names no field, without locking submit', async () => {
    const w = mountModal({ kind: 'idle' })
    await chooseCustomRange(w, '2026-10-01', '2026-10-05')
    await w.setProps({ phase: { kind: 'starting' } })
    await w.setProps({ phase: { kind: 'invalid', field: null } })

    const dates = w.findAll('input[type="date"]')
    expect((dates[0]!.element as HTMLInputElement).value).toBe('2026-10-01')
    expect((dates[1]!.element as HTMLInputElement).value).toBe('2026-10-05')
    expect(w.text()).toContain('Check the export options and try again.')
    // Nothing the user can edit was named, so locking would leave no way forward.
    expect(submitButton(w).attributes('disabled')).toBeUndefined()

    await dates[1]!.setValue('2026-10-06')
    expect(w.text()).not.toContain('Check the export options and try again.')
    w.unmount()
  })

  it('marks the date field a 422 names', async () => {
    const w = mountModal({ kind: 'idle' })
    await chooseCustomRange(w, '2026-10-01', '2026-10-05')
    await w.setProps({ phase: { kind: 'invalid', field: 'end' } })
    const end = w.findAll('input[type="date"]')[1]!
    expect(end.attributes('aria-invalid')).toBe('true')
    expect(submitButton(w).attributes('disabled')).toBeDefined()
    await end.setValue('2026-10-06')
    expect(submitButton(w).attributes('disabled')).toBeUndefined()
    w.unmount()
  })

  it.each<[ExportPhase, string | string[], string[]]>([
    [{ kind: 'running', jobId: 'A' }, 'Exporting chatroom history', []],
    [
      { kind: 'stalled', jobId: 'A', reason: 'slow' },
      'Still processing. Large rooms can take several minutes.',
      ['export-check-again'],
    ],
    [
      { kind: 'stalled', jobId: 'A', reason: 'unreachable' },
      'Could not reach the server to check this export.',
      ['export-check-again'],
    ],
    [{ kind: 'ready', jobId: 'A' }, 'Export ready!', ['export-download']],
    [
      { kind: 'start-failed', opts: { format: 'json' } },
      'Could not start the export.',
      ['export-cancel', 'export-retry'],
    ],
    [
      { kind: 'job-failed' },
      ['Export failed.', 'Unable to generate the archive.'],
      ['export-cancel', 'export-retry'],
    ],
  ])('renders %o with its copy and actions', (phase, copy, actions) => {
    const w = mountModal(phase)
    for (const line of [copy].flat()) expect(w.text()).toContain(line)
    for (const id of actions) expect(w.find(`[data-testid="${id}"]`).exists()).toBe(true)
    expect(w.find('[data-testid="submit-export"]').exists()).toBe(false)
    w.unmount()
  })

  it('emits the intent for each action', async () => {
    const w = mountModal({ kind: 'stalled', jobId: 'A', reason: 'slow' })
    await w.get('[data-testid="export-check-again"]').trigger('click')
    expect(w.emitted('checkAgain')).toHaveLength(1)

    await w.setProps({ phase: { kind: 'job-failed' } })
    await w.get('[data-testid="export-retry"]').trigger('click')
    expect(w.emitted('retry')).toHaveLength(1)
    await w.get('[data-testid="export-cancel"]').trigger('click')
    expect(w.emitted('close')).toBeTruthy()
    w.unmount()
  })

  it('Retry from a job failure shows the form again with the previous values', async () => {
    const w = mountModal({ kind: 'idle' })
    await w.get('#export-fmt-pdf').setValue(true)
    await chooseCustomRange(w, '2026-09-01', '2026-09-30')
    await w.setProps({ phase: { kind: 'running', jobId: 'A' } })
    await w.setProps({ phase: { kind: 'job-failed' } })
    // The composable's retry() for a job failure returns to idle.
    await w.setProps({ phase: { kind: 'idle' } })

    expect((w.get('#export-fmt-pdf').element as HTMLInputElement).checked).toBe(true)
    const dates = w.findAll('input[type="date"]')
    expect((dates[0]!.element as HTMLInputElement).value).toBe('2026-09-01')
    expect((dates[1]!.element as HTMLInputElement).value).toBe('2026-09-30')
    w.unmount()
  })
})
