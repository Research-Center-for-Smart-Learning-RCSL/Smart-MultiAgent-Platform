/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotificationKind } from './NotificationKind';
export type NotificationOut = {
    id: string;
    kind: NotificationKind;
    title: string;
    body: (string | null);
    metadata: Record<string, any>;
    read_at: (string | null);
    created_at: string;
};

