/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EmailDomainPolicyMode } from './EmailDomainPolicyMode';
/**
 * A full replacement, not a patch.
 *
 * Bounds are declared here as well as in the domain normaliser so an oversized
 * body is rejected at the boundary rather than after being parsed and
 * normalised — the normaliser is the decision, this is the cost control.
 */
export type EmailDomainPolicyIn = {
    mode: EmailDomainPolicyMode;
    allow?: Array<string>;
    deny?: Array<string>;
};

