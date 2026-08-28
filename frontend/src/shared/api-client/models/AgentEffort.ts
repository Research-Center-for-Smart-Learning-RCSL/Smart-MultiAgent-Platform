/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Cross-provider reasoning-effort level, mapped per-provider at the
 * adapter boundary (Claude ``output_config.effort`` / OpenAI
 * ``reasoning_effort`` / Gemini ``thinkingConfig.thinkingLevel``). ``None``
 * on an agent means the parameter is not sent and the provider's own default
 * applies.
 *
 * The union of every value any provider accepts (Q-3, R9.03a) — which subset
 * a given model accepts is a capability-table field
 * (``model_specs.ChatModelSpec.effort_values``), not an enum concern. A
 * value a model does not accept is never sent (see the adapters); the form
 * only offers a value the selected model's spec lists.
 */
export type AgentEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
