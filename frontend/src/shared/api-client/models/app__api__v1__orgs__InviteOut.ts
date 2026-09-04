/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InviteScope } from './InviteScope';
import type { InviteState } from './InviteState';
export type app__api__v1__orgs__InviteOut = {
    id: string;
    scope_type: InviteScope;
    scope_id: string;
    invitee_email: string;
    role: 'owner' | 'member';
    state: InviteState;
    expires_at: string;
    accept_url?: (string | null);
};

