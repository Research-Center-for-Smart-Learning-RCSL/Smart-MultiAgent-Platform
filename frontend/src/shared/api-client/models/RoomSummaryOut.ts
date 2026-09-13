/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TrafficLight } from './TrafficLight';
export type RoomSummaryOut = {
    last_submission_at: (string | null);
    room_id: string;
    room_name: string;
    status: TrafficLight;
    total_submissions: number;
    valid_count: number;
};

