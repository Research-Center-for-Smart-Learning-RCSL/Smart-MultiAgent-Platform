export type {
  RoomSummaryOut as RoomSummary,
  DashboardSummaryOut as DashboardSummaryResponse,
  TimeseriesBucketOut as TimeseriesBucket,
  DashboardTimeseriesOut as DashboardTimeseriesResponse,
  WatchlistEntryOut as WatchlistEntry,
  DashboardWatchlistOut as DashboardWatchlistResponse,
  AlertOut as DashboardAlert,
  CollaborationEntryOut as NoticeEntry,
  PeerReviewEntryOut as PeerReviewEntry,
  TrafficLight,
  TimeWindow,
  BucketSize,
} from '@shared/api-client'

import type { CollaborationEntryOut, PeerReviewEntryOut } from '@shared/api-client'

export type CollaborationEntry = CollaborationEntryOut | PeerReviewEntryOut
