/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChangeEmailIn } from '../models/ChangeEmailIn';
import type { ChangePasswordIn } from '../models/ChangePasswordIn';
import type { LoginIn } from '../models/LoginIn';
import type { LogoutIn } from '../models/LogoutIn';
import type { PasswordResetIn } from '../models/PasswordResetIn';
import type { PasswordResetRequestIn } from '../models/PasswordResetRequestIn';
import type { RefreshIn } from '../models/RefreshIn';
import type { RegisterIn } from '../models/RegisterIn';
import type { SessionOut } from '../models/SessionOut';
import type { TokenPairOut } from '../models/TokenPairOut';
import type { UserOut } from '../models/UserOut';
import type { VerifyEmailIn } from '../models/VerifyEmailIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AuthService {
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
            },
        });
    }
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Sessions
     * @returns SessionOut Successful Response
     * @throws ApiError
     */
    public static listSessionsApiAuthSessionsGet(): CancelablePromise<Array<SessionOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/sessions',
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
                422: `Validation Error`,
            },
        });
    }
    /**
     * Verify Email Via Link
     * @returns string Successful Response
     * @throws ApiError
     */
    public static verifyEmailViaLinkApiAuthVerifyEmailGet({
        token,
    }: {
        token: string,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/auth/verify-email',
            query: {
                'token': token,
            },
            errors: {
                422: `Validation Error`,
            },
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
                422: `Validation Error`,
            },
        });
    }
}
