/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Cross-provider reasoning-effort level. The common subset all three
 * providers accept; mapped per-provider at the adapter boundary
 * (Claude ``output_config.effort`` / OpenAI ``reasoning_effort`` /
 * Gemini ``thinkingConfig.thinkingLevel``). ``None`` on an agent means the
 * parameter is not sent and the provider's own default applies.
 */
export type AgentEffort = 'low' | 'medium' | 'high';
