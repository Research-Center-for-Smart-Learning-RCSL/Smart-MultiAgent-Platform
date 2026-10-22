import { appendFileSync } from 'node:fs'

const TOKEN_FIELD = /("(?:access_token|refresh_token|guest_token|token|password|new_password|api_key)"\s*:\s*")[^"]*(")/gi
const BEARER = /(Bearer\s+)[A-Za-z0-9._~+/=-]+/g
const FRAGMENT_TOKEN = /(#token=)[^&\s"]+/g

/**
 * Removes every configured secret value and every token-shaped field from text before it
 * leaves the process. Secrets are registered as they become known (passwords, keys,
 * minted tokens), so the redactor is the single place AC-2 depends on.
 */
export class Redactor {
  private readonly secrets = new Set<string>()

  add(value: string | undefined | null): void {
    if (value && value.length >= 6) this.secrets.add(value)
  }

  redact(text: string): string {
    let out = text.replace(TOKEN_FIELD, '$1[redacted]$2').replace(BEARER, '$1[redacted]').replace(FRAGMENT_TOKEN, '$1[redacted]')
    for (const s of this.secrets) out = out.split(s).join('[redacted]')
    return out
  }
}

export interface SimEvent {
  t: string
  kind: string
  [key: string]: unknown
}

/** Console plus an optional JSONL event file, both redacted. */
export class Logger {
  private eventsPath: string | null = null
  private readonly redactor: Redactor

  constructor(redactor: Redactor) {
    this.redactor = redactor
  }

  attachEventsFile(path: string): void {
    this.eventsPath = path
  }

  info(message: string): void {
    process.stdout.write(this.redactor.redact(`[sim] ${message}`) + '\n')
  }

  warn(message: string): void {
    process.stderr.write(this.redactor.redact(`[sim] WARN ${message}`) + '\n')
  }

  event(kind: string, fields: Record<string, unknown> = {}): SimEvent {
    const ev: SimEvent = { t: new Date().toISOString(), kind, ...fields }
    if (this.eventsPath) appendFileSync(this.eventsPath, this.redactor.redact(JSON.stringify(ev)) + '\n')
    return ev
  }

  redact(text: string): string {
    return this.redactor.redact(text)
  }
}
