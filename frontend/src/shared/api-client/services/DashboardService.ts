/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BucketSize } from '../models/BucketSize';
import type { DashboardAlertsOut } from '../models/DashboardAlertsOut';
import type { DashboardCollaborationOut } from '../models/DashboardCollaborationOut';
import type { DashboardSummaryOut } from '../models/DashboardSummaryOut';
import type { DashboardTimeseriesOut } from '../models/DashboardTimeseriesOut';
import type { DashboardWatchlistOut } from '../models/DashboardWatchlistOut';
import type { TimeWindow } from '../models/TimeWindow';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DashboardService {
    /**
     * Dashboard Alerts
     * Analyst alerts from the rooms the caller created ([R34.03]).
     *
     * An alert is an observation, readable only by its room's creator ([R28.03]), so
     * a member who may open a room still sees none of its alerts here.
     * @returns DashboardAlertsOut Successful Response
     * @throws ApiError
     */
    public static dashboardAlertsApiV1WorkspacesWorkspaceIdDashboardAlertsGet({
        workspaceId,
        limit = 50,
        offset,
    }: {
        workspaceId: string,
        limit?: number,
        offset?: number,
    }): CancelablePromise<DashboardAlertsOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/workspaces/{workspace_id}/dashboard/alerts',
            path: {
                'workspace_id': workspaceId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Dashboard Collaboration
     * The collaboration log of the rooms the caller created ([R34.03], [R34.06]).
     *
     * Notices and held drafts are as confidential as observations ([R28.03]): only
     * the room's creator reads them, whoever else may open the room.
     * @returns DashboardCollaborationOut Successful Response
     * @throws ApiError
     */
    public static dashboardCollaborationApiV1WorkspacesWorkspaceIdDashboardCollaborationGet({
        workspaceId,
        limit = 50,
        offset,
    }: {
        workspaceId: string,
        limit?: number,
        offset?: number,
    }): CancelablePromise<DashboardCollaborationOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/workspaces/{workspace_id}/dashboard/collaboration',
            path: {
                'workspace_id': workspaceId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
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
