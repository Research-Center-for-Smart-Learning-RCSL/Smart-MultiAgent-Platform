/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { KnowmapGraphEdgeOut } from './KnowmapGraphEdgeOut';
import type { KnowmapGraphNodeOut } from './KnowmapGraphNodeOut';
export type KnowmapGraphOut = {
    config_id: string;
    nodes: Array<KnowmapGraphNodeOut>;
    edges: Array<KnowmapGraphEdgeOut>;
    truncated: boolean;
    build_state_blocked: boolean;
};

