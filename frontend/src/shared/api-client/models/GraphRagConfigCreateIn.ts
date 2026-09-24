/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GraphRagTriggerConfig } from './GraphRagTriggerConfig';
/**
 * Owner-centric create (Phase 2b WS2).
 *
 * ``owner_id`` references an existing owner of ``owner_kind`` in the project:
 * an ``agent_group`` (managed via the member-CRUD surface), a ``chatroom``, or
 * a ``workspace``.
 */
export type GraphRagConfigCreateIn = {
    owner_kind: 'agent_group' | 'chatroom' | 'workspace';
    owner_id: string;
    builder_key_group_id: string;
    trigger_config?: GraphRagTriggerConfig;
    recency_half_life_days?: (number | null);
};

