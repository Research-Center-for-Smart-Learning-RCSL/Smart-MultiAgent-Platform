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
 * Both the previous title and description are captured on mount and restored on
 * unmount (a tag this view created is removed), so a view's metadata does not
 * leak onto whatever route follows it. No-ops under SSR.
 */
export function useDocumentMeta(meta: DocumentMeta): void {
  let previousTitle = ''
  let previousDescription: string | null = null
  let createdDescriptionTag = false

  function descriptionTag(): HTMLMetaElement | null {
    if (typeof document === 'undefined') return null
    return document.head.querySelector<HTMLMetaElement>('meta[name="description"]')
  }

  function ensureDescriptionTag(): HTMLMetaElement | null {
    if (typeof document === 'undefined') return null
    let tag = descriptionTag()
    if (!tag) {
      tag = document.createElement('meta')
      tag.name = 'description'
      document.head.appendChild(tag)
      createdDescriptionTag = true
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
    if (typeof document !== 'undefined') {
      previousTitle = document.title
      if (meta.description) previousDescription = descriptionTag()?.content ?? null
    }
    apply()
  })

  watch(() => [meta.title?.(), meta.description?.()], apply)

  onBeforeUnmount(() => {
    if (typeof document === 'undefined') return
    if (meta.title) document.title = previousTitle
    if (meta.description) {
      const tag = descriptionTag()
      if (tag) {
        if (createdDescriptionTag) tag.remove()
        else if (previousDescription !== null) tag.content = previousDescription
      }
    }
  })
}
