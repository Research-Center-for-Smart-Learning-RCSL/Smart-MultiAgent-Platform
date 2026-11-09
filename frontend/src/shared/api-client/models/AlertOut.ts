/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AlertOut = {
    agent_name: (string | null);
    alert_reason: (string | null);
    created_at: (string | null);
    first_action_at: (string | null);
    first_action_kind: ('released' | 'deleted' | 'room_message' | 'activity_started' | 'activity_ended' | null);
    first_viewed_at: (string | null);
    observation_id: string;
    room_id: string;
    room_name: string;
};

