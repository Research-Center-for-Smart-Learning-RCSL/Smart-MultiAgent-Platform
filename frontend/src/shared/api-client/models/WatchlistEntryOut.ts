/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreativityMeansOut } from './CreativityMeansOut';
export type WatchlistEntryOut = {
    creativity?: (CreativityMeansOut | null);
    display_name: (string | null);
    kind: 'user' | 'guest' | 'group';
    last_valid_submission_at: (string | null);
    needs_attention: boolean;
    reason: ('no_valid_in_round' | 'idle' | null);
    room_id: string;
    subject_code: string;
    valid_count: number;
};

