/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExamplePackAgentOut } from './ExamplePackAgentOut';
export type ExamplePackOut = {
    pack_key: string;
    title: string;
    source: string;
    for_course: string;
    group_name: string;
    agents: Array<ExamplePackAgentOut>;
    fully_installed: boolean;
};

