/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GraphEdgeOut } from './GraphEdgeOut';
import type { GraphNodeOut } from './GraphNodeOut';
/**
 * Bounded node/edge view for the knowledge-graph visualizer (viz P0).
 */
export type GraphOut = {
    config_id: string;
    edges: Array<GraphEdgeOut>;
    nodes: Array<GraphNodeOut>;
    truncated: boolean;
};

