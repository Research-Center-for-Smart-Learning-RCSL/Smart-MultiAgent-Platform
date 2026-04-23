"""Side-effect import registry — wires every Table into the shared metadata.

`alembic/env.py` imports this module so `metadata` contains every context's
tables on autogenerate. Runtime code doesn't need it (imports trigger the
side effect on their own) but importing it at app startup guarantees the
catalog is complete before the first query touches the DB.
"""

from __future__ import annotations

# Tables — side-effect imports.
from shared_kernel import audit as _audit  # noqa: F401
from contexts.agents.infrastructure import tables as _agents_tables  # noqa: F401
from contexts.agents.infrastructure import (  # noqa: F401
    mcp_tables as _agents_mcp_tables,
)
from contexts.conversation.infrastructure import (  # noqa: F401
    tables as _conversation_tables,
)
from contexts.identity.infrastructure import tables as _identity_tables  # noqa: F401
from contexts.keys.infrastructure import tables as _keys_tables  # noqa: F401
from contexts.knowledge.infrastructure import tables as _knowledge_tables  # noqa: F401
from contexts.knowledge.infrastructure import (  # noqa: F401
    graphrag_tables as _graphrag_tables,
)
from contexts.tenancy.infrastructure import tables as _tenancy_tables  # noqa: F401
from contexts.orchestration.infrastructure import tables as _orchestration_tables  # noqa: F401
from contexts.workflow.infrastructure import tables as _workflow_tables  # noqa: F401
from contexts.notification.infrastructure import tables as _notification_tables  # noqa: F401

__all__: list[str] = []
