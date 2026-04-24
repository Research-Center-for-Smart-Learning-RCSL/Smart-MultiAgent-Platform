export function idempotencyKey(): string {
  return crypto.randomUUID()
}
