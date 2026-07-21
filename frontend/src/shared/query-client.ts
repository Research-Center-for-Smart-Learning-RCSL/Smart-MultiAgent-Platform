import { QueryClient } from '@tanstack/vue-query'
import { AuthError, PermissionError, RateLimitError } from '@shared/errors'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof AuthError || error instanceof PermissionError) return false
        // A 429 means the caller is already over budget for the window. Retrying
        // inside that window cannot succeed and spends more of it — with two
        // retries per query, hitting the limit triples the request rate exactly
        // when the server is asking for less. Surface it instead.
        if (error instanceof RateLimitError) return false
        return failureCount < 2
      },
    },
    mutations: {
      retry: false,
    },
  },
})
