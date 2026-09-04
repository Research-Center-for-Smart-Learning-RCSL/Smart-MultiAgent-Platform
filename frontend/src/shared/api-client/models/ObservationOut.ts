/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ObservationOut = {
    id: string;
    chatroom_id: string;
    agent_id: string;
    content_md: string;
    metadata: Record<string, any>;
    blocks: Array<Record<string, any>>;
    trigger: string;
    trigger_message_id: (string | null);
    released_at: (string | null);
    release_target: (Record<string, any> | null);
    released_by_user_id: (string | null);
    created_at: (string | null);
};

