/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WakeupBoolFieldOut } from './WakeupBoolFieldOut';
import type { WakeupCountFieldOut } from './WakeupCountFieldOut';
import type { WakeupIntFieldOut } from './WakeupIntFieldOut';
export type RoomWakeupOut = {
    agent_id: string;
    agent_may_toggle: Array<'every_n_messages' | 'silence_minutes'>;
    autostop_rounds: WakeupIntFieldOut;
    binding_id: string;
    call_only: boolean;
    count: WakeupCountFieldOut;
    every_n_messages: WakeupIntFieldOut;
    every_n_messages_enabled: WakeupBoolFieldOut;
    ignored_agent_fields: Array<string>;
    role: 'normal' | 'observer';
    runtime_refresh_at: (string | null);
    silence_minutes: WakeupIntFieldOut;
    silence_minutes_enabled: WakeupBoolFieldOut;
    version: number;
};

