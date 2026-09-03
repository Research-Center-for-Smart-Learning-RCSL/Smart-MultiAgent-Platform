/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An agent reduced to what a label needs.
 *
 * `AgentOut` carries `system_prompt`, bounded at 100k characters. Every caller
 * that only wanted a name paid for that: the chatroom builds an id-to-name map
 * from this collection on every room open, so a project near the pagination
 * ceiling turned opening a room into a multi-megabyte response for two fields
 * per row.
 */
export type AgentNameOut = {
    id: string;
    name: string;
};

