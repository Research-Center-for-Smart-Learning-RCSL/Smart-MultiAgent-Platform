/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A group the caller may propose for, named so a picker can render it.
 *
 * Not a tenancy read: the caller already belongs to every group in this list,
 * so it discloses no grouping they could not see elsewhere, and it carries
 * nothing about who else is in one.
 */
export type ActivityMemberGroupRefOut = {
    id: string;
    name: string;
};

