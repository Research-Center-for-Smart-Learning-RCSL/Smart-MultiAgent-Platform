"""Does the datastore preserve a payload schema's key order? ([R30.36], AC-4)

The generic activity form and the Mandala plugin both derive their field order
from the stored schema's object key order
(``frontend/src/slices/activities/components/schemaFields.ts::fieldsFromSchema``),
while ``activity_types.payload_schema`` is ``jsonb``
(``contexts/activities/infrastructure/tables.py``). PostgreSQL normalises a
``jsonb`` object's keys instead of preserving them, so the order an educator
writes in a course file is not the order a participant sees.

That is the premise the whole ``x-order`` mechanism rests on, and it is invisible
to the unit tier: nothing there round-trips through PostgreSQL, and a schema dict
built in Python keeps its insertion order regardless of what the database would
have done with it.

**If this file ever fails because the authored order came back intact, the fix is
not to relax the assertion.** It would mean ``x-order`` solves a problem that does
not exist, and the decision that introduced it (Q-8 of
``docs/tasks/2026-08-13-creative-thinking-example-agents/spec.md``) should be
revisited instead.

The key set is the shipped six-hats course type's, so the test pins the concrete
case the course correction had to work around rather than a synthetic one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.domain.models import ActivityTypeScope, ValidatorKind
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository

pytestmark = pytest.mark.db

# Declaration order of the six-hats worksheet: 事件, 白帽, 紅帽, 黑帽, 黃帽, 藍帽.
_AUTHORED_KEYS = ("event", "hat_white", "hat_red", "hat_black", "hat_yellow", "hat_blue")


def _jsonb_canonical(keys: tuple[str, ...]) -> tuple[str, ...]:
    """The order PostgreSQL stores these keys in: by UTF-8 length, then bytewise.

    Spelled out rather than hard-coded so the expectation reads as the rule it is
    testing. For this key set it yields event, hat_red, hat_blue, hat_black,
    hat_white, hat_yellow.
    """
    return tuple(sorted(keys, key=lambda k: (len(k.encode()), k.encode())))


def _schema(keys: tuple[str, ...], *, with_order: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for index, key in enumerate(keys, start=1):
        node: dict[str, Any] = {"type": "string", "title": key}
        if with_order:
            node["x-order"] = index
        properties[key] = node
    return {"type": "object", "properties": properties}


@pytest.fixture
async def platform_type_key(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    """A key unique to this run, cleaned up afterwards.

    Randomised for the same reason as in ``test_platform_activity_type_schema``:
    the platform key space is global, so a fixed key would let two concurrent runs
    fail each other in a way that looks exactly like the behaviour under test.
    """
    key = f"ci-keyorder-{uuid.uuid4().hex[:12]}"
    try:
        yield key
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(
                text("DELETE FROM activity_types WHERE project_id IS NULL AND key LIKE :k"),
                {"k": f"{key}%"},
            )
            await cleanup.commit()


async def _store(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    key: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Persist ``schema`` and return it as the database hands it back."""
    async with sessionmaker() as session:
        type_id = await ActivityTypeRepository(session).create(
            project_id=None,
            key=key,
            name=f"key order {key}",
            payload_schema=schema,
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config={"validator_id": "filled_count", "min_filled": 1},
            retention_days=None,
            expose_payload_to_agent=True,
            echo_includes_content=False,
            scope=ActivityTypeScope.PLATFORM,
        )
        await session.commit()

    # A fresh session, so the answer cannot come from an identity-mapped instance
    # still holding the dict this process built.
    async with sessionmaker() as session:
        stored = await ActivityTypeRepository(session).get(type_id)

    assert stored is not None
    return dict(stored.payload_schema)


@pytest.mark.asyncio
async def test_jsonb_does_not_return_properties_in_the_authored_order(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    stored = await _store(
        sessionmaker,
        key=platform_type_key,
        schema=_schema(_AUTHORED_KEYS),
    )
    stored_order = tuple(stored["properties"])

    assert stored_order == _jsonb_canonical(_AUTHORED_KEYS)
    assert stored_order != _AUTHORED_KEYS, (
        "payload_schema key order survived the round trip; x-order may be unnecessary. "
        "See this module's docstring before changing anything."
    )


@pytest.mark.asyncio
async def test_declared_x_order_survives_the_round_trip(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    """The other half: an order carried as a *value* is not what jsonb reorders.

    Without this the first test only says the old mechanism is broken; together
    they say the replacement works.
    """
    stored = await _store(
        sessionmaker,
        key=f"{platform_type_key}-ordered",
        schema=_schema(_AUTHORED_KEYS, with_order=True),
    )
    stored_order = tuple(stored["properties"])

    by_declared_order = tuple(
        name
        for name, _ in sorted(
            stored["properties"].items(),
            key=lambda item: item[1]["x-order"],
        )
    )
    assert by_declared_order == _AUTHORED_KEYS
    # And the key order really was scrambled, so the sort above did the work
    # rather than agreeing with an order that happened to survive.
    assert stored_order != _AUTHORED_KEYS
