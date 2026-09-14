/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UserStatus } from './UserStatus';
export type UserDetailOut = {
    id: string;
    email: string;
    display_name: (string | null);
    status: UserStatus;
    email_verified: boolean;
    is_admin: boolean;
    banned_reason: (string | null);
    banned_at: (string | null);
    deleted_at: (string | null);
    last_login_at: (string | null);
    created_at: string;
    org_ids: Array<string>;
    project_ids: Array<string>;
};

