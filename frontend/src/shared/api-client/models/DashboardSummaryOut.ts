/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DashboardThresholdsOut } from './DashboardThresholdsOut';
import type { RoomSummaryOut } from './RoomSummaryOut';
export type DashboardSummaryOut = {
    generated_at: string;
    rooms: Array<RoomSummaryOut>;
    thresholds: DashboardThresholdsOut;
};

