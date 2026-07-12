import { useQuery } from '@tanstack/vue-query'

import { ModelCatalogService } from '@shared/api-client'
import type { ModelCatalogOut } from '@shared/api-client'

// The provider/model catalog is immutable global config (not project-scoped).
// One shared query, keyed globally, so every slice that needs it (agent + RAG
// forms, the prompt-studio config editor) reads the same cache entry and the
// catalog is fetched once per session instead of once per slice.
//
// The generated `ModelCatalogOut` is field-identical to the shapes these forms
// consumed before, so it is aliased directly rather than re-declared.
export type ModelCatalog = ModelCatalogOut

export const MODEL_CATALOG_KEY = ['model-catalog'] as const

export function useModelCatalog() {
  return useQuery({
    queryKey: MODEL_CATALOG_KEY,
    queryFn: () => ModelCatalogService.getModelCatalogApiModelCatalogGet(),
    staleTime: Infinity,
  })
}
