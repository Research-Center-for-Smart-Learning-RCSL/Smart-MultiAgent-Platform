export interface CompletionRequest {
  model: string
  system: string
  user: string
  maxOutputTokens: number
  signal?: AbortSignal
}

export interface CompletionResult {
  text: string
  inputTokens: number
  outputTokens: number
}

export interface Provider {
  readonly name: 'openai' | 'anthropic'
  complete(req: CompletionRequest): Promise<CompletionResult>
}

export class ProviderError extends Error {}

type FetchLike = typeof fetch

async function postJson(fetchImpl: FetchLike, url: string, headers: Record<string, string>, body: unknown, signal?: AbortSignal): Promise<unknown> {
  const init: RequestInit = { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) }
  if (signal) init.signal = signal
  const resp = await fetchImpl(url, init)
  const text = await resp.text()
  // The body may echo request content but never the key, which travels only in a header.
  if (!resp.ok) throw new ProviderError(`provider HTTP ${resp.status}: ${text.slice(0, 300)}`)
  return JSON.parse(text)
}

/** OpenAI Chat Completions with JSON mode. */
export class OpenAIProvider implements Provider {
  readonly name = 'openai' as const
  private readonly apiKey: string
  private readonly baseUrl: string
  private readonly fetchImpl: FetchLike

  constructor(apiKey: string, baseUrl = 'https://api.openai.com/v1', fetchImpl: FetchLike = fetch) {
    this.apiKey = apiKey
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.fetchImpl = fetchImpl
  }

  buildBody(req: CompletionRequest): Record<string, unknown> {
    return {
      model: req.model,
      messages: [
        { role: 'system', content: req.system },
        { role: 'user', content: req.user },
      ],
      response_format: { type: 'json_object' },
      max_completion_tokens: req.maxOutputTokens,
    }
  }

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    const data = (await postJson(
      this.fetchImpl,
      `${this.baseUrl}/chat/completions`,
      { Authorization: `Bearer ${this.apiKey}` },
      this.buildBody(req),
      req.signal,
    )) as {
      choices?: Array<{ message?: { content?: string | null } }>
      usage?: { prompt_tokens?: number; completion_tokens?: number }
    }
    return {
      text: data.choices?.[0]?.message?.content ?? '',
      inputTokens: data.usage?.prompt_tokens ?? 0,
      outputTokens: data.usage?.completion_tokens ?? 0,
    }
  }
}

/** Anthropic Messages API. */
export class AnthropicProvider implements Provider {
  readonly name = 'anthropic' as const
  private readonly apiKey: string
  private readonly baseUrl: string
  private readonly fetchImpl: FetchLike

  constructor(apiKey: string, baseUrl = 'https://api.anthropic.com/v1', fetchImpl: FetchLike = fetch) {
    this.apiKey = apiKey
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.fetchImpl = fetchImpl
  }

  buildBody(req: CompletionRequest): Record<string, unknown> {
    return {
      model: req.model,
      max_tokens: req.maxOutputTokens,
      system: req.system,
      messages: [{ role: 'user', content: req.user }],
    }
  }

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    const data = (await postJson(
      this.fetchImpl,
      `${this.baseUrl}/messages`,
      { 'x-api-key': this.apiKey, 'anthropic-version': '2023-06-01' },
      this.buildBody(req),
      req.signal,
    )) as {
      content?: Array<{ type?: string; text?: string }>
      usage?: { input_tokens?: number; output_tokens?: number }
    }
    const text = (data.content ?? []).filter((c) => c.type === 'text').map((c) => c.text ?? '').join('')
    return { text, inputTokens: data.usage?.input_tokens ?? 0, outputTokens: data.usage?.output_tokens ?? 0 }
  }
}

export type PersonaDecision = { action: 'say'; text: string } | { action: 'type_pause'; text: string } | { action: 'silent' }

/**
 * Reads the model's reply. Anything that is not a well-formed decision is treated as
 * silence: a persona that cannot be understood should not post into a class.
 */
export function parseDecision(raw: string, maxChars: number): PersonaDecision {
  const start = raw.indexOf('{')
  const end = raw.lastIndexOf('}')
  if (start < 0 || end <= start) return { action: 'silent' }
  let obj: unknown
  try {
    obj = JSON.parse(raw.slice(start, end + 1))
  } catch {
    return { action: 'silent' }
  }
  if (!obj || typeof obj !== 'object') return { action: 'silent' }
  const { action, text } = obj as { action?: unknown; text?: unknown }
  if ((action === 'say' || action === 'type_pause') && typeof text === 'string' && text.trim()) {
    return { action, text: text.trim().slice(0, maxChars) }
  }
  return { action: 'silent' }
}
