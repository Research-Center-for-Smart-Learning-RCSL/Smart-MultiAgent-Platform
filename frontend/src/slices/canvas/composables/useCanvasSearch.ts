import { computed, onUnmounted, ref, watch, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import DOMPurify from 'dompurify'
import { canvasKeys } from '../queries'
import * as canvasApi from '../api'
import type { CanvasSearchResult } from '../types'

const SNIPPET_PURIFY_CONFIG = {
  ALLOWED_TAGS: ['mark'],
  ALLOWED_ATTR: [] as string[],
}

function sanitizeSnippet(html: string): string {
  return DOMPurify.sanitize(html ?? '', SNIPPET_PURIFY_CONFIG) as unknown as string
}

export interface SanitizedSearchResult extends CanvasSearchResult {
  sanitizedSnippet: string
}

export function useCanvasSearch(chatroomId: Ref<string>) {
  const query = ref('')
  const debouncedQuery = ref('')

  let debounceTimer: ReturnType<typeof setTimeout> | undefined

  watch(query, (val) => {
    clearTimeout(debounceTimer)
    if (!val.trim()) {
      debouncedQuery.value = ''
      return
    }
    debounceTimer = setTimeout(() => {
      debouncedQuery.value = val.trim()
    }, 300)
  })

  onUnmounted(() => clearTimeout(debounceTimer))

  const searchQuery = useQuery({
    queryKey: computed(() => canvasKeys.search(chatroomId.value, debouncedQuery.value)),
    queryFn: () => canvasApi.searchCanvas(chatroomId.value, debouncedQuery.value),
    enabled: computed(() => !!chatroomId.value && debouncedQuery.value.length > 0),
  })

  const results = computed<SanitizedSearchResult[]>(() =>
    (searchQuery.data.value ?? []).map((r) => ({
      ...r,
      sanitizedSnippet: sanitizeSnippet(r.snippet),
    })),
  )
  const isSearching = computed(() => searchQuery.isFetching.value)

  function clearSearch() {
    query.value = ''
    debouncedQuery.value = ''
  }

  return {
    query,
    results,
    isSearching,
    clearSearch,
  }
}
