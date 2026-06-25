/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AdminEntryOut } from '../models/AdminEntryOut';
import type { AdminPromoteIn } from '../models/AdminPromoteIn';
import type { AuditPageOut } from '../models/AuditPageOut';
import type { BanIn } from '../models/BanIn';
import type { ForceTransferIn } from '../models/ForceTransferIn';
import type { ImpersonateOut } from '../models/ImpersonateOut';
import type { IpBanIn } from '../models/IpBanIn';
import type { IpBanOut } from '../models/IpBanOut';
import type { MetricsOut } from '../models/MetricsOut';
import type { OrgSummaryOut } from '../models/OrgSummaryOut';
import type { ProjectSummaryOut } from '../models/ProjectSummaryOut';
import type { RateLimitPatchIn } from '../models/RateLimitPatchIn';
import type { RateLimitPolicyOut } from '../models/RateLimitPolicyOut';
import type { RestoreOut } from '../models/RestoreOut';
import type { UserDetailOut } from '../models/UserDetailOut';
import type { UserSummaryOut } from '../models/UserSummaryOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AdminService {
    /**
     * List Admins
     * @returns AdminEntryOut Successful Response
     * @throws ApiError
     */
    public static listAdminsApiAdminAdminsGet(): CancelablePromise<Array<AdminEntryOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/admins',
        });
    }
    /**
     * Promote Admin
     * @returns AdminEntryOut Successful Response
     * @throws ApiError
     */
    public static promoteAdminApiAdminAdminsPost({
        requestBody,
    }: {
        requestBody: AdminPromoteIn,
    }): CancelablePromise<AdminEntryOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/admins',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Demote Admin
     * @returns void
     * @throws ApiError
     */
    public static demoteAdminApiAdminAdminsUserIdDelete({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/admin/admins/{user_id}',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Query Audit
     * @returns AuditPageOut Successful Response
     * @throws ApiError
     */
    public static queryAuditApiAdminAuditGet({
        actorUserId,
        resourceType,
        resourceId,
        action,
        from,
        to,
        ipPrefix,
        sessionId,
        requestId,
        cursor,
        limit = 50,
    }: {
        actorUserId?: (string | null),
        resourceType?: (string | null),
        resourceId?: (string | null),
        action?: (string | null),
        from?: (string | null),
        to?: (string | null),
        ipPrefix?: (string | null),
        sessionId?: (string | null),
        requestId?: (string | null),
        cursor?: (number | null),
        limit?: number,
    }): CancelablePromise<AuditPageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/audit',
            query: {
                'actor_user_id': actorUserId,
                'resource_type': resourceType,
                'resource_id': resourceId,
                'action': action,
                'from': from,
                'to': to,
                'ip_prefix': ipPrefix,
                'session_id': sessionId,
                'request_id': requestId,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Audit
     * Kick off audit CSV export -> MinIO `exports/` bucket.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static exportAuditApiAdminAuditExportPost({
        actorUserId,
        resourceType,
        resourceId,
        action,
        from,
        to,
        ipPrefix,
        sessionId,
        requestId,
    }: {
        actorUserId?: (string | null),
        resourceType?: (string | null),
        resourceId?: (string | null),
        action?: (string | null),
        from?: (string | null),
        to?: (string | null),
        ipPrefix?: (string | null),
        sessionId?: (string | null),
        requestId?: (string | null),
    }): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/audit/export',
            query: {
                'actor_user_id': actorUserId,
                'resource_type': resourceType,
                'resource_id': resourceId,
                'action': action,
                'from': from,
                'to': to,
                'ip_prefix': ipPrefix,
                'session_id': sessionId,
                'request_id': requestId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Bans
     * @returns IpBanOut Successful Response
     * @throws ApiError
     */
    public static listBansApiAdminIpBansGet(): CancelablePromise<Array<IpBanOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/ip-bans',
        });
    }
    /**
     * Add Ban
     * @returns IpBanOut Successful Response
     * @throws ApiError
     */
    public static addBanApiAdminIpBansPost({
        requestBody,
    }: {
        requestBody: IpBanIn,
    }): CancelablePromise<IpBanOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/ip-bans',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Ban
     * @returns void
     * @throws ApiError
     */
    public static removeBanApiAdminIpBansBanIdDelete({
        banId,
    }: {
        banId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/admin/ip-bans/{ban_id}',
            path: {
                'ban_id': banId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Admin Metrics
     * @returns MetricsOut Successful Response
     * @throws ApiError
     */
    public static adminMetricsApiAdminMetricsGet(): CancelablePromise<MetricsOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/metrics',
        });
    }
    /**
     * List Orgs
     * @returns OrgSummaryOut Successful Response
     * @throws ApiError
     */
    public static listOrgsApiAdminOrgsGet({
        cursor,
        limit = 50,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<OrgSummaryOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/orgs',
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Force Delete Org
     * @returns void
     * @throws ApiError
     */
    public static forceDeleteOrgApiAdminOrgsOrgIdForceDeletePost({
        orgId,
    }: {
        orgId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/orgs/{org_id}/force-delete',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Force Transfer Oc
     * @returns any Successful Response
     * @throws ApiError
     */
    public static forceTransferOcApiAdminOrgsOrgIdForceTransferOriginalCreatorPost({
        orgId,
        requestBody,
    }: {
        orgId: string,
        requestBody: ForceTransferIn,
    }): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/orgs/{org_id}/force-transfer-original-creator',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Projects
     * @returns ProjectSummaryOut Successful Response
     * @throws ApiError
     */
    public static listProjectsApiAdminProjectsGet({
        cursor,
        limit = 50,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<ProjectSummaryOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/projects',
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Rate Limits
     * @returns RateLimitPolicyOut Successful Response
     * @throws ApiError
     */
    public static listRateLimitsApiAdminRateLimitsGet({
        limit = 100,
        offset,
    }: {
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<RateLimitPolicyOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/rate-limits',
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Rate Limit
     * @returns RateLimitPolicyOut Successful Response
     * @throws ApiError
     */
    public static patchRateLimitApiAdminRateLimitsKeyPatch({
        key,
        requestBody,
    }: {
        key: string,
        requestBody: RateLimitPatchIn,
    }): CancelablePromise<RateLimitPolicyOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/admin/rate-limits/{key}',
            path: {
                'key': key,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Restore Resource
     * @returns RestoreOut Successful Response
     * @throws ApiError
     */
    public static restoreResourceApiAdminRestoreResourceTypeResourceIdPost({
        resourceType,
        resourceId,
    }: {
        resourceType: 'user' | 'org' | 'project',
        resourceId: string,
    }): CancelablePromise<RestoreOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/restore/{resource_type}/{resource_id}',
            path: {
                'resource_type': resourceType,
                'resource_id': resourceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Users
     * @returns UserSummaryOut Successful Response
     * @throws ApiError
     */
    public static listUsersApiAdminUsersGet({
        q,
        status,
        cursor,
        limit = 50,
    }: {
        q?: (string | null),
        status?: (string | null),
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<UserSummaryOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/users',
            query: {
                'q': q,
                'status': status,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get User
     * @returns UserDetailOut Successful Response
     * @throws ApiError
     */
    public static getUserApiAdminUsersUserIdGet({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<UserDetailOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/users/{user_id}',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Ban User
     * @returns void
     * @throws ApiError
     */
    public static banUserApiAdminUsersUserIdBanPost({
        userId,
        requestBody,
    }: {
        userId: string,
        requestBody: BanIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users/{user_id}/ban',
            path: {
                'user_id': userId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Soft Delete User
     * @returns void
     * @throws ApiError
     */
    public static softDeleteUserApiAdminUsersUserIdDeletePost({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users/{user_id}/delete',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * End Impersonate
     * @returns void
     * @throws ApiError
     */
    public static endImpersonateApiAdminUsersUserIdEndImpersonatePost({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users/{user_id}/end-impersonate',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Hard Delete User
     * @returns void
     * @throws ApiError
     */
    public static hardDeleteUserApiAdminUsersUserIdHardDeletePost({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users/{user_id}/hard-delete',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Impersonate
     * @returns ImpersonateOut Successful Response
     * @throws ApiError
     */
    public static impersonateApiAdminUsersUserIdImpersonatePost({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<ImpersonateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users/{user_id}/impersonate',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Unban User
     * @returns void
     * @throws ApiError
     */
    public static unbanUserApiAdminUsersUserIdUnbanPost({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users/{user_id}/unban',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
