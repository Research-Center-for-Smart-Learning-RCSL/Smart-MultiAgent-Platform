/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BucketSize } from '../models/BucketSize';
import type { DashboardSummaryOut } from '../models/DashboardSummaryOut';
import type { DashboardTimeseriesOut } from '../models/DashboardTimeseriesOut';
import type { DashboardWatchlistOut } from '../models/DashboardWatchlistOut';
import type { TimeWindow } from '../models/TimeWindow';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DashboardService {
    /**
     * Dashboard Summary
     * @returns DashboardSummaryOut Successful Response
     * @throws ApiError
     */
    public static dashboardSummaryApiV1WorkspacesWorkspaceIdDashboardSummaryGet({
        workspaceId,
    }: {
        workspaceId: string,
    }): CancelablePromise<DashboardSummaryOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/workspaces/{workspace_id}/dashboard/summary',
            path: {
                'workspace_id': workspaceId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Dashboard Timeseries
     * @returns DashboardTimeseriesOut Successful Response
     * @throws ApiError
     */
    public static dashboardTimeseriesApiV1WorkspacesWorkspaceIdDashboardTimeseriesGet({
        workspaceId,
        window = '1h',
        bucket = '5m',
    }: {
        workspaceId: string,
        window?: TimeWindow,
        bucket?: BucketSize,
    }): CancelablePromise<DashboardTimeseriesOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/workspaces/{workspace_id}/dashboard/timeseries',
            path: {
                'workspace_id': workspaceId,
            },
            query: {
                'window': window,
                'bucket': bucket,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Dashboard Watchlist
     * @returns DashboardWatchlistOut Successful Response
     * @throws ApiError
     */
    public static dashboardWatchlistApiV1WorkspacesWorkspaceIdDashboardWatchlistGet({
        workspaceId,
    }: {
        workspaceId: string,
    }): CancelablePromise<DashboardWatchlistOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/workspaces/{workspace_id}/dashboard/watchlist',
            path: {
                'workspace_id': workspaceId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
