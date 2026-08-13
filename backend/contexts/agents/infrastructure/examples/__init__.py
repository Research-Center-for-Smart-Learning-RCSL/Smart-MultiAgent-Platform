"""Shipped example agent packs, as an adapter over packaged data ([R30.35]).

Lives in ``contexts/agents`` rather than beside the course catalogue because the
artifacts installed are agents: project-scoped rows with a key group, which is why
a pack copies into one project instead of installing platform-wide the way a
course does. An agent cannot be platform-scoped at all under BYO-key, since the
platform owns no provider key for one to reference.

``infrastructure/`` for the same reason the course catalogue is there: reading a
packaged resource is an adapter over an external store.
"""

from contexts.agents.infrastructure.examples.catalogue import (
    PACKS_DIRNAME,
    AgentPackDefinition,
    PackAgent,
    PackFileInvalid,
    available_packs,
    load_pack,
    packs_root,
    parse_pack,
)

__all__ = [
    "PACKS_DIRNAME",
    "AgentPackDefinition",
    "PackAgent",
    "PackFileInvalid",
    "available_packs",
    "load_pack",
    "packs_root",
    "parse_pack",
]
