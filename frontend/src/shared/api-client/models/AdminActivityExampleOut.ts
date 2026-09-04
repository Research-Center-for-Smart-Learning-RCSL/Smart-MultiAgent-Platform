/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AdminCatalogueTypeOut } from './AdminCatalogueTypeOut';
/**
 * One shipped course, annotated with what is already installed ([R30.32]).
 */
export type AdminActivityExampleOut = {
    course_key: string;
    title: string;
    source: string;
    activity_types: Array<AdminCatalogueTypeOut>;
    fully_installed: boolean;
};

