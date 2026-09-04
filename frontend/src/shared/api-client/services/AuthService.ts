/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CaptchaConfigOut } from '../models/CaptchaConfigOut';
import type { ChangeEmailIn } from '../models/ChangeEmailIn';
import type { ChangePasswordIn } from '../models/ChangePasswordIn';
import type { DeleteAccountIn } from '../models/DeleteAccountIn';
import type { GoogleLinkStartOut } from '../models/GoogleLinkStartOut';
import type { IdentityOut } from '../models/IdentityOut';
import type { LoginIn } from '../models/LoginIn';
import type { LogoutIn } from '../models/LogoutIn';
import type { PasswordResetIn } from '../models/PasswordResetIn';
import type { PasswordResetRequestIn } from '../models/PasswordResetRequestIn';
import type { RefreshIn } from '../models/RefreshIn';
import type { RegisterIn } from '../models/RegisterIn';
import type { SessionOut } from '../models/SessionOut';
import type { SessionPolicyOut } from '../models/SessionPolicyOut';
import type { TokenPairOut } from '../models/TokenPairOut';
import type { UpdateProfileIn } from '../models/UpdateProfileIn';
import type { UserOut } from '../models/UserOut';
import type { VerifyEmailIn } from '../models/VerifyEmailIn';
import type { WsTicketOut } from '../models/WsTicketOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AuthService {
    /**
     * Register
     * @returns string Successful Response
     * @throws ApiError
     */
    public static registerApiAuthRegisterPost({
        requestBody,
    }: {
        requestBody: RegisterIn,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/register',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Captcha Config
     * Public CAPTCHA config for the registration widget (R19a.12).
     *
     * Unauthenticated by design — the SPA fetches this before the user has any
     * credentials. Returns only the public provider/sitekey/mode; the verify
     * secret never leaves the backend. When ``mode=off`` the SPA renders no widget.
     * @returns CaptchaConfigOut Successful Response
     * @throws ApiError
     */
    public static captchaConfigApiAuthCaptchaConfigGet(): CancelablePromise<CaptchaConfigOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/captcha-config',
        });
    }
    /**
     * Session Policy
     * Idle-timeout policy for the SPA's inactivity logout (R6.03-adjacent).
     *
     * Unauthenticated by design — it carries no secrets, only the two timing
     * values the client needs to drive its "are you still there?" countdown so the
     * warning UI and the server-enforced idle window share one source of truth.
     * @returns SessionPolicyOut Successful Response
     * @throws ApiError
     */
    public static sessionPolicyApiAuthSessionPolicyGet(): CancelablePromise<SessionPolicyOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/session-policy',
        });
    }
    /**
     * Verify Email
     * @returns string Successful Response
     * @throws ApiError
     */
    public static verifyEmailApiAuthVerifyEmailPost({
        requestBody,
    }: {
        requestBody: VerifyEmailIn,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/verify-email',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Login
     * @returns TokenPairOut Successful Response
     * @throws ApiError
     */
    public static loginApiAuthLoginPost({
        requestBody,
    }: {
        requestBody: LoginIn,
    }): CancelablePromise<TokenPairOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/login',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Refresh
     * @returns TokenPairOut Successful Response
     * @throws ApiError
     */
    public static refreshApiAuthRefreshPost({
        requestBody,
    }: {
        requestBody: RefreshIn,
    }): CancelablePromise<TokenPairOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/refresh',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Logout
     * @returns void
     * @throws ApiError
     */
    public static logoutApiAuthLogoutPost({
        requestBody,
    }: {
        requestBody: LogoutIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/logout',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Request Password Reset
     * @returns any Successful Response
     * @throws ApiError
     */
    public static requestPasswordResetApiAuthRequestPasswordResetPost({
        requestBody,
    }: {
        requestBody: PasswordResetRequestIn,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/request-password-reset',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Reset Password
     * @returns void
     * @throws ApiError
     */
    public static resetPasswordApiAuthResetPasswordPost({
        requestBody,
    }: {
        requestBody: PasswordResetIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/reset-password',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Change Password
     * @returns void
     * @throws ApiError
     */
    public static changePasswordApiAuthChangePasswordPost({
        requestBody,
    }: {
        requestBody: ChangePasswordIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/change-password',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Change Email
     * @returns void
     * @throws ApiError
     */
    public static changeEmailApiAuthChangeEmailPost({
        requestBody,
    }: {
        requestBody: ChangeEmailIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/change-email',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Me
     * @returns UserOut Successful Response
     * @throws ApiError
     */
    public static meApiAuthMeGet(): CancelablePromise<UserOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/me',
        });
    }
    /**
     * Delete Account
     * Self-service account deletion (R6.07 / R8.14 / R8.18).
     *
     * Re-confirms the current password (destructive, recovery-gated action), soft-
     * deletes the account + its tenancy footprint, then clears the refresh cookie.
     * Returns 409 (``tenancy/original-creator-self-delete-blocked`` with
     * ``blocked_org_ids``) when the caller is the Original Creator of an Org that
     * still has other active members.
     * @returns void
     * @throws ApiError
     */
    public static deleteAccountApiAuthMeDelete({
        requestBody,
    }: {
        requestBody: DeleteAccountIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/auth/me',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Update Me
     * @returns UserOut Successful Response
     * @throws ApiError
     */
    public static updateMeApiAuthMePatch({
        requestBody,
    }: {
        requestBody: UpdateProfileIn,
    }): CancelablePromise<UserOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/auth/me',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Sessions
     * @returns SessionOut Successful Response
     * @throws ApiError
     */
    public static listSessionsApiAuthSessionsGet({
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
    }): CancelablePromise<Array<SessionOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/sessions',
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
     * Revoke Session
     * @returns void
     * @throws ApiError
     */
    public static revokeSessionApiAuthSessionsSessionIdDelete({
        sessionId,
    }: {
        sessionId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/auth/sessions/{session_id}',
            path: {
                'session_id': sessionId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Issue Ws Ticket
     * Mint a short-lived, single-use ticket for a WebSocket handshake (FE-7).
     *
     * Browsers cannot set `Authorization` on a WS upgrade, so the credential
     * must ride in `Sec-WebSocket-Protocol` — a header proxies and access logs
     * record. Placing the JWT there leaks it; instead this endpoint (reached
     * over HTTPS, where the bearer token sits in the redacted `Authorization`
     * header) stashes the access token behind an opaque ticket the handshake
     * redeems exactly once. A ticket later found in a log is already consumed.
     *
     * The `current_principal` dependency gates the call; the raw token is read
     * straight off the header so the exact JWT the caller presented is what the
     * WS handshake later verifies.
     * @returns WsTicketOut Successful Response
     * @throws ApiError
     */
    public static issueWsTicketApiAuthWsTicketPost(): CancelablePromise<WsTicketOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/ws-ticket',
        });
    }
    /**
     * Google Authorize
     * Login-mode OAuth start: 302 to Google, with the single-use state stored
     * server-side and mirrored into the `smap_oauth_state` cookie (login-CSRF).
     * Unauthenticated (R19.01 exception).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static googleAuthorizeApiAuthGoogleAuthorizeGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/google/authorize',
        });
    }
    /**
     * Google Callback
     * Backend-handled callback: verify server-side, mint the session, set the
     * refresh cookie, and 302 back to the SPA. Errors end in a redirect with
     * `?oauth_error=`, never a JSON 4xx. Unauthenticated (R19.01 exception).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static googleCallbackApiAuthGoogleCallbackGet({
        state,
        code,
        error,
    }: {
        state: string,
        code?: (string | null),
        error?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/google/callback',
            query: {
                'state': state,
                'code': code,
                'error': error,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Google Link Start
     * Authenticated (XHR) link start: returns the Google authorize URL and sets
     * the state cookie; the SPA then navigates the browser to that URL. Bearer auth
     * works here because it is an XHR, not a top-level navigation.
     * @returns GoogleLinkStartOut Successful Response
     * @throws ApiError
     */
    public static googleLinkStartApiAuthGoogleLinkStartPost(): CancelablePromise<GoogleLinkStartOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/auth/google/link/start',
        });
    }
    /**
     * Google Unlink
     * @returns void
     * @throws ApiError
     */
    public static googleUnlinkApiAuthGoogleLinkDelete(): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/auth/google/link',
        });
    }
    /**
     * List Identities
     * @returns IdentityOut Successful Response
     * @throws ApiError
     */
    public static listIdentitiesApiAuthIdentitiesGet(): CancelablePromise<Array<IdentityOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/identities',
        });
    }
}
