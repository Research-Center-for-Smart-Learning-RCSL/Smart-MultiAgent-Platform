/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InviteScope } from './InviteScope';
import type { InviteState } from './InviteState';
export type app__api__v1__invites__InviteOut = {
    id: string;
    scope_type: InviteScope;
    scope_id: string;
    scope_name: string;
    role: 'owner' | 'member';
    invitee_email: string;
    state: InviteState;
    expires_at: string;
    created_at: string;
};

