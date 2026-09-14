/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One agent a pack would install, and whether this project already has it.
 */
export type ExamplePackAgentOut = {
    key: string;
    name: string;
    room_role: ('normal' | 'observer' | null);
    preferred_model_hint: string;
    binds_activity_types: Array<string>;
    may_control_activities: boolean;
    installed: boolean;
};

