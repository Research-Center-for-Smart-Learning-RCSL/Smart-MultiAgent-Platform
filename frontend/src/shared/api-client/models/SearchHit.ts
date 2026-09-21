/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SenderType } from './SenderType';
export type SearchHit = {
    message_id: string;
    sender_type: SenderType;
    sender_id: (string | null);
    created_at: string;
    snippet: string;
    rank: number;
};

