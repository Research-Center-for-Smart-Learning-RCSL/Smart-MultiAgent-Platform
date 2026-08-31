/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Which list governs.
 *
 * ``ALLOW`` admits only listed domains, so an empty allow list is a legal
 * deny-all. ``DENY`` refuses only listed domains, so an empty deny list is a
 * legal allow-all. ``OFF`` applies no restriction and may retain dormant lists
 * an operator intends to re-enable.
 */
export type EmailDomainPolicyMode = 'allow' | 'deny' | 'off';
