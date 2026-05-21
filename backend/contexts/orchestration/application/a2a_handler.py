"""A2A inbox message handler (G.1).

The Phase H agent runtime — which will run a real agent turn for an inbound
CALL — does not exist yet. Until it does, the background consumer still needs
a handler so inbox streams are drained and synchronous callers do not block
for the full timeout:

- ``reply``    — handed to the reply rendezvous so a waiting ``call`` wakes.
- ``call``     — answered immediately with a structured error reply so the
                 caller fails fast instead of blocking until A2ATimeout.
- ``notify`` / ``instruct`` — logged and ACKed; there is no runtime to act
                 on them yet.

When the agent runtime lands, only the ``call`` branch changes: it will
dispatch a real turn and emit the genuine reply.
"""

from __future__ import annotations

import logging

from contexts.orchestration.domain.models import A2AEnvelope, A2AMessageType
from contexts.orchestration.infrastructure import a2a_rendezvous

logger = logging.getLogger(__name__)


async def handle_envelope(envelope: A2AEnvelope) -> None:
    """Consumer ``MessageHandler`` — dispatch one inbound A2A envelope."""
    if envelope.type is A2AMessageType.REPLY:
        # Route the reply to whichever synchronous `call` is waiting on it.
        await a2a_rendezvous.deliver_reply(envelope.correlation_id, envelope.to_dict())
        return

    if envelope.type is A2AMessageType.CALL:
        # No agent runtime yet — answer with a fail-fast error reply so the
        # caller's `call` raises immediately instead of blocking for 60-120 s.
        error_reply = {
            "type": A2AMessageType.REPLY.value,
            "from_agent": envelope.to_agent,
            "to_agent": str(envelope.from_agent),
            "correlation_id": str(envelope.correlation_id),
            "payload": {
                a2a_rendezvous.A2A_ERROR_KEY: "agent runtime unavailable",
                "detail": (
                    "A2A call received but no agent runtime is wired to "
                    "execute the target agent's turn"
                ),
            },
        }
        await a2a_rendezvous.deliver_reply(envelope.correlation_id, error_reply)
        logger.info(
            "a2a call %s for agent %s answered with degraded error reply",
            envelope.correlation_id,
            envelope.to_agent,
        )
        return

    # notify / instruct — fire-and-forget; nothing to act on until Phase H.
    logger.info(
        "a2a %s message %s for agent %s drained (no runtime to dispatch)",
        envelope.type.value,
        envelope.id,
        envelope.to_agent,
    )


__all__ = ["handle_envelope"]
