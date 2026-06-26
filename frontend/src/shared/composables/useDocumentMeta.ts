import { onBeforeUnmount, onMounted, watch } from 'vue'

interface DocumentMeta {
  title?: () => string
  description?: () => string
}

/**
 * Manages the document `<title>` and `<meta name="description">` for the
 * lifetime of a view. Pass reactive getters (e.g. ones that call `t()`); the
 * tags re-apply when their sources change, so switching locale updates them.
 *
 * The previous `<title>` is captured on mount and restored on unmount, so a
 * view's title does not leak onto whatever route follows it. No-ops under SSR.
 */
export function useDocumentMeta(meta: DocumentMeta): void {
  let previousTitle = ''

  function ensureDescriptionTag(): HTMLMetaElement | null {
    if (typeof document === 'undefined') return null
    let tag = document.head.querySelector<HTMLMetaElement>('meta[name="description"]')
    if (!tag) {
      tag = document.createElement('meta')
      tag.name = 'description'
      document.head.appendChild(tag)
    }
    return tag
  }

  function apply(): void {
    if (typeof document === 'undefined') return
    if (meta.title) document.title = meta.title()
    if (meta.description) {
      const tag = ensureDescriptionTag()
      if (tag) tag.content = meta.description()
    }
  }

  onMounted(() => {
    previousTitle = typeof document !== 'undefined' ? document.title : ''
    apply()
  })

  watch(() => [meta.title?.(), meta.description?.()], apply)

  onBeforeUnmount(() => {
    if (typeof document !== 'undefined' && meta.title) document.title = previousTitle
  })
}
