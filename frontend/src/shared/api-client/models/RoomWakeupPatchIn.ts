/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RoomWakeupPatchIn = {
    autostop_rounds?: (number | null);
    binding_id: string;
    count?: ('all' | 'human' | null);
    every_n_messages?: (number | null);
    every_n_messages_enabled?: (boolean | null);
    expected_version: number;
    on_join_enabled?: (boolean | null);
    silence_minutes?: (number | null);
    silence_minutes_enabled?: (boolean | null);
};

