import { ApiError } from '@shared/errors'

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

export type FreshDownloadResult = 'ok' | 'expired' | 'failed'

// Download an export from a link minted now, not the one held since the job
// became ready: links are short-lived and the user may click much later. A job
// the server no longer knows (404) has outlived its 24 h archive.
export async function downloadFresh(
  fetchStatus: () => Promise<{ status: string; url?: string | null }>,
): Promise<FreshDownloadResult> {
  try {
    const fresh = await fetchStatus()
    if (fresh.status === 'ready' && fresh.url) {
      triggerDownload(fresh.url)
      return 'ok'
    }
    return fresh.status === 'failed' ? 'failed' : 'expired'
  } catch (err) {
    return err instanceof ApiError && err.status === 404 ? 'expired' : 'failed'
  }
}
