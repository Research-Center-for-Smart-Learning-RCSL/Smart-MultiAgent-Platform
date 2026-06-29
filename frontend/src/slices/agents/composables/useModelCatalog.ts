import { useQuery } from '@tanstack/vue-query'
import { agentsApi } from '../api'
import { agentKeys } from '../queries'

// The provider/model catalog is immutable global config. Fetch it once per
// session (staleTime: Infinity) and share this single query across the agent and
// RAG-config forms instead of each view defining its own query that refetches on
// every mount/refocus.
export function useModelCatalog() {
  return useQuery({
    queryKey: agentKeys.modelCatalog(),
    queryFn: async () => (await agentsApi.getModelCatalog()).data,
    staleTime: Infinity,
  })
}
