import { describe, expect, it } from 'vitest'
import { buildDashboardChartData, dashboardBucketForWindow } from '../chartData'
import type { RoomSummary, TimeseriesBucket } from '../types'

const now = Date.parse('2026-10-20T10:00:00.000Z')

function room(roomId: string): RoomSummary {
  return {
    room_id: roomId,
    room_name: `Room ${roomId}`,
    round: null,
    total_submissions: 0,
    valid_count: 0,
    last_valid_submission_at: null,
    status: 'inactive',
    online_count: null,
    participant_count: 0,
    message_counts: { human: 0, agent: 0 },
  }
}

describe('dashboard chart data', () => {
  it.each([
    ['1h', '5m'],
    ['6h', '15m'],
    ['24h', '1h'],
  ] as const)('selects the %s bucket size for its window', (window, bucket) => {
    expect(dashboardBucketForWindow(window)).toBe(bucket)
  })

  it('zero-fills the full window and includes one series for every room', () => {
    const buckets: TimeseriesBucket[] = [
      {
        room_id: 'room-1',
        bucket: new Date(now - 5 * 60 * 1000).toISOString(),
        count: 2,
      },
    ]
    const data = buildDashboardChartData(
      { buckets, rooms: [room('room-1'), room('room-2')], window: '1h', bucket: '5m' },
      'en-US',
      (roomId) => `series-${roomId}`,
      now,
    )

    expect(data.labels).toHaveLength(13)
    expect(data.datasets).toHaveLength(2)
    expect(data.datasets[0]?.data).toHaveLength(13)
    expect(data.datasets[0]?.data[11]).toBe(2)
    expect(data.datasets[0]?.data[12]).toBe(0)
    expect(data.datasets[1]?.data).toEqual(Array<number>(13).fill(0))
  })

  it('keeps room colors tied to room ids when the window changes', () => {
    const colorForRoom = (roomId: string) => `stable-${roomId}`
    const rooms = [room('room-1')]
    const oneHour = buildDashboardChartData(
      { buckets: [], rooms, window: '1h', bucket: '5m' },
      'en-US',
      colorForRoom,
      now,
    )
    const sixHours = buildDashboardChartData(
      { buckets: [], rooms, window: '6h', bucket: '15m' },
      'en-US',
      colorForRoom,
      now,
    )

    expect(oneHour.datasets[0]?.borderColor).toBe('stable-room-1')
    expect(sixHours.datasets[0]?.borderColor).toBe('stable-room-1')
    expect(oneHour.datasets[0]?.pointRadius).toBe(0)
    expect(oneHour.datasets[0]?.pointHoverRadius).toBe(3)
  })
})
