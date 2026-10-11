// Client mirror of the backend's `normalise_label` rule
// (backend/shared_kernel/labels.py): drop every Unicode category C and
// default-ignorable character except ZERO WIDTH JOINER and VARIATION SELECTOR-16,
// which live inside emoji, then trim; a name is usable only if some character
// draws on its own. The backend stays authoritative; this only spares a round trip
// for a name that would come back as `guest-display-name-invalid` (Q-5).

const KEEP = new Set(['\u200D', '\uFE0F'])
const OTHER = /[\p{C}\p{Default_Ignorable_Code_Point}]/gu
// Whitespace, combining marks and the blank Braille cell draw nothing alone.
const NOT_VISIBLE = /^[\s\p{M}\u200D\uFE0F\u2800]*$/u

export function normaliseGuestName(raw: string): string {
  return raw.replace(OTHER, (ch) => (KEEP.has(ch) ? ch : '')).trim()
}

export function isUsableGuestName(raw: string): boolean {
  return !NOT_VISIBLE.test(normaliseGuestName(raw))
}
