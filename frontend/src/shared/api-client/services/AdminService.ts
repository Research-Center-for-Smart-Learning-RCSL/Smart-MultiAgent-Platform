/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivationLinksOut } from '../models/ActivationLinksOut';
import type { AdminActivityActivationOut } from '../models/AdminActivityActivationOut';
import type { AdminActivityExampleOut } from '../models/AdminActivityExampleOut';
import type { AdminActivityPolicyIn } from '../models/AdminActivityPolicyIn';
import type { AdminActivityPolicyOut } from '../models/AdminActivityPolicyOut';
import type { AdminActivityTypeOut } from '../models/AdminActivityTypeOut';
import type { AdminEntryOut } from '../models/AdminEntryOut';
import type { AdminInstallReportOut } from '../models/AdminInstallReportOut';
import type { AdminPlatformActivityTypeIn } from '../models/AdminPlatformActivityTypeIn';
import type { AdminPolicyImpactOut } from '../models/AdminPolicyImpactOut';
import type { AdminPromoteIn } from '../models/AdminPromoteIn';
import type { AuditPageOut } from '../models/AuditPageOut';
import type { BanIn } from '../models/BanIn';
import type { EmailDomainPolicyIn } from '../models/EmailDomainPolicyIn';
import type { EmailDomainPolicyOut } from '../models/EmailDomainPolicyOut';
import type { ForceTransferIn } from '../models/ForceTransferIn';
import type { ImpersonateOut } from '../models/ImpersonateOut';
import type { IpBanIn } from '../models/IpBanIn';
import type { IpBanOut } from '../models/IpBanOut';
import type { MetricsOut } from '../models/MetricsOut';
import type { OrgSummaryOut } from '../models/OrgSummaryOut';
import type { ProjectSummaryOut } from '../models/ProjectSummaryOut';
import type { ProvisionedUserOut } from '../models/ProvisionedUserOut';
import type { RateLimitPatchIn } from '../models/RateLimitPatchIn';
import type { RateLimitPolicyOut } from '../models/RateLimitPolicyOut';
import type { RestoreOut } from '../models/RestoreOut';
import type { UserCreateIn } from '../models/UserCreateIn';
import type { UserDetailOut } from '../models/UserDetailOut';
import type { UserSummaryOut } from '../models/UserSummaryOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AdminService {
    /**
     * List All Active Activations
     * Every currently-active activation across every room, newest first.
     *
     * Answers "which classrooms are running what right now", which is why the room
     * and type names are hydrated rather than left as bare ids.
     * @returns AdminActivityActivationOut Successful Response
     * @throws ApiError
     */
    public static listAllActiveActivationsApiAdminActivityActivationsGet({
        cursor,
        limit = 50,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<AdminActivityActivationOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/activity-activations',
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Activity Examples
     * The shipped example catalogue and its install state ([R30.32]).
     * @returns AdminActivityExampleOut Successful Response
     * @throws ApiError
     */
    public static listActivityExamplesApiAdminActivityExamplesGet(): CancelablePromise<Array<AdminActivityExampleOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/activity-examples',
        });
    }
    /**
     * Install Activity Example
     * Install a shipped course as platform-scoped types ([R30.32]).
     *
     * Idempotent by key, so a re-run after a partial failure is safe. `course_key`
     * is a client-supplied path segment, which is what makes the loader's anchored
     * traversal guard load-bearing here rather than merely tidy — it now bounds a
     * network-reachable path.
     * @returns AdminInstallReportOut Successful Response
     * @throws ApiError
     */
    public static installActivityExampleApiAdminActivityExamplesCourseKeyInstallPost({
        courseKey,
    }: {
        courseKey: string,
    }): CancelablePromise<AdminInstallReportOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/activity-examples/{course_key}/install',
            path: {
                'course_key': courseKey,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Activity Policy
     * The policy in force, or the permissive default when none is saved.
     * @returns AdminActivityPolicyOut Successful Response
     * @throws ApiError
     */
    public static getActivityPolicyApiAdminActivityPolicyGet(): CancelablePromise<AdminActivityPolicyOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/activity-policy',
        });
    }
    /**
     * Put Activity Policy
     * Create or replace the platform policy.
     *
     * ``If-Match`` carries the version the admin's form was built against and is
     * required once a policy exists; without it a concurrent edit would be silently
     * overwritten. A non-integer header is rejected as a mismatch rather than
     * ignored — treating an unparseable precondition as "no precondition" would
     * defeat the point.
     * @returns AdminActivityPolicyOut Successful Response
     * @throws ApiError
     */
    public static putActivityPolicyApiAdminActivityPolicyPut({
        requestBody,
        ifMatch,
    }: {
        requestBody: AdminActivityPolicyIn,
        ifMatch?: (string | null),
    }): CancelablePromise<AdminActivityPolicyOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/admin/activity-policy',
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Preview Activity Policy Impact
     * Count the live types a candidate policy would block, without saving it.
     *
     * POST rather than GET because the candidate policy is a body, not an identity;
     * it writes nothing.
     * @returns AdminPolicyImpactOut Successful Response
     * @throws ApiError
     */
    public static previewActivityPolicyImpactApiAdminActivityPolicyImpactPost({
        requestBody,
    }: {
        requestBody: AdminActivityPolicyIn,
    }): CancelablePromise<AdminPolicyImpactOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/activity-policy/impact',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List All Activity Types
     * Every live activity type across every project, newest first.
     * @returns AdminActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static listAllActivityTypesApiAdminActivityTypesGet({
        cursor,
        limit = 50,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<AdminActivityTypeOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/activity-types',
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Platform Activity Type
     * Remove an installed example ([R30.32]).
     *
     * The cascade legitimately spans every tenant: the type is going away for
     * everyone, so every active activation ends, every open session closes, and
     * every project's opt-in is revoked. Durable-commit before the fan-out, so no
     * room is told its activation ended before it is.
     *
     * Re-installing the course mints a new type id, so every project that had
     * enabled this example must enable it again — the revocation is not undone.
     * @returns void
     * @throws ApiError
     */
    public static deletePlatformActivityTypeApiAdminActivityTypesTypeIdDelete({
        typeId,
    }: {
        typeId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/admin/activity-types/{type_id}',
            path: {
                'type_id': typeId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Update Platform Activity Type
     * Edit an installed example's safe and governance fields ([R30.23]).
     *
     * Platform-scoped rows only: a project's own type stays the project owner's, and
     * a project-scoped target 404s rather than being edited from here ([R30.31]).
     * @returns AdminActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static updatePlatformActivityTypeApiAdminActivityTypesTypeIdPatch({
        typeId,
        requestBody,
    }: {
        typeId: string,
        requestBody: AdminPlatformActivityTypeIn,
    }): CancelablePromise<AdminActivityTypeOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/admin/activity-types/{type_id}',
            path: {
                'type_id': typeId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Email Domain Policy
     * The policy in force, readable in every rollout phase.
     *
     * Readable while writes are fenced on purpose: an operator mid-rollout needs
     * to see what is stored precisely because they cannot change it.
     * @returns EmailDomainPolicyOut Successful Response
     * @throws ApiError
     */
    public static getEmailDomainPolicyApiAdminEmailDomainPolicyGet(): CancelablePromise<EmailDomainPolicyOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/email-domain-policy',
        });
    }
    /**
     * Put Email Domain Policy
     * Replace the policy. Permitted only while the rollout state is `active`.
     *
     * ``If-Match`` carries the version the Admin's form was built against and is
     * required: the row always exists by the time this route is reachable (the
     * startup import creates it), so there is no "first write" case that could
     * legitimately omit it. A missing or unparseable precondition is a mismatch
     * rather than "no precondition" — treating an unreadable header as permission
     * to overwrite would defeat the point of having one.
     * @returns EmailDomainPolicyOut Successful Response
     * @throws ApiError
     */
    public static putEmailDomainPolicyApiAdminEmailDomainPolicyPut({
        requestBody,
        ifMatch,
    }: {
        requestBody: EmailDomainPolicyIn,
        ifMatch?: (string | null),
    }): CancelablePromise<EmailDomainPolicyOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/admin/email-domain-policy',
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Platform Activity Types
     * Every live platform-scoped activity type, newest first ([R30.32]).
     *
     * Deliberately unbounded, unlike its cross-project sibling above. That one grows
     * with every tenant's authoring and so must be keyset-paginated; this population
     * is bounded by deliberate admin installs, which is the rationale
     * ``ActivityTypeRepository.list_platform`` already documents.
     *
     * It exists because the shipped-examples section needs the **stored** row for a
     * type it offers to edit, and the paged cross-project listing cannot be relied on
     * to contain it: platform examples are installed at setup, so they are the oldest
     * rows and the first to age off a newest-first page. Resolving from there left an
     * Edit action that opened a blank form and silently saved nothing.
     *
     * ``project_name`` is always None here: a platform type has no owning project by
     * construction, so there is nothing to look up.
     * @returns AdminActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static listPlatformActivityTypesApiAdminPlatformActivityTypesGet(): CancelablePromise<Array<AdminActivityTypeOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/platform-activity-types',
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
        resourceType: 'user' | 'org' | 'project' | 'agent' | 'workflow' | 'chatroom',
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create User
     * Provision an account without the holder present (R6.18).
     *
     * The 409 on an address that already has a live account is not an
     * account-existence oracle: this route is admin-only and an Admin already holds
     * `USER_READ_ANY`, so the same fact is one `GET /api/admin/users?q=` away.
     * @returns ProvisionedUserOut Successful Response
     * @throws ApiError
     */
    public static createUserApiAdminUsersPost({
        requestBody,
    }: {
        requestBody: UserCreateIn,
    }): CancelablePromise<ProvisionedUserOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Reissue Activation Links
     * Re-mint both activation links for an account that still needs them (R6.18).
     * @returns ActivationLinksOut Successful Response
     * @throws ApiError
     */
    public static reissueActivationLinksApiAdminUsersUserIdActivationLinksPost({
        userId,
    }: {
        userId: string,
    }): CancelablePromise<ActivationLinksOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/users/{user_id}/activation-links',
            path: {
                'user_id': userId,
            },
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
}
