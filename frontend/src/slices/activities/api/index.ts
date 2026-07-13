// Thin use-case wrappers over the generated ActivitiesService (R24.13). Auth,
// silent 401 refresh, and problem+json -> typed ApiError all come from the
// shared axios instrumentation the generated client calls into — the host
// mediates every plugin call through here, so a plugin never touches the
// session or network directly (R30.17).

import { ActivitiesService } from '@shared/api-client'
import type {
  ActivitySessionOpenIn,
  ActivitySessionOut,
  ActivityActivationOut,
  ActivityActivationStartIn,
  ActivitySubmissionIn,
  ActivitySubmissionOut,
  ActivitySubmissionsPageOut,
  ActivityTypeOut,
} from '@shared/api-client'

export async function listActivityTypes(projectId: string): Promise<ActivityTypeOut[]> {
  return ActivitiesService.listActivityTypesApiProjectsProjectIdActivityTypesGet({ projectId })
}

export async function startActivation(
  chatroomId: string,
  body: ActivityActivationStartIn,
): Promise<ActivityActivationOut> {
  return ActivitiesService.startActivityActivationApiChatroomsChatroomIdActivityActivationsPost({
    chatroomId,
    requestBody: body,
  })
}

export async function endActivation(
  chatroomId: string,
  activationId: string,
): Promise<ActivityActivationOut> {
  return ActivitiesService.endActivityActivationApiChatroomsChatroomIdActivityActivationsActivationIdEndPatch({
    chatroomId,
    activationId,
  })
}

export async function getActiveActivation(chatroomId: string): Promise<ActivityActivationOut | null> {
  return ActivitiesService.getActiveActivityActivationApiChatroomsChatroomIdActivityActivationsActiveGet({
    chatroomId,
  })
}

export async function openActivitySession(
  chatroomId: string,
  body: ActivitySessionOpenIn,
): Promise<ActivitySessionOut> {
  return ActivitiesService.openActivitySessionApiChatroomsChatroomIdActivitySessionsPost({
    chatroomId,
    requestBody: body,
  })
}

export async function closeActivitySession(
  chatroomId: string,
  sessionId: string,
): Promise<ActivitySessionOut> {
  return ActivitiesService.closeActivitySessionApiChatroomsChatroomIdActivitySessionsSessionIdClosePatch(
    { chatroomId, sessionId },
  )
}

export async function submitActivity(
  chatroomId: string,
  body: ActivitySubmissionIn,
): Promise<ActivitySubmissionOut> {
  return ActivitiesService.submitActivityApiChatroomsChatroomIdActivitySubmissionsPost({
    chatroomId,
    requestBody: body,
  })
}

export interface ListSubmissionsParams {
  sessionId?: string | null
  subjectUserId?: string | null
  limit?: number
  offset?: number
}

export async function listActivitySubmissions(
  chatroomId: string,
  params: ListSubmissionsParams = {},
): Promise<ActivitySubmissionsPageOut> {
  return ActivitiesService.listActivitySubmissionsApiChatroomsChatroomIdActivitySubmissionsGet({
    chatroomId,
    ...params,
  })
}
