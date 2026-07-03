/** Deep-clones a plain JSON-safe value via a serialize round-trip. Safe for a
 *  Vue reactive proxy, which `structuredClone` rejects with DataCloneError. */
export function deepCloneJSON<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}
