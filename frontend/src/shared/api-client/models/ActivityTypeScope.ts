/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Who owns an ``ActivityType`` ([R30.02]).
 *
 * ``PROJECT`` is the original and only pre-0076 case: the row belongs to one
 * project and ``project_id`` is set. ``PLATFORM`` is a shipped example a
 * platform admin installed: no owning project, reachable from a project only
 * through a ``ProjectActivityTypeOptIn`` row ([R30.33]).
 *
 * Deliberately two values, mirroring ``ActivityPolicy.scope`` — a per-org layer
 * would be a third value, which is a row-level concern rather than a rewrite.
 */
export type ActivityTypeScope = 'project' | 'platform';
