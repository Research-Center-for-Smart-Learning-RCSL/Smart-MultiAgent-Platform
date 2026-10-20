import type { ChartData } from 'chart.js'
import type { BucketSize, TimeWindow } from '@shared/api-client'
import type { TimeseriesBucket, RoomSummary } from './types'

const windowMilliseconds: Record<TimeWindow, number> = {
  '1h': 60 * 60 * 1000,
  '6h': 6 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
}

const bucketMilliseconds: Record<BucketSize, number> = {
  '1m': 60 * 1000,
  '5m': 5 * 60 * 1000,
  '15m': 15 * 60 * 1000,
  '1h': 60 * 60 * 1000,
}

export function dashboardBucketForWindow(window: TimeWindow): BucketSize {
  if (window === '24h') return '1h'
  if (window === '6h') return '15m'
  return '5m'
}

export function buildDashboardChartData(
  input: {
    buckets: TimeseriesBucket[]
    rooms: RoomSummary[]
    window: TimeWindow
    bucket: BucketSize
  },
  locale: string,
  colorForRoom: (roomId: string) => string,
  now = Date.now(),
): ChartData<'line'> {
  const bucketMs = bucketMilliseconds[input.bucket]
  const windowMs = windowMilliseconds[input.window]
  const latest = Math.floor(now / bucketMs) * bucketMs
  const earliest = Math.floor((now - windowMs) / bucketMs) * bucketMs
  const timestamps: number[] = []
  for (let timestamp = earliest; timestamp <= latest; timestamp += bucketMs) {
    timestamps.push(timestamp)
  }

  const countsByRoom = new Map<string, Map<number, number>>()
  for (const bucket of input.buckets) {
    const timestamp = Math.floor(new Date(bucket.bucket).getTime() / bucketMs) * bucketMs
    if (!countsByRoom.has(bucket.room_id)) countsByRoom.set(bucket.room_id, new Map())
    countsByRoom.get(bucket.room_id)?.set(timestamp, bucket.count)
  }

  const datasets = input.rooms.map((room) => {
    const counts = countsByRoom.get(room.room_id)
    return {
      label: room.room_name,
      data: timestamps.map((timestamp) => counts?.get(timestamp) ?? 0),
      borderColor: colorForRoom(room.room_id),
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 0,
      pointHoverRadius: 3,
      borderWidth: 2,
    }
  })

  const dateOptions: Intl.DateTimeFormatOptions =
    input.window === '24h'
      ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
      : { hour: 'numeric', minute: '2-digit' }
  const formatter = new Intl.DateTimeFormat(locale, dateOptions)

  return {
    labels: timestamps.map((timestamp) => formatter.format(new Date(timestamp))),
    datasets,
  }
}
