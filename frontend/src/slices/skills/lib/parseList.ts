// requires / allowed_tools are edited as one-per-line text and normalised to a string[]
// on save; blank lines are dropped.
export function parseList(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}
