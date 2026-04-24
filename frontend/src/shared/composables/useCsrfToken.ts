// Placeholder for future cookie-auth CSRF protection (R24.44).
// Currently a no-op — Bearer auth doesn't require CSRF tokens.

import { ref } from 'vue'

export function useCsrfToken() {
  const token = ref<string | null>(null)

  return { csrfToken: token }
}
