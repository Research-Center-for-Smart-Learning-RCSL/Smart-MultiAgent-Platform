import { describe, expect, it } from 'vitest'
import { AnthropicProvider, OpenAIProvider, ProviderError, parseDecision } from '../llm/provider.ts'

interface Captured {
  url: string
  headers: Record<string, string>
  body: Record<string, unknown>
}

function fakeFetch(status: number, response: unknown): { fetch: typeof fetch; calls: Captured[] } {
  const calls: Captured[] = []
  const impl = (async (url: string, init: RequestInit) => {
    calls.push({ url, headers: init.headers as Record<string, string>, body: JSON.parse(String(init.body)) })
    return new Response(typeof response === 'string' ? response : JSON.stringify(response), { status })
  }) as unknown as typeof fetch
  return { fetch: impl, calls }
}

const REQ = { model: 'm-1', system: 'sys', user: 'usr', maxOutputTokens: 240 }

describe('OpenAIProvider (AC-11)', () => {
  it('sends a JSON-mode chat completion with the key only in the Authorization header', async () => {
    const f = fakeFetch(200, {
      choices: [{ message: { content: '{"action":"say","text":"hi"}' } }],
      usage: { prompt_tokens: 50, completion_tokens: 7 },
    })
    const res = await new OpenAIProvider('sk-test-key', 'https://llm.example/v1/', f.fetch).complete(REQ)
    const call = f.calls[0] as Captured
    expect(call.url).toBe('https://llm.example/v1/chat/completions')
    expect(call.headers.Authorization).toBe('Bearer sk-test-key')
    expect(call.body).toEqual({
      model: 'm-1',
      messages: [
        { role: 'system', content: 'sys' },
        { role: 'user', content: 'usr' },
      ],
      response_format: { type: 'json_object' },
      max_completion_tokens: 240,
    })
    expect(JSON.stringify(call.body)).not.toContain('sk-test-key')
    expect(res).toEqual({ text: '{"action":"say","text":"hi"}', inputTokens: 50, outputTokens: 7 })
  })

  it('raises a ProviderError on an HTTP error', async () => {
    const f = fakeFetch(401, { error: { message: 'bad key' } })
    await expect(new OpenAIProvider('sk-test-key', undefined, f.fetch).complete(REQ)).rejects.toBeInstanceOf(ProviderError)
  })
})

describe('AnthropicProvider (AC-11)', () => {
  it('sends a Messages request with x-api-key and a version header', async () => {
    const f = fakeFetch(200, {
      content: [
        { type: 'text', text: '{"action":"silent"' },
        { type: 'text', text: '}' },
      ],
      usage: { input_tokens: 80, output_tokens: 4 },
    })
    const res = await new AnthropicProvider('ak-test-key', undefined, f.fetch).complete(REQ)
    const call = f.calls[0] as Captured
    expect(call.url).toBe('https://api.anthropic.com/v1/messages')
    expect(call.headers['x-api-key']).toBe('ak-test-key')
    expect(call.headers['anthropic-version']).toBe('2023-06-01')
    expect(call.body).toEqual({ model: 'm-1', max_tokens: 240, system: 'sys', messages: [{ role: 'user', content: 'usr' }] })
    expect(res).toEqual({ text: '{"action":"silent"}', inputTokens: 80, outputTokens: 4 })
  })
})

describe('parseDecision', () => {
  it('reads say and type_pause, trimming and truncating the text', () => {
    expect(parseDecision('{"action":"say","text":"  我覺得是心  "}', 100)).toEqual({ action: 'say', text: '我覺得是心' })
    expect(parseDecision('{"action":"type_pause","text":"abcdef"}', 3)).toEqual({ action: 'type_pause', text: 'abc' })
  })

  it('reads a decision wrapped in prose or a code fence', () => {
    expect(parseDecision('Sure:\n```json\n{"action":"say","text":"好"}\n```', 100)).toEqual({ action: 'say', text: '好' })
  })

  it('treats silence, empty text, unknown actions and malformed JSON as silence', () => {
    for (const raw of ['{"action":"silent"}', '{"action":"say","text":"   "}', '{"action":"shout","text":"x"}', '{"action":"say"', 'no json', '[1,2]']) {
      expect(parseDecision(raw, 100)).toEqual({ action: 'silent' })
    }
  })
})
