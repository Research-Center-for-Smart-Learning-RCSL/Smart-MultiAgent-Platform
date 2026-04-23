import { describe, it, expect } from 'vitest'
import { CAPABILITIES } from '../api/keys'

// Front-end mirror of the R7.01 golden. If either side drifts, the wire
// contract drifts — this test catches a one-sided change quickly.
describe('R7.01 capabilities (FE mirror)', () => {
  it('matches the authoritative table exactly', () => {
    expect(CAPABILITIES).toEqual({
      claude: ['llm_chat'],
      openai: ['llm_chat', 'embedding'],
      gemini: ['llm_chat', 'embedding'],
      voyage: ['embedding'],
      cohere: ['rerank'],
    })
  })
})
