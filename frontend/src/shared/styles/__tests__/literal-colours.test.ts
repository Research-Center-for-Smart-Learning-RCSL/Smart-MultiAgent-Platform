import { describe, it, expect } from 'vitest'

/**
 * AC-12 of `docs/tasks/2026-08-21-visual-refinement-phase2-identity-and-depth`.
 *
 * A neutral-axis change that misses the literals is a half-change, and it shows
 * as a hue mismatch on exactly the screens nobody opens during development. The
 * literals that remain are the ones where a token would be *wrong*, not merely
 * inconvenient - each is listed here with the role that keeps it, so the next
 * sweep reads the reason instead of re-deciding it per file.
 */
const sources = import.meta.glob('/src/**/*.vue', { eager: true, query: '?raw', import: 'default' }) as Record<
  string,
  string
>

/** Files permitted to state a colour literally, and why. */
const RETAINED: Record<string, string> = {
  // Categorical scales. Their job is to keep categories apart; semantic tokens
  // carry meanings these categories do not have, and there are more categories
  // than there are semantic roles.
  'slices/workflow/components/WorkflowNodeComponent.vue':
    'node-type palette, plus three run-state tints mixed into --color-bg at a fixed 12%',
  'slices/agents/views/GraphragGraphView.vue': 'entity-type palette',
  // A brand mark is not ours to retheme.
  'slices/identity/views/LoginView.vue': "Google's G mark",
  'slices/identity/views/RegisterView.vue': "Google's G mark",
  // #000 in a mask is an alpha selector, not a colour.
  'app/views/Landing.vue': 'mask-image alpha stops',
  // Must read against BOTH track states, so it can follow neither.
  'shared/ui/SToggle.vue': 'the knob and the robot body over both track colours',
}

const HEX = /#[0-9a-fA-F]{3,8}\b/

describe('literal colours', () => {
  it('swept a plausible amount of source', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(200)
  })

  it('states a colour literally only where a token would be wrong', () => {
    const offenders: string[] = []
    for (const [path, text] of Object.entries(sources)) {
      if (path.includes('__tests__/')) continue
      const rel = path.replace('/src/', '')
      if (rel in RETAINED) continue
      for (const line of text.split('\n')) {
        if (HEX.test(line)) offenders.push(`${rel}: ${line.trim()}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('carries no retention for a file that no longer states one', () => {
    // A licence nobody is using would silently re-permit literals if the file
    // ever grew one again for an unrelated reason.
    const stale = Object.keys(RETAINED).filter((rel) => {
      const text = sources[`/src/${rel}`]
      return text === undefined || !HEX.test(text)
    })
    expect(stale).toEqual([])
  })

  it('resolves a fallback-free var() for every semantic colour', () => {
    // `var(--color-danger-tint, #fee2e2)` is a second copy of a token's value
    // that nothing keeps in step: the token always resolves, so the fallback is
    // unreachable until the day someone renames the token, at which point it
    // silently becomes the real value.
    const offenders: string[] = []
    for (const [path, text] of Object.entries(sources)) {
      if (path.includes('__tests__/')) continue
      for (const m of text.matchAll(/var\(\s*--color-[a-z0-9-]+\s*,[^)]*\)/g)) {
        offenders.push(`${path.replace('/src/', '')}: ${m[0]}`)
      }
    }
    expect(offenders).toEqual([])
  })
})
