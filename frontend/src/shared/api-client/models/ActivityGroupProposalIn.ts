/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * ``member_group_id`` is the chatroom id when ``group_kind`` is ``room``
 * ([R30.49]): the room group has no Member Group row.
 */
export type ActivityGroupProposalIn = {
    activity_type_id: string;
    group_kind?: 'member_group' | 'room';
    member_group_id: string;
    payload: Record<string, any>;
};

