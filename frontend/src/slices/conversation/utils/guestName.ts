// Client mirror of the backend's `normalise_label` rule
// (backend/shared_kernel/labels.py): drop every Unicode category C character
// except ZERO WIDTH JOINER and VARIATION SELECTOR-16, which live inside emoji,
// then trim. The backend stays authoritative; this only spares a round trip for
// a name that would come back as `guest-display-name-invalid` (Q-5).

const KEEP = new Set(['‍', '️'])
const OTHER = /\p{C}/gu

export function normaliseGuestName(raw: string): string {
  return raw.replace(OTHER, (ch) => (KEEP.has(ch) ? ch : '')).trim()
}

export function isUsableGuestName(raw: string): boolean {
  return normaliseGuestName(raw).length > 0
}
