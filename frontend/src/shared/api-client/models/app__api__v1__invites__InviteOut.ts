/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InviteScope } from './InviteScope';
import type { InviteState } from './InviteState';
export type app__api__v1__invites__InviteOut = {
    created_at: string;
    expires_at: string;
    id: string;
    invitee_email: string;
    role: 'owner' | 'member';
    scope_id: string;
    scope_name: string;
    scope_type: InviteScope;
    state: InviteState;
};

