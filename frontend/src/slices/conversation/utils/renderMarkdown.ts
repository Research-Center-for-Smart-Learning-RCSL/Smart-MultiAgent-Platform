// Single `v-html` site for the whole slice (R24.41 / R24.42). Any other
// use of `v-html` is forbidden by ESLint (gate enabled in Phase J).
//
// Pipeline order (strict — changing it breaks the XSS contract):
//   1. markdown-it → raw HTML
//   2. DOMPurify   → allowlisted HTML
//   3. post-process via DOM-mutation APIs ONLY (KaTeX, Mermaid, hljs)
//
// Post-processing is not done here because the composable renders into a
// live DOM node after the template has committed. `renderMarkdown` returns
// the sanitised string only; the component calls `highlightInDom(node)` on
// `onMounted` / `onUpdated`.

import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

let mermaidInited = false

// Lazily constructed so importing this module has no top-level side effect.
// Eager-vs-lazy bundle placement is already decided by the import graph (this
// module is only reached via the lazy chatroom/search chunks); the lazy
// singleton additionally avoids tree-shaking surprises if an eager module ever
// imports it, and defers the MarkdownIt instantiation cost to first render.
let _md: MarkdownIt | null = null

function getMd(): MarkdownIt {
  if (_md) return _md
  const md = new MarkdownIt({
    html: true,
    linkify: true,
    breaks: false,
    highlight(str, lang) {
      const escaped = md.utils.escapeHtml(str)
      if (lang) {
        return `<pre class="hljs"><code class="language-${md.utils.escapeHtml(lang)}">${escaped}</code></pre>`
      }
      return `<pre class="hljs"><code>${escaped}</code></pre>`
    },
  })
  _md = md
  return _md
}

// Strict DOMPurify config — event handlers, javascript: URIs, etc. are
// already stripped by the default. We also remove `style`.
const PURIFY_CONFIG: DOMPurify.Config = {
  ALLOWED_TAGS: [
    'a',
    'abbr',
    'b',
    'blockquote',
    'br',
    'code',
    'div',
    'em',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'hr',
    'i',
    'img',
    'li',
    'ol',
    'p',
    'pre',
    's',
    'span',
    'strong',
    'sub',
    'sup',
    'table',
    'tbody',
    'td',
    'th',
    'thead',
    'tr',
    'ul',
  ],
  ALLOWED_ATTR: ['href', 'rel', 'target', 'title', 'class', 'id', 'alt', 'src', 'width', 'height'],
  FORBID_ATTR: ['style', 'onerror', 'onload'],
  FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'meta'],
}

/** Render markdown → sanitised HTML. Returns a string safe to pass to v-html. */
export function renderMarkdown(source: string): string {
  const raw = getMd().render(source ?? '')
  return DOMPurify.sanitize(raw, PURIFY_CONFIG) as unknown as string
}

/** Sanitise a snippet that's already HTML (e.g. ts_headline output). */
export function sanitizeSnippet(html: string): string {
  return DOMPurify.sanitize(html ?? '', PURIFY_CONFIG) as unknown as string
}

/** Syntax-highlight pass — lazy-loads highlight.js/lib/common on first use. */
async function hljsInDom(root: HTMLElement): Promise<void> {
  const blocks = Array.from(
    root.querySelectorAll<HTMLElement>('pre code[class*="language-"]'),
  ).filter(
    (el) =>
      // Skip blocks already highlighted on a previous pass — onUpdated re-runs
      // over the whole message list, and re-highlighting every block each time
      // is wasteful (and warns in highlight.js).
      !el.dataset.highlighted &&
      !el.classList.contains('language-mermaid') &&
      !el.classList.contains('language-math') &&
      !el.classList.contains('language-latex'),
  )
  if (!blocks.length) return
  const hljs = (await import('highlight.js/lib/common')).default
  blocks.forEach((block) => {
    try {
      hljs.highlightElement(block)
    } catch {
      /* keep unhighlighted */
    }
  })
}

/** KaTeX pass — lazy-loads katex on first use. */
async function katexInDom(root: HTMLElement): Promise<void> {
  const nodes = root.querySelectorAll('code.language-math, code.language-latex')
  if (!nodes.length) return
  const katex = (await import('katex')).default
  nodes.forEach((node) => {
    try {
      const rendered = katex.renderToString(node.textContent ?? '', {
        displayMode: true,
        throwOnError: false,
      })
      const wrapper = document.createElement('div')
      wrapper.className = 'smap-katex'
      wrapper.innerHTML = rendered
      node.parentElement?.replaceWith(wrapper)
    } catch {
      /* keep original block */
    }
  })
}

/** Mermaid pass — lazy-loads mermaid on first use. */
async function mermaidInDom(root: HTMLElement): Promise<void> {
  const nodes = Array.from(
    root.querySelectorAll<HTMLElement>('code.language-mermaid'),
  )
  if (!nodes.length) return
  const mermaid = (await import('mermaid')).default
  if (!mermaidInited) {
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' })
    mermaidInited = true
  }
  for (const [i, node] of nodes.entries()) {
    const id = `smap-mermaid-${Date.now()}-${i}`
    try {
      const { svg } = await mermaid.render(id, node.textContent ?? '')
      const wrapper = document.createElement('div')
      wrapper.className = 'smap-mermaid'
      wrapper.innerHTML = DOMPurify.sanitize(svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
      }) as unknown as string
      node.parentElement?.replaceWith(wrapper)
    } catch {
      /* keep the raw block visible */
    }
  }
}

/** Drive all three passes. Safe to call on onMounted/onUpdated. */
export async function enhanceRenderedMarkdown(root: HTMLElement): Promise<void> {
  await Promise.allSettled([hljsInDom(root), katexInDom(root), mermaidInDom(root)])
}
