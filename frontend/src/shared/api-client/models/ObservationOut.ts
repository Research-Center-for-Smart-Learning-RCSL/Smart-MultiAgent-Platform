/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ObservationOut = {
    agent_id: string;
    alert_reason?: (string | null);
    blocks: Array<Record<string, any>>;
    chatroom_id: string;
    content_md: string;
    created_at: (string | null);
    first_action_at?: (string | null);
    first_viewed_at?: (string | null);
    id: string;
    is_alert?: boolean;
    metadata: Record<string, any>;
    release_target: (Record<string, any> | null);
    released_at: (string | null);
    released_by_user_id: (string | null);
    trigger: string;
    trigger_message_id: (string | null);
};

