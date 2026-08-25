// Presentation blocks in the creator's panel ([R28.15]-[R28.19]) — AC-8, AC-9, AC-13.
//
// The test i18n harness echoes keys (bundles load lazily), so a rendered
// `conversation.observers.basis.transcript` IS the assertion that the sentence
// came from the platform's catalogue rather than from the agent.
import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObservationBlocks from '../components/observation-blocks/ObservationBlocks.vue'
import ObsAttemptTableBlock from '../components/observation-blocks/ObsAttemptTableBlock.vue'
import ObsFieldCoverageBlock from '../components/observation-blocks/ObsFieldCoverageBlock.vue'
import ObsKeyPointsBlock from '../components/observation-blocks/ObsKeyPointsBlock.vue'
import ObsMandalaGridBlock from '../components/observation-blocks/ObsMandalaGridBlock.vue'
import ObsTimelineBlock from '../components/observation-blocks/ObsTimelineBlock.vue'
import type {
  ObservationAttemptTableBlock,
  ObservationBlock,
  ObservationFieldCoverageBlock,
  ObservationKeyPointsBlock,
  ObservationMandalaGridBlock,
  ObservationTimelineBlock,
} from '../types'

const CELLS = [
  { name: 'home', title: 'Home', filled: 9 },
  { name: 'work', title: 'Work', filled: 3 },
]

function coverage(over: Partial<ObservationFieldCoverageBlock> = {}): ObservationFieldCoverageBlock {
  return {
    kind: 'field_coverage',
    basis: 'server_facts',
    type_key: 'mandala-9grid',
    type_name: 'Unit 2',
    submissions_counted: 12,
    cells: CELLS,
    ...over,
  }
}

function grid(): ObservationMandalaGridBlock {
  const cells = Array.from({ length: 9 }, (_, i) => ({
    name: `c${i}`,
    title: `Cell ${i}`,
    filled: i,
  }))
  return {
    kind: 'mandala_grid',
    basis: 'server_facts',
    submissions_counted: 4,
    rows: [cells.slice(0, 3), cells.slice(3, 6), cells.slice(6, 9)],
  }
}

function table(over: Partial<ObservationAttemptTableBlock> = {}): ObservationAttemptTableBlock {
  return {
    kind: 'attempt_table',
    basis: 'server_facts',
    submissions_counted: 40,
    truncated: false,
    rows: [
      {
        subject_code: 'u:1a2b3c4d',
        attempts: 3,
        submissions: 5,
        latest_outcome: 'invalid',
        latest_error_class: 'too_few_filled',
      },
    ],
    ...over,
  }
}

const points: ObservationKeyPointsBlock = {
  kind: 'key_points',
  title: 'Three things',
  basis: 'transcript',
  caveat: 'only what was said out loud',
  points: [{ text: 'the room split three ways', evidence: 'u:1a2b3c4d and two others' }],
  next_step: 'ask the quiet half',
}

const timeline: ObservationTimelineBlock = {
  kind: 'timeline',
  basis: 'recent_window',
  entries: [{ label: '10:05', detail: 'round opened' }],
}

describe('narrative blocks', () => {
  it('renders points, their evidence and the next step as text', async () => {
    const wrapper = await renderView(ObsKeyPointsBlock, { props: { block: points } })
    expect(wrapper.text()).toContain('the room split three ways')
    expect(wrapper.text()).toContain('u:1a2b3c4d and two others')
    expect(wrapper.text()).toContain('ask the quiet half')
  })

  it('never renders agent text as markup', async () => {
    // AC-14's runtime half: every string here is model-authored and the model
    // reads participant text, so a block renders text nodes and nothing else.
    const wrapper = await renderView(ObsKeyPointsBlock, {
      props: {
        block: { ...points, points: [{ text: '<img src=x onerror="alert(1)">' }] },
      },
    })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror="alert(1)">')
  })

  it('shows the caveat and the platform basis sentence (AC-8)', async () => {
    const wrapper = await renderView(ObsKeyPointsBlock, { props: { block: points } })
    expect(wrapper.text()).toContain('only what was said out loud')
    expect(wrapper.text()).toContain('conversation.observers.basis.transcript')
  })

  it('renders a timeline entry and its own basis', async () => {
    const wrapper = await renderView(ObsTimelineBlock, { props: { block: timeline } })
    expect(wrapper.text()).toContain('10:05')
    expect(wrapper.text()).toContain('round opened')
    expect(wrapper.text()).toContain('conversation.observers.basis.recent_window')
  })

  it('renders nothing for a basis this build does not know', async () => {
    // A newer server value is data, not a missing key; echoing the raw enum at
    // the reader would be worse than saying nothing.
    const wrapper = await renderView(ObsTimelineBlock, {
      props: { block: { ...timeline, basis: 'from_the_future' } as unknown as ObservationTimelineBlock },
    })
    expect(wrapper.find('.obs-block__basis').exists()).toBe(false)
  })
})

describe('computed blocks', () => {
  it('shows the submissions-counted denominator and the server_facts basis (AC-8)', async () => {
    const wrapper = await renderView(ObsFieldCoverageBlock, { props: { block: coverage() } })
    expect(wrapper.text()).toContain('conversation.observers.blocks.submissionsCounted')
    expect(wrapper.text()).toContain('conversation.observers.basis.server_facts')
  })

  it('renders no percentage and no participant denominator (AC-9)', async () => {
    const wrapper = await renderView(ObsFieldCoverageBlock, { props: { block: coverage() } })
    const text = wrapper.text()
    expect(text).not.toContain('%')
    expect(text.toLowerCase()).not.toContain('participants')
    expect(text.toLowerCase()).not.toContain('of the class')
  })

  it('sizes each bar against the counted submissions', async () => {
    const wrapper = await renderView(ObsFieldCoverageBlock, {
      props: { block: coverage({ submissions_counted: 12, cells: CELLS }) },
    })
    const fills = wrapper.findAll('.obs-coverage__fill')
    expect(fills[0]!.attributes('style')).toContain('width: 75%')
    expect(fills[1]!.attributes('style')).toContain('width: 25%')
  })

  it('does not divide by a zero denominator', async () => {
    const wrapper = await renderView(ObsFieldCoverageBlock, {
      props: { block: coverage({ submissions_counted: 0 }) },
    })
    expect(wrapper.find('.obs-coverage__fill').attributes('style')).toContain('width: 0%')
  })

  it('lays a mandala grid out as three rows of three', async () => {
    const wrapper = await renderView(ObsMandalaGridBlock, { props: { block: grid() } })
    expect(wrapper.findAll('.obs-grid__cell')).toHaveLength(9)
    expect(wrapper.text()).toContain('Cell 4')
  })

  it('shows a code, its counts and the error class in the attempt table (AC-7)', async () => {
    const wrapper = await renderView(ObsAttemptTableBlock, { props: { block: table() } })
    const text = wrapper.text()
    expect(text).toContain('u:1a2b3c4d')
    expect(text).toContain('conversation.observers.blocks.outcome.invalid')
    expect(text).toContain('too_few_filled')
  })

  it('says so when the listing was cut short rather than hiding it', async () => {
    const wrapper = await renderView(ObsAttemptTableBlock, {
      props: { block: table({ truncated: true }) },
    })
    expect(wrapper.text()).toContain('conversation.observers.blocks.truncated')
  })

  it('renders an outcome this build does not know verbatim', async () => {
    const wrapper = await renderView(ObsAttemptTableBlock, {
      props: {
        block: table({
          rows: [
            {
              subject_code: 'u:9',
              attempts: 1,
              submissions: 1,
              latest_outcome: 'quarantined',
              latest_error_class: null,
            },
          ],
        }),
      },
    })
    expect(wrapper.text()).toContain('quarantined')
    expect(wrapper.text()).not.toContain('outcome.quarantined')
  })
})

describe('ObservationBlocks switch', () => {
  it('renders every kind in array order', async () => {
    const blocks: ObservationBlock[] = [points, timeline, coverage(), grid(), table()]
    const wrapper = await renderView(ObservationBlocks, { props: { blocks } })
    const text = wrapper.text()
    expect(text.indexOf('Three things')).toBeLessThan(text.indexOf('10:05'))
    expect(wrapper.findAll('.obs-grid__cell')).toHaveLength(9)
  })

  it('hands a prose block to the scoped slot wherever it sits', async () => {
    // The card owns the one allowlisted `v-html` binding, so a prose block at any
    // position has to reach it through the slot rather than sanitise its own.
    const blocks = [
      timeline,
      { kind: 'prose', text: 'middle words' },
      timeline,
    ] as ObservationBlock[]
    const wrapper = await renderView(ObservationBlocks, {
      props: { blocks },
      slots: { prose: '<p class="slotted">{{ params.block.text }}</p>' },
    })
    expect(wrapper.find('.slotted').text()).toBe('middle words')
  })

  it('renders an unknown kind as a cannot-display line without throwing (AC-13)', async () => {
    const blocks = [
      { kind: 'bar_chart', title: 'Something new' },
      points,
    ] as unknown as ObservationBlock[]
    const wrapper = await renderView(ObservationBlocks, { props: { blocks } })
    expect(wrapper.text()).toContain('Something new')
    expect(wrapper.text()).toContain('conversation.observers.blocks.cannotDisplay')
    // The other blocks in the array still reach the creator.
    expect(wrapper.text()).toContain('the room split three ways')
  })

  it('renders an unknown kind with no title without an empty heading', async () => {
    const blocks = [{ kind: 'bar_chart' }] as unknown as ObservationBlock[]
    const wrapper = await renderView(ObservationBlocks, { props: { blocks } })
    expect(wrapper.find('.obs-unknown__title').exists()).toBe(false)
    expect(wrapper.text()).toContain('conversation.observers.blocks.cannotDisplay')
  })
})
