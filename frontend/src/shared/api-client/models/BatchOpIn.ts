/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BatchUpdateItem } from './BatchUpdateItem';
import type { CanvasObjectIn } from './CanvasObjectIn';
export type BatchOpIn = {
    creates?: (Array<CanvasObjectIn> | null);
    updates?: (Array<BatchUpdateItem> | null);
    deletes?: (Array<string> | null);
};

