/** Send each id at most once, in batches of `batchSize`. A failed batch's ids
 *  are forgotten so a later call retries them; the caller's `send` failure is
 *  otherwise swallowed, which suits best-effort telemetry and nothing else. */
export function onceBatchReporter(
  send: (ids: string[]) => Promise<unknown>,
  batchSize: number,
): (ids: string[]) => void {
  const sent = new Set<string>()
  return (ids) => {
    const fresh = [...new Set(ids)].filter((id) => !sent.has(id))
    for (let i = 0; i < fresh.length; i += batchSize) {
      const batch = fresh.slice(i, i + batchSize)
      for (const id of batch) sent.add(id)
      send(batch).catch(() => {
        for (const id of batch) sent.delete(id)
      })
    }
  }
}
