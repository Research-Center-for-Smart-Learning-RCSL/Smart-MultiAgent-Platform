/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Four scopes, not five.
 *
 * `user` was dropped during review (Q-26): a user is not a container, so no containment
 * predicate exists for it, and the natural implementation is an always-true branch that
 * lets a departed org member keep remotely-updatable code executing inside the org's
 * agents. Dropping it makes the containment predicate total over every scope. FU-14
 * tracks the containment-safe version.
 */
export type SkillScope = 'agent' | 'project' | 'org' | 'platform';
