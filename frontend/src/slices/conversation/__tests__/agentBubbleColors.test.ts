import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { AGENT_BUBBLE_COLORS, isAgentBubbleColor } from '../utils/agentBubbleColors'

// Read from disk for the reason contrast.test.ts gives: an import of main.css
// yields Tailwind's processed output, not the token declarations.
const mainCss = readFileSync(resolve(process.cwd(), 'src/shared/styles/main.css'), 'utf-8')

function declaredKeys(suffix: string): string[] {
  const re = new RegExp(`--color-agent-([a-z]+)${suffix}\\s*:`, 'g')
  return [...new Set([...mainCss.matchAll(re)].map((m) => m[1]!))].sort()
}

describe('agent bubble palette', () => {
  it('declares a border and a tint token for exactly the keys the API accepts', () => {
    const palette = [...AGENT_BUBBLE_COLORS].sort()
    expect(declaredKeys('')).toEqual(palette)
    expect(declaredKeys('-tint')).toEqual(palette)
  })

  it('accepts only palette keys', () => {
    expect(isAgentBubbleColor('teal')).toBe(true)
    for (const value of ['blue', 'toString', '__proto__', '', null, 1]) {
      expect(isAgentBubbleColor(value)).toBe(false)
    }
  })
})
