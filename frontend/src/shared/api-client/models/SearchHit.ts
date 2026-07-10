/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SenderType } from './SenderType';
export type SearchHit = {
    created_at: string;
    message_id: string;
    rank: number;
    sender_id: (string | null);
    sender_type: SenderType;
    snippet: string;
};

