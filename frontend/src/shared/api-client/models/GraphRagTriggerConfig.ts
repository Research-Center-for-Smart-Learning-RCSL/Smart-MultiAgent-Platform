/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Typed GraphRAG build-trigger config (audit M11).
 *
 * Replaces the previously untyped ``dict[str, object]`` that was persisted
 * verbatim as JSON. Unknown keys are rejected and numeric bounds are
 * enforced so arbitrary/oversized payloads can't be stored.
 */
export type GraphRagTriggerConfig = {
    every_n_messages?: (number | null);
    manual?: boolean;
    silence_minutes?: (number | null);
};

