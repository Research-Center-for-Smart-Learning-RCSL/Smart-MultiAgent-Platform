import { describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { onceBatchReporter } from '../onceBatchReporter'

describe('onceBatchReporter', () => {
  it('sends each id once, in bounded batches', () => {
    const send = vi.fn(async () => undefined)
    const report = onceBatchReporter(send, 2)
    report(['a', 'b', 'c', 'a'])
    report(['a', 'c', 'd'])
    expect(send.mock.calls).toEqual([[['a', 'b']], [['c']], [['d']]])
  })

  it('retries the ids of a failed batch on the next call', async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue(undefined)
    const report = onceBatchReporter(send, 50)
    report(['a'])
    await flushPromises()
    report(['a'])
    expect(send).toHaveBeenCalledTimes(2)
  })
})
