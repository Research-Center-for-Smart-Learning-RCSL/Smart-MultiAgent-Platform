// Client mirror of the backend label rule (backend/shared_kernel/labels.py):
// drop every Unicode category C and default-ignorable character except ZERO
// WIDTH JOINER and VARIATION SELECTOR-16, which live inside emoji, then trim; a
// label is usable only if some character draws on its own. The backend stays
// authoritative; this only spares a round trip for a name it would refuse.
//
// Invisible code points are written as escapes: an editor or a paste can strip
// them silently, and nothing else would notice.

const KEEP = new Set(['\u200D', '\uFE0F'])
const HIDDEN = /[\p{C}\p{Default_Ignorable_Code_Point}]/gu
// Whitespace, combining marks and the blank Braille cell draw nothing alone.
const NOT_VISIBLE = /^[\s\p{M}\u200D\uFE0F\u2800]*$/u
// What a model-facing label drops because its containers use them
// (`one_line_label` in the backend).
const LABEL_DELIMITERS = /["[\]]/g

export function normaliseLabel(raw: string): string {
  return raw.replace(HIDDEN, (ch) => (KEEP.has(ch) ? ch : '')).trim()
}

export function isUsableLabel(raw: string): boolean {
  return !NOT_VISIBLE.test(normaliseLabel(raw))
}

// Agent names label turns in other agents' context, so they must still say
// something once the label delimiters are dropped.
export function isUsableModelLabel(raw: string): boolean {
  return !NOT_VISIBLE.test(normaliseLabel(raw).replace(LABEL_DELIMITERS, ''))
}
