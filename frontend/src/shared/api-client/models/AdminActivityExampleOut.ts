/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AdminCatalogueTypeOut } from './AdminCatalogueTypeOut';
/**
 * One shipped course, annotated with what is already installed ([R30.32]).
 */
export type AdminActivityExampleOut = {
    activity_types: Array<AdminCatalogueTypeOut>;
    course_key: string;
    fully_installed: boolean;
    source: string;
    title: string;
};

