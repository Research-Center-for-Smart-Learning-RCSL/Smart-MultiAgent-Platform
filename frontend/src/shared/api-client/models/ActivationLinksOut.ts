/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The two links an Admin hands over (R6.18).
 *
 * Both are bearer credentials and the set-password one is equivalent to the
 * password itself, so this model is returned only by the two mint endpoints and
 * never by a read path.
 */
export type ActivationLinksOut = {
    set_password_url: string;
    verify_email_url: string;
    set_password_expires_at: string;
    verify_email_expires_at: string;
};

