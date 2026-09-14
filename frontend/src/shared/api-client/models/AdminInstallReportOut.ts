/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Which keys the install created and which were already there.
 *
 * Both lists rather than a count: an install is idempotent, so "nothing created"
 * is a normal, successful outcome and the admin needs to see why.
 */
export type AdminInstallReportOut = {
    already_present: Array<string>;
    course_key: string;
    created: Array<string>;
};

