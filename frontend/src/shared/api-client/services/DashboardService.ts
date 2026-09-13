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
    public static dashboardSummaryApiV1ProjectsProjectIdDashboardSummaryGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<DashboardSummaryOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/projects/{project_id}/dashboard/summary',
            path: {
                'project_id': projectId,
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
    public static dashboardTimeseriesApiV1ProjectsProjectIdDashboardTimeseriesGet({
        projectId,
        window = '1h',
        bucket = '5m',
    }: {
        projectId: string,
        window?: TimeWindow,
        bucket?: BucketSize,
    }): CancelablePromise<DashboardTimeseriesOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/projects/{project_id}/dashboard/timeseries',
            path: {
                'project_id': projectId,
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
    public static dashboardWatchlistApiV1ProjectsProjectIdDashboardWatchlistGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<DashboardWatchlistOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/projects/{project_id}/dashboard/watchlist',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
