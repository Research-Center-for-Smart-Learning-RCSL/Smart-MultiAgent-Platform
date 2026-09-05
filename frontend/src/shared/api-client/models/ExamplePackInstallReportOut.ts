/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InstalledPackAgentOut } from './InstalledPackAgentOut';
/**
 * What one install created and what was already there.
 *
 * Both lists rather than a count: install is idempotent by agent name, so
 * "nothing created" is a normal, successful outcome the installer needs to see
 * the reason for.
 */
export type ExamplePackInstallReportOut = {
    pack_key: string;
    created: Array<InstalledPackAgentOut>;
    already_present: Array<string>;
    group_id: string;
    group_created: boolean;
};

