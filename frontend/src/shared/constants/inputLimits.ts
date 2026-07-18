/**
 * Central input length limits — single source of truth for `maxlength`
 * attributes and character counters across the app.
 *
 * These MUST stay in sync with the backend Pydantic `max_length` bounds
 * (see `backend/app/api/v1/*` and `backend/shared_kernel/validation.py`).
 * The backend is the security boundary; these values only drive the UI hard
 * cap and the visual counter so a user is told *before* the request is
 * rejected with a 422.
 */
export const INPUT_LIMITS = {
  /** Chat message body (`content_md`). */
  MESSAGE: 100_000,
  /** Agent system prompt (`agents.system_prompt`; backend `app/api/v1/agents.py`). */
  SYSTEM_PROMPT: 100_000,
  /**
   * The prompt-assistant *config's* system prompt — a different field from
   * `SYSTEM_PROMPT` above, with a different backend bound. Mirrors
   * `backend/contexts/prompt_studio/domain/models.py` (`SYSTEM_PROMPT_MAX`), enforced in
   * `app/api/v1/prompt_studio.py`. Do not collapse the two: promising 100 000 here would
   * counter up to a value the backend 422s at 20 000, which is the drift this file exists
   * to prevent. `CONFIG_TEXT` is the same number for an unrelated concept — also not a
   * substitute.
   */
  PROMPT_ASSISTANT_SYSTEM_PROMPT: 20_000,
  /** Generic resource name (agent, chatroom, workspace, org, project, workflow, RAG config, key group). */
  NAME: 200,
  /** Provider model id / embedding model name. */
  MODEL_ID: 200,
  /** API key display name. */
  KEY_NAME: 200,
  /** API key secret token. */
  KEY_SECRET: 4096,
  /** Email address (matches RFC max + backend EmailStr). */
  EMAIL: 254,
  /** Account password (matches backend `Field(max_length=1024)`). */
  PASSWORD: 1_024,
  /** User display name. */
  DISPLAY_NAME: 50,
  /** Guest display name. */
  GUEST_NAME: 100,
  /** MCP server reference (url or package). */
  MCP_REFERENCE: 2_000,
  /** Function-tool name. */
  FUNCTION_NAME: 64,
  /** Function-tool description. */
  FUNCTION_DESCRIPTION: 1_000,
  /** HTTP(S) endpoint URL. */
  URL: 2_000,
  /**
   * Free-form config / workflow text (instruction templates, expressions).
   * Kept well under the backend JSON-body cap (~1 MiB) so a single field can
   * never approach it.
   */
  CONFIG_TEXT: 20_000,
  /**
   * Agent compact-mode compaction ceiling (`agents.context_token_cap`). Mirrors
   * `MAX_CONTEXT_TOKEN_CAP` in `backend/contexts/agents/domain/models.py` — the widest
   * provider context window (currently Gemini's 1 000 000). Above it the value cannot
   * help any provider and, in compact mode, silently disables compaction.
   */
  CONTEXT_TOKEN_CAP: 1_000_000,
} as const

export type InputLimitKey = keyof typeof INPUT_LIMITS

/**
 * Threshold at which a character counter should switch to a "warning" tone,
 * and to a "danger" tone — expressed as a fraction of the max. Mirrors the
 * existing system-prompt counter (warn at 90%, danger at 99%).
 */
export const CHAR_COUNT_WARN_RATIO = 0.9
export const CHAR_COUNT_DANGER_RATIO = 0.99
