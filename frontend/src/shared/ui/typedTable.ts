import STable from './STable.vue'

// STable's row generic (`T extends Record<string, unknown> = Record<string, unknown>`)
// falls back to its default type instead of inferring from `:data`/slot usage at a
// call site (a Volar limitation with generic script-setup props that declare a default
// type argument). `typedTable<Row>()` returns the same STable pinned to `Row`, so `row`
// in slots/emits resolves to `Row` instead of `Record<string, unknown>`. Runtime is
// unchanged — this is a compile-time cast only.

const _fixedSTable = STable<Record<string, unknown>>
type STablePropsBase = Parameters<typeof _fixedSTable>[0]

export function typedTable<T extends object>() {
  return STable as unknown as new () => {
    $props: Omit<STablePropsBase, 'data'> & {
      data?: T[]
      onRowClick?: (row: T) => void
    }
    $slots: {
      [key: string]: (arg: { row: T; value: unknown; index: number }) => unknown
    }
  }
}
