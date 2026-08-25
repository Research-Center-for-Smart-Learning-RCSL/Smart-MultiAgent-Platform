/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Grant or revoke one bound agent's live-draft reading ([R32.03]).
 *
 * One field, deliberately. Its sibling ``AgentActivityControlIn`` carries an
 * allowlist because activity control is authority over a *set* of worksheets the
 * teacher chooses; draft reading has no such set. What a granted agent may
 * actually read is decided per call by the activity type's own
 * ``expose_payload_to_agent`` and by the platform payload policy ([R32.04]) — the
 * same two gates a submitted payload passes. A list here would be a third gate to
 * keep in step with those two, and the state where they disagreed would be a draft
 * readable on looser terms than its own submission.
 */
export type AgentDraftAccessIn = {
    granted: boolean;
};

