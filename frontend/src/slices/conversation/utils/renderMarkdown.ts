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
import hljs from 'highlight.js'
import katex from 'katex'
import MarkdownIt from 'markdown-it'
import mermaid from 'mermaid'

let mermaidInited = false

function ensureMermaid(): void {
  if (mermaidInited) return
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' })
  mermaidInited = true
}

const md = new MarkdownIt({
  html: true,
  linkify: true,
  breaks: false,
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return (
          `<pre class="hljs"><code class="language-${lang}">` +
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
          '</code></pre>'
        )
      } catch {
        /* fall through */
      }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  },
})

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
  const raw = md.render(source ?? '')
  return DOMPurify.sanitize(raw, PURIFY_CONFIG) as unknown as string
}

/** Sanitise a snippet that's already HTML (e.g. ts_headline output). */
export function sanitizeSnippet(html: string): string {
  return DOMPurify.sanitize(html ?? '', PURIFY_CONFIG) as unknown as string
}

/** KaTeX pass — replaces `$$...$$` blocks that survived sanitisation. */
export function katexInDom(root: HTMLElement): void {
  root.querySelectorAll('code.language-math, code.language-latex').forEach((node) => {
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

/** Mermaid pass — renders `code.language-mermaid` blocks in place. */
export async function mermaidInDom(root: HTMLElement): Promise<void> {
  ensureMermaid()
  const nodes = Array.from(
    root.querySelectorAll<HTMLElement>('code.language-mermaid'),
  )
  for (const [i, node] of nodes.entries()) {
    const id = `smap-mermaid-${Date.now()}-${i}`
    try {
      const { svg } = await mermaid.render(id, node.textContent ?? '')
      const wrapper = document.createElement('div')
      wrapper.className = 'smap-mermaid'
      // Sanitize Mermaid's SVG output before inserting into the DOM — the
      // mermaid renderer's strict mode does not guarantee XSS-free output.
      wrapper.innerHTML = DOMPurify.sanitize(svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
      }) as unknown as string
      node.parentElement?.replaceWith(wrapper)
    } catch {
      /* keep the raw block visible */
    }
  }
}

/** Drive all three passes in sequence. Safe to call on onMounted/onUpdated. */
export async function enhanceRenderedMarkdown(root: HTMLElement): Promise<void> {
  katexInDom(root)
  await mermaidInDom(root)
}
