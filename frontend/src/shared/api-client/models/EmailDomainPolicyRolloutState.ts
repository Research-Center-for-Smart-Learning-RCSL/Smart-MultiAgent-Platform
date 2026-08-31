/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Which store a new reader must treat as authoritative.
 *
 * ``COMPATIBILITY`` — the legacy Redis triple still governs, because replicas
 * that know only those three keys may still be serving. Admin writes are fenced.
 *
 * ``ACTIVE`` — PostgreSQL governs, with a disposable Redis mirror in front of
 * it. The only phase in which an Admin may write.
 *
 * ``ROLLBACK_FROZEN`` — PostgreSQL governs and is frozen, while the legacy
 * triple is rewritten beneath it so old images can be started. Writes are
 * fenced so the verified mirror cannot go stale the moment it is taken.
 */
export type EmailDomainPolicyRolloutState = 'compatibility' | 'active' | 'rollback_frozen';
