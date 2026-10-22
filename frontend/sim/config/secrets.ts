import { createHmac } from 'node:crypto'
import { createInterface } from 'node:readline'

/**
 * A simulated account's password, derived rather than stored: the same secret always
 * yields the same password, so nothing on disk holds it. The fixed prefix satisfies the
 * letter/digit/symbol policy of `backend/shared_kernel/auth/password.py:79-94`.
 */
export function derivePassword(secret: string, email: string): string {
  const mac = createHmac('sha256', secret).update(email.toLowerCase()).digest('base64url')
  return `Sim1!${mac.slice(0, 22)}`
}

export function requireEnv(name: string): string {
  const v = process.env[name]
  if (!v) throw new Error(`environment variable ${name} is required`)
  return v
}

/** SMAP_SIM_SECRET: long enough to resist guessing, and to be redactable at all (log.ts ignores short values). */
export function requireSimSecret(): string {
  const v = requireEnv('SMAP_SIM_SECRET')
  if (v.length < 16) throw new Error('SMAP_SIM_SECRET must be at least 16 characters')
  return v
}

/** Reads a value from the environment, else asks for it on a TTY without echoing it. */
export async function secretFromEnvOrPrompt(envName: string, prompt: string): Promise<string> {
  const v = process.env[envName]
  if (v) return v
  if (!process.stdin.isTTY) throw new Error(`${envName} is not set and no terminal is available to ask for it`)
  return hiddenPrompt(prompt)
}

function hiddenPrompt(prompt: string): Promise<string> {
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true })
    const out = rl as unknown as { _writeToOutput?: (s: string) => void; output: NodeJS.WritableStream }
    process.stdout.write(prompt)
    // readline has no public no-echo mode; replacing its private writer is the standard
    // workaround. If a Node release renames it, the prompt echoes and no test notices,
    // so check by hand after a Node upgrade.
    out._writeToOutput = () => {}
    rl.question('', (answer) => {
      rl.close()
      process.stdout.write('\n')
      resolve(answer)
    })
  })
}
