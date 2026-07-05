"""prompt_studio bounded context — AI prompt assistant + prompt templates (§29).

Owns three aggregates: ``AssistantConfig`` and ``PromptTemplate`` (persisted,
scoped platform / org / user) and ``AssistantSession`` (ephemeral, Redis). It
calls the keys context router for LLM traffic and the tenancy facade for scope
resolution — the same dependency direction the knowledge context uses.
"""
