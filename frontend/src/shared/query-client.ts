import { QueryClient } from '@tanstack/vue-query'
import { AuthError, PermissionError } from '@shared/errors'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof AuthError || error instanceof PermissionError) return false
        return failureCount < 2
      },
    },
    mutations: {
      retry: false,
    },
  },
})
