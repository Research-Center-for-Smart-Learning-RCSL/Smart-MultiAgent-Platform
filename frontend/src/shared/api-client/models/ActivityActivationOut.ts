/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityTypePublicOut } from './ActivityTypePublicOut';
export type ActivityActivationOut = {
    activity_type?: (ActivityTypePublicOut | null);
    activity_type_id: string;
    chatroom_id: string;
    created_at: (string | null);
    ended_at: (string | null);
    id: string;
    started_by_user_id: string;
    status: string;
};

