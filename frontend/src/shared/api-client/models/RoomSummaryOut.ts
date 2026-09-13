/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TrafficLight } from './TrafficLight';
export type RoomSummaryOut = {
    room_id: string;
    room_name: string;
    total_submissions: number;
    valid_count: number;
    last_submission_at: (string | null);
    status: TrafficLight;
};

