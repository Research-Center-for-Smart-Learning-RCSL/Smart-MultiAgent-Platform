/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MessageCountsOut } from './MessageCountsOut';
import type { RoomRoundOut } from './RoomRoundOut';
import type { TrafficLight } from './TrafficLight';
export type RoomSummaryOut = {
    last_valid_submission_at: (string | null);
    message_counts: MessageCountsOut;
    online_count: (number | null);
    participant_count: number;
    room_id: string;
    room_name: string;
    round: (RoomRoundOut | null);
    status: TrafficLight;
    total_submissions: number;
    valid_count: number;
};

