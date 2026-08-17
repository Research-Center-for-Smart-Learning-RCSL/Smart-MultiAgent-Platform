// AC-8 (grid placement + non-9-field fallback) and AC-9 (emit contract, failure
// leaves the grid mounted) for the mandala-9grid plugin. The component is mounted
// through InProcessBridge rather than directly, so the test also covers the
// island's lack of app context: a plugin that needed vue-i18n would throw here.

import { describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import { InProcessBridge } from '../sdk/bridge'
import type { ActivitySubmissionResult, JSONSchema } from '../sdk/types'
import { MANDALA_9GRID_KEY, mandala9GridPlugin } from '../plugins/mandala9grid'

const OK: ActivitySubmissionResult = {
  submissionId: 's1',
  status: 'validated',
  isValid: true,
  subScores: { filled: 2 },
}

function nineFieldSchema(): JSONSchema {
  const properties: Record<string, JSONSchema> = {
    center: { type: 'string', title: '中心主題' },
  }
  for (let i = 1; i <= 8; i += 1) {
    properties[`cell_${i}`] = { type: 'string', title: `格 ${i}` }
  }
  return { type: 'object', properties, required: ['center'] }
}

function mount(schema: JSONSchema, submit = vi.fn().mockResolvedValue(OK)) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const teardown = new InProcessBridge().mount(mandala9GridPlugin, {
    container,
    schema,
    session: { activityTypeKey: MANDALA_9GRID_KEY, sessionId: null },
    t: (key: string) => key,
    submit,
  })
  return { container, teardown, submit }
}

describe('mandala9GridPlugin', () => {
  it('is registered under the generic mandala-9grid key', () => {
    expect(mandala9GridPlugin.manifest.key).toBe('mandala-9grid')
    expect(mandala9GridPlugin.manifest.version).toBeTruthy()
    expect(mandala9GridPlugin.manifest.title).toBeTruthy()
  })

  it('places center in the middle cell and the ring in declaration order (AC-8)', () => {
    const { container, teardown } = mount(nineFieldSchema())

    const cells = container.querySelectorAll('[data-testid="mandala-grid"] > div')
    expect(cells).toHaveLength(9)
    expect(cells[4]?.getAttribute('data-testid')).toBe('mandala-cell-center')
    expect(cells[4]?.querySelector('textarea')?.id).toBe('mandala-center')

    const ids = Array.from(cells).map((c) => c.querySelector('textarea')?.id)
    expect(ids).toEqual([
      'mandala-cell_1',
      'mandala-cell_2',
      'mandala-cell_3',
      'mandala-cell_4',
      'mandala-center',
      'mandala-cell_5',
      'mandala-cell_6',
      'mandala-cell_7',
      'mandala-cell_8',
    ])
    teardown()
  })

  it('reproduces the 時空旅人 worksheet from a jsonb-ordered schema (AC-1)', () => {
    // Properties are written here in the order PostgreSQL actually returns them
    // (by length, then bytewise), not the order the course file declares. The
    // worksheet layout must come back regardless, which is what x-order buys.
    const { container, teardown } = mount({
      type: 'object',
      properties: {
        free: { type: 'string', title: '自由發揮', 'x-order': 8 },
        home: { type: 'string', title: '家', 'x-order': 1 },
        work: { type: 'string', title: '工作', 'x-order': 2 },
        center: { type: 'string', title: '30 歲的我會有什麼改變呢？', 'x-order': 5 },
        leisure: { type: 'string', title: '休閒娛樂', 'x-order': 6 },
        abilities: { type: 'string', title: '具備能力', 'x-order': 3 },
        appearance: { type: 'string', title: '外貌', 'x-order': 4 },
        relationships: { type: 'string', title: '人際關係', 'x-order': 9 },
        message_to_self: { type: 'string', title: '想對 30 歲的自己說…', 'x-order': 7 },
      },
      required: [],
    })

    const cells = container.querySelectorAll('[data-testid="mandala-grid"] > div')
    const ids = Array.from(cells).map((c) => c.querySelector('textarea')?.id)
    expect(ids).toEqual([
      'mandala-home',
      'mandala-work',
      'mandala-abilities',
      'mandala-appearance',
      'mandala-center',
      'mandala-leisure',
      'mandala-message_to_self',
      'mandala-free',
      'mandala-relationships',
    ])
    expect(cells[4]?.getAttribute('data-testid')).toBe('mandala-cell-center')
    teardown()
  })

  it('renders declared order when no property is named center (R30.36)', () => {
    // This scenario used to assert the opposite: the plugin promoted the first
    // property to the centre, citing AC-8 of
    // `docs/tasks/2026-08-08-creative-thinking-course-example/spec.md:571-572`.
    // That AC specifies the center-present and not-nine-fields cases and says
    // nothing about this one, so the promotion was an implementation choice
    // rather than a mandate. Triaged 2026-08-16 in [R30.36]'s favour: `center`
    // is a named opt-in, and a schema that declares none renders as declared.
    const properties: Record<string, JSONSchema> = {}
    for (let i = 1; i <= 9; i += 1) properties[`f${i}`] = { type: 'string' }
    const { container, teardown } = mount({ type: 'object', properties })

    const cells = container.querySelectorAll('[data-testid="mandala-grid"] > div')
    expect(cells).toHaveLength(9)
    const ids = Array.from(cells).map((c) => c.querySelector('textarea')?.id)
    expect(ids).toEqual([
      'mandala-f1',
      'mandala-f2',
      'mandala-f3',
      'mandala-f4',
      'mandala-f5',
      'mandala-f6',
      'mandala-f7',
      'mandala-f8',
      'mandala-f9',
    ])
    // No cell claims the centre styling when the schema named no centre.
    expect(container.querySelector('[data-testid="mandala-cell-center"]')).toBeNull()
    teardown()
  })

  it('honours x-order over object key order when no property is named center (R30.36)', () => {
    // Key order here is deliberately the reverse of x-order, so passing cannot
    // be an accident of how the object literal happens to enumerate — which is
    // the property that ties this renderer to R30.36 rather than to jsonb's
    // storage order.
    const properties: Record<string, JSONSchema> = {}
    for (let i = 9; i >= 1; i -= 1) properties[`f${i}`] = { type: 'string', 'x-order': i }
    const { container, teardown } = mount({ type: 'object', properties })

    const cells = container.querySelectorAll('[data-testid="mandala-grid"] > div')
    const ids = Array.from(cells).map((c) => c.querySelector('textarea')?.id)
    expect(ids).toEqual([
      'mandala-f1',
      'mandala-f2',
      'mandala-f3',
      'mandala-f4',
      'mandala-f5',
      'mandala-f6',
      'mandala-f7',
      'mandala-f8',
      'mandala-f9',
    ])
    teardown()
  })

  it('falls back to a single column when the schema is not nine fields (AC-8)', () => {
    const { container, teardown } = mount({
      type: 'object',
      properties: { a: { type: 'string' }, b: { type: 'string' } },
    })

    expect(container.querySelector('[data-testid="mandala-grid"]')).toBeNull()
    expect(container.querySelector('[data-testid="mandala-list"]')).not.toBeNull()
    expect(container.querySelectorAll('textarea')).toHaveLength(2)
    teardown()
  })

  it('emits the assembled payload with no score field (AC-9)', async () => {
    const submit = vi.fn().mockResolvedValue(OK)
    const { container, teardown } = mount(nineFieldSchema(), submit)

    const center = container.querySelector<HTMLTextAreaElement>('[data-testid="mandala-input-center"]')!
    center.value = '30 歲的我'
    center.dispatchEvent(new Event('input'))
    const cell1 = container.querySelector<HTMLTextAreaElement>('[data-testid="mandala-input-cell_1"]')!
    cell1.value = '住在海邊'
    cell1.dispatchEvent(new Event('input'))
    await flushPromises()

    container.querySelector<HTMLButtonElement>('[data-testid="mandala-submit"]')!.click()
    await flushPromises()

    expect(submit).toHaveBeenCalledTimes(1)
    const payload = submit.mock.calls[0]![0] as Record<string, unknown>
    expect(payload.center).toBe('30 歲的我')
    expect(payload.cell_1).toBe('住在海邊')
    // Empty optional cells are omitted, and nothing score-shaped is ever sent.
    expect(payload).not.toHaveProperty('cell_2')
    expect(payload).not.toHaveProperty('is_valid')
    expect(payload).not.toHaveProperty('sub_scores')
    teardown()
  })

  it('renders a boolean property as a checkbox and submits a real boolean (AC-9)', async () => {
    // A textarea cannot express a boolean: typed text through assemblePayload's
    // boolean branch submits `false` for anything, and narrowing the schema to
    // strings instead sends "yes" for a declared boolean, which the server rejects
    // with a 422 the participant cannot act on. A checkbox avoids both.
    const submit = vi.fn().mockResolvedValue(OK)
    const { container, teardown } = mount(
      {
        type: 'object',
        properties: { note: { type: 'string' }, agreed: { type: 'boolean' } },
        required: ['note'],
      },
      submit,
    )

    const agreed = container.querySelector<HTMLInputElement>('[data-testid="mandala-input-agreed"]')!
    expect(agreed.tagName).toBe('INPUT')
    expect(agreed.type).toBe('checkbox')

    const note = container.querySelector<HTMLTextAreaElement>('[data-testid="mandala-input-note"]')!
    note.value = 'hello'
    note.dispatchEvent(new Event('input'))
    agreed.checked = true
    agreed.dispatchEvent(new Event('change'))
    await flushPromises()

    container.querySelector<HTMLButtonElement>('[data-testid="mandala-submit"]')!.click()
    await flushPromises()

    expect(submit).toHaveBeenCalledTimes(1)
    const payload = submit.mock.calls[0]![0] as Record<string, unknown>
    expect(payload.agreed).toBe(true)
    teardown()
  })

  it('submits a number property as a number, not as the typed text (AC-9)', async () => {
    const submit = vi.fn().mockResolvedValue(OK)
    const { container, teardown } = mount(
      { type: 'object', properties: { count: { type: 'number' } } },
      submit,
    )

    const count = container.querySelector<HTMLTextAreaElement>('[data-testid="mandala-input-count"]')!
    count.value = '5'
    count.dispatchEvent(new Event('input'))
    await flushPromises()

    container.querySelector<HTMLButtonElement>('[data-testid="mandala-submit"]')!.click()
    await flushPromises()

    const payload = submit.mock.calls[0]![0] as Record<string, unknown>
    // "5" would be schema-invalid and earn an unfixable server 422.
    expect(payload.count).toBe(5)
    teardown()
  })

  it("renders each cell's description, which carries the author's instruction", async () => {
    const { container, teardown } = mount({
      type: 'object',
      properties: {
        center: {
          type: 'string',
          title: '中心主題',
          description: '用一句話寫下你想像中 30 歲的自己。',
        },
      },
    })

    expect(container.textContent).toContain('用一句話寫下你想像中 30 歲的自己。')
    const input = container.querySelector('[data-testid="mandala-input-center"]')!
    expect(input.getAttribute('aria-describedby')).toBe('mandala-help-center')
    teardown()
  })

  it('blocks submit and flags the field when a required cell is blank (AC-9)', async () => {
    const submit = vi.fn().mockResolvedValue(OK)
    const { container, teardown } = mount(nineFieldSchema(), submit)

    container.querySelector<HTMLButtonElement>('[data-testid="mandala-submit"]')!.click()
    await flushPromises()

    expect(submit).not.toHaveBeenCalled()
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    teardown()
  })

  it('keeps the grid mounted with answers intact when submit rejects (AC-9)', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('boom'))
    const { container, teardown } = mount(nineFieldSchema(), submit)

    const center = container.querySelector<HTMLTextAreaElement>('[data-testid="mandala-input-center"]')!
    center.value = 'keep me'
    center.dispatchEvent(new Event('input'))
    await flushPromises()

    container.querySelector<HTMLButtonElement>('[data-testid="mandala-submit"]')!.click()
    await flushPromises()

    expect(submit).toHaveBeenCalledTimes(1)
    expect(container.querySelector('[data-testid="mandala-grid"]')).not.toBeNull()
    const after = container.querySelector<HTMLTextAreaElement>('[data-testid="mandala-input-center"]')!
    expect(after.value).toBe('keep me')
    expect(after.disabled).toBe(false)
    teardown()
  })

  it('unmounts cleanly via the returned teardown', () => {
    const { container, teardown } = mount(nineFieldSchema())
    expect(container.querySelector('[data-testid="mandala-grid"]')).not.toBeNull()
    teardown()
    expect(container.querySelector('[data-testid="mandala-grid"]')).toBeNull()
  })
})
