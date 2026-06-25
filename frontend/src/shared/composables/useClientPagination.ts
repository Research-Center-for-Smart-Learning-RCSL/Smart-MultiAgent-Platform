import { computed, ref, watch, type Ref, type ComputedRef } from 'vue'

export interface ClientPagination<T> {
  currentPage: Ref<number>
  totalPages: ComputedRef<number>
  paginatedItems: ComputedRef<T[]>
  pageSize: number
}

export function useClientPagination<T>(
  items: Ref<T[]> | ComputedRef<T[]>,
  size = 20,
): ClientPagination<T> {
  const currentPage = ref(1)

  const totalPages = computed(() => Math.max(1, Math.ceil(items.value.length / size)))

  watch(totalPages, (tp) => {
    if (currentPage.value > tp) currentPage.value = tp
  })

  const paginatedItems = computed(() => {
    const start = (currentPage.value - 1) * size
    return items.value.slice(start, start + size)
  })

  return { currentPage, totalPages, paginatedItems, pageSize: size }
}
