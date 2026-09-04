/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityTypePublicOut } from './ActivityTypePublicOut';
export type ActivityActivationOut = {
    id: string;
    chatroom_id: string;
    activity_type_id: string;
    started_by_user_id: string;
    status: string;
    created_at: (string | null);
    ended_at: (string | null);
    activity_type?: (ActivityTypePublicOut | null);
    started_by_agent_id?: (string | null);
    started_by_agent_name?: (string | null);
};

