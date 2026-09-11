/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One agent a pack would install, and whether this project already has it.
 */
export type ExamplePackAgentOut = {
    binds_activity_types: Array<string>;
    installed: boolean;
    key: string;
    may_control_activities: boolean;
    name: string;
    preferred_model_hint: string;
    room_role: ('normal' | 'observer' | null);
};

