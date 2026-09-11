/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CanvasObjectKind } from './CanvasObjectKind';
export type CanvasSearchResult = {
    object_id: string;
    kind: CanvasObjectKind;
    content: (string | null);
    snippet: string;
    rank: number;
    position_x: number;
    position_y: number;
};

