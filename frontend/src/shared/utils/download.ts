// Save a file without leaving the page or opening a window. A same-origin link
// (`/api/files/{ticket}`) honours the `download` attribute, and a navigation
// that ends in a download is not subject to popup blocking, so this works even
// after an `await` has used up the click's user activation.
export function triggerDownload(target: string | Blob, filename?: string): void {
  const href = typeof target === 'string' ? target : URL.createObjectURL(target)
  const a = document.createElement('a')
  a.href = href
  a.download = filename ?? ''
  a.rel = 'noopener'
  // Firefox ignores a click on a detached anchor.
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  if (typeof target !== 'string') URL.revokeObjectURL(href)
}
