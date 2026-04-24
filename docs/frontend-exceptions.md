# Frontend CI Gate Exceptions

Exceptions to the 12 SoC gates (§24.15) filed here with rationale.
Gates themselves cannot be silently disabled — every bypass must be listed.

## Gate #4: v-html allowlist

| File | Rationale |
|------|-----------|
| `slices/conversation/views/ChatroomView.vue` | Renders sanitized markdown via `renderMarkdown.ts` (DOMPurify). Single approved site per R24.41. |

## Gate #12: i18n bare-string allowlist

| Token | Rationale |
|-------|-----------|
| Provider names (OpenAI, Claude, etc.) | Brand names are not translatable. |
| Punctuation / symbols | Layout characters have no locale variant. |
| `shared/ui/` atoms | Design-system atoms use fixed labels; consumer views wrap with `$t()`. |
