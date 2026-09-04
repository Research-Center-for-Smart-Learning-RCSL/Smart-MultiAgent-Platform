/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssistantConfigOut } from '../models/AssistantConfigOut';
import type { AssistantConfigPutIn } from '../models/AssistantConfigPutIn';
import type { AssistantSessionOut } from '../models/AssistantSessionOut';
import type { Body_admin_upload_file_api_admin_prompt_assistant_config_files_post } from '../models/Body_admin_upload_file_api_admin_prompt_assistant_config_files_post';
import type { Body_me_upload_file_api_me_prompt_assistant_config_files_post } from '../models/Body_me_upload_file_api_me_prompt_assistant_config_files_post';
import type { Body_org_upload_file_api_orgs__org_id__prompt_assistant_config_files_post } from '../models/Body_org_upload_file_api_orgs__org_id__prompt_assistant_config_files_post';
import type { ConfigEnvelopeOut } from '../models/ConfigEnvelopeOut';
import type { FileOut } from '../models/FileOut';
import type { MessageIn } from '../models/MessageIn';
import type { ResolvedAssistantOut } from '../models/ResolvedAssistantOut';
import type { SessionCreatedOut } from '../models/SessionCreatedOut';
import type { TemplateCreateIn } from '../models/TemplateCreateIn';
import type { TemplateOut } from '../models/TemplateOut';
import type { TemplatePatchIn } from '../models/TemplatePatchIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PromptStudioService {
    /**
     * Me Get Config
     * @returns ConfigEnvelopeOut Successful Response
     * @throws ApiError
     */
    public static meGetConfigApiMePromptAssistantConfigGet(): CancelablePromise<ConfigEnvelopeOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/me/prompt-assistant/config',
        });
    }
    /**
     * Me Put Config
     * @returns AssistantConfigOut Successful Response
     * @throws ApiError
     */
    public static mePutConfigApiMePromptAssistantConfigPut({
        requestBody,
        ifMatch,
    }: {
        requestBody: AssistantConfigPutIn,
        ifMatch?: (string | null),
    }): CancelablePromise<AssistantConfigOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/me/prompt-assistant/config',
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Me Upload File
     * @returns FileOut Successful Response
     * @throws ApiError
     */
    public static meUploadFileApiMePromptAssistantConfigFilesPost({
        formData,
    }: {
        formData: Body_me_upload_file_api_me_prompt_assistant_config_files_post,
    }): CancelablePromise<FileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/me/prompt-assistant/config/files',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Me Delete File
     * @returns void
     * @throws ApiError
     */
    public static meDeleteFileApiMePromptAssistantConfigFilesFileIdDelete({
        fileId,
    }: {
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/me/prompt-assistant/config/files/{file_id}',
            path: {
                'file_id': fileId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Me List Templates
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static meListTemplatesApiMePromptTemplatesGet(): CancelablePromise<Array<TemplateOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/me/prompt-templates',
        });
    }
    /**
     * Me Create Template
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static meCreateTemplateApiMePromptTemplatesPost({
        requestBody,
    }: {
        requestBody: TemplateCreateIn,
    }): CancelablePromise<TemplateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/me/prompt-templates',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Me Patch Template
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static mePatchTemplateApiMePromptTemplatesTemplateIdPatch({
        templateId,
        ifMatch,
        requestBody,
    }: {
        templateId: string,
        ifMatch: string,
        requestBody: TemplatePatchIn,
    }): CancelablePromise<TemplateOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/me/prompt-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Me Delete Template
     * @returns void
     * @throws ApiError
     */
    public static meDeleteTemplateApiMePromptTemplatesTemplateIdDelete({
        templateId,
    }: {
        templateId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/me/prompt-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Get Config
     * @returns ConfigEnvelopeOut Successful Response
     * @throws ApiError
     */
    public static orgGetConfigApiOrgsOrgIdPromptAssistantConfigGet({
        orgId,
    }: {
        orgId: string,
    }): CancelablePromise<ConfigEnvelopeOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/prompt-assistant/config',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Put Config
     * @returns AssistantConfigOut Successful Response
     * @throws ApiError
     */
    public static orgPutConfigApiOrgsOrgIdPromptAssistantConfigPut({
        orgId,
        requestBody,
        ifMatch,
    }: {
        orgId: string,
        requestBody: AssistantConfigPutIn,
        ifMatch?: (string | null),
    }): CancelablePromise<AssistantConfigOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/orgs/{org_id}/prompt-assistant/config',
            path: {
                'org_id': orgId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Upload File
     * @returns FileOut Successful Response
     * @throws ApiError
     */
    public static orgUploadFileApiOrgsOrgIdPromptAssistantConfigFilesPost({
        orgId,
        formData,
    }: {
        orgId: string,
        formData: Body_org_upload_file_api_orgs__org_id__prompt_assistant_config_files_post,
    }): CancelablePromise<FileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/prompt-assistant/config/files',
            path: {
                'org_id': orgId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Delete File
     * @returns void
     * @throws ApiError
     */
    public static orgDeleteFileApiOrgsOrgIdPromptAssistantConfigFilesFileIdDelete({
        orgId,
        fileId,
    }: {
        orgId: string,
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/orgs/{org_id}/prompt-assistant/config/files/{file_id}',
            path: {
                'org_id': orgId,
                'file_id': fileId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org List Templates
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static orgListTemplatesApiOrgsOrgIdPromptTemplatesGet({
        orgId,
    }: {
        orgId: string,
    }): CancelablePromise<Array<TemplateOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/prompt-templates',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Create Template
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static orgCreateTemplateApiOrgsOrgIdPromptTemplatesPost({
        orgId,
        requestBody,
    }: {
        orgId: string,
        requestBody: TemplateCreateIn,
    }): CancelablePromise<TemplateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/prompt-templates',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Patch Template
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static orgPatchTemplateApiOrgsOrgIdPromptTemplatesTemplateIdPatch({
        orgId,
        templateId,
        ifMatch,
        requestBody,
    }: {
        orgId: string,
        templateId: string,
        ifMatch: string,
        requestBody: TemplatePatchIn,
    }): CancelablePromise<TemplateOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/orgs/{org_id}/prompt-templates/{template_id}',
            path: {
                'org_id': orgId,
                'template_id': templateId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Delete Template
     * @returns void
     * @throws ApiError
     */
    public static orgDeleteTemplateApiOrgsOrgIdPromptTemplatesTemplateIdDelete({
        orgId,
        templateId,
    }: {
        orgId: string,
        templateId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/orgs/{org_id}/prompt-templates/{template_id}',
            path: {
                'org_id': orgId,
                'template_id': templateId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Get Config
     * @returns ConfigEnvelopeOut Successful Response
     * @throws ApiError
     */
    public static adminGetConfigApiAdminPromptAssistantConfigGet(): CancelablePromise<ConfigEnvelopeOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/prompt-assistant/config',
        });
    }
    /**
     * Admin Put Config
     * @returns AssistantConfigOut Successful Response
     * @throws ApiError
     */
    public static adminPutConfigApiAdminPromptAssistantConfigPut({
        requestBody,
        ifMatch,
    }: {
        requestBody: AssistantConfigPutIn,
        ifMatch?: (string | null),
    }): CancelablePromise<AssistantConfigOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/admin/prompt-assistant/config',
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Upload File
     * @returns FileOut Successful Response
     * @throws ApiError
     */
    public static adminUploadFileApiAdminPromptAssistantConfigFilesPost({
        formData,
    }: {
        formData: Body_admin_upload_file_api_admin_prompt_assistant_config_files_post,
    }): CancelablePromise<FileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/prompt-assistant/config/files',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Delete File
     * @returns void
     * @throws ApiError
     */
    public static adminDeleteFileApiAdminPromptAssistantConfigFilesFileIdDelete({
        fileId,
    }: {
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/admin/prompt-assistant/config/files/{file_id}',
            path: {
                'file_id': fileId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin List Templates
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static adminListTemplatesApiAdminPromptTemplatesGet(): CancelablePromise<Array<TemplateOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/prompt-templates',
        });
    }
    /**
     * Admin Create Template
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static adminCreateTemplateApiAdminPromptTemplatesPost({
        requestBody,
    }: {
        requestBody: TemplateCreateIn,
    }): CancelablePromise<TemplateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/prompt-templates',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Patch Template
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static adminPatchTemplateApiAdminPromptTemplatesTemplateIdPatch({
        templateId,
        ifMatch,
        requestBody,
    }: {
        templateId: string,
        ifMatch: string,
        requestBody: TemplatePatchIn,
    }): CancelablePromise<TemplateOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/admin/prompt-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Delete Template
     * @returns void
     * @throws ApiError
     */
    public static adminDeleteTemplateApiAdminPromptTemplatesTemplateIdDelete({
        templateId,
    }: {
        templateId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/admin/prompt-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Resolved Assistant
     * @returns ResolvedAssistantOut Successful Response
     * @throws ApiError
     */
    public static projectResolvedAssistantApiProjectsProjectIdPromptAssistantGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ResolvedAssistantOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/prompt-assistant',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Merged Templates
     * @returns TemplateOut Successful Response
     * @throws ApiError
     */
    public static projectMergedTemplatesApiProjectsProjectIdPromptTemplatesGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<TemplateOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/prompt-templates',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Session
     * @returns SessionCreatedOut Successful Response
     * @throws ApiError
     */
    public static createSessionApiProjectsProjectIdPromptAssistantSessionsPost({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<SessionCreatedOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/prompt-assistant/sessions',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Session
     * @returns AssistantSessionOut Successful Response
     * @throws ApiError
     */
    public static getSessionApiPromptAssistantSessionsSessionIdGet({
        sessionId,
    }: {
        sessionId: string,
    }): CancelablePromise<AssistantSessionOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/prompt-assistant/sessions/{session_id}',
            path: {
                'session_id': sessionId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Post Message
     * @returns any Successful Response
     * @throws ApiError
     */
    public static postMessageApiPromptAssistantSessionsSessionIdMessagesPost({
        sessionId,
        requestBody,
    }: {
        sessionId: string,
        requestBody: MessageIn,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/prompt-assistant/sessions/{session_id}/messages',
            path: {
                'session_id': sessionId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
