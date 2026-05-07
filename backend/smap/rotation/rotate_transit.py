"""Rotate `smap-provider-secret` and rewrap every DEK (D.10 / §7.6).

Flow:

1. Read the current latest version of the Transit key from Vault.
2. `transit/keys/smap-provider-secret/rotate` → new version becomes latest.
3. Resume / start a `rewrap_progress` row per target table.
4. Walk the target in `(id ASC)` chunks; for each row whose
   `transit_key_version != target`, call `transit/rewrap/...` and write back
   the new `dek_wrapped` + `transit_key_version`. Plaintext DEK never
   leaves Vault.
5. Commit per chunk so a kill mid-run keeps the checkpoint usable.

Idempotent: re-running after a completed rotation becomes a no-op because
the version filter picks up nothing to rewrap.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import Settings, get_settings
from contexts.keys.infrastructure import tables as t
from shared_kernel.infra.vault import VaultClient, _parse_transit_version

_CHUNK_SIZE = 200
_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RotationSummary:
    target_version: int
    api_keys_rewrapped: int
    search_keys_rewrapped: int


async def _latest_transit_version(client: VaultClient) -> int:
    resp = client._call(  # — operator tool reuses internal call path
        client._client.secrets.transit.read_key,
        name=client._cfg.transit_key_provider,
    )
    return int(resp["data"]["latest_version"])


async def _rotate_transit(client: VaultClient) -> None:
    client._call(
        client._client.secrets.transit.rotate_key,
        name=client._cfg.transit_key_provider,
    )


async def _rewrap_table(
    db: AsyncSession,
    client: VaultClient,
    *,
    table: sa.Table,
    target_version: int,
) -> int:
    """Walk `table` and rewrap every DEK that is not at `target_version`.

    Returns the number of rows rewrapped. Idempotent: on re-run after a
    completed rotation the WHERE clause returns zero rows.
    """
    table_name = table.name

    # Upsert a progress row so crash-resume can tell which table we're on.
    await db.execute(
        pg_insert(t.rewrap_progress)
        .values(
            table_name=table_name,
            last_id=None,
            target_transit_version=target_version,
            rows_rewrapped=0,
        )
        .on_conflict_do_update(
            index_elements=["table_name"],
            set_={
                "target_transit_version": target_version,
                "completed_at": None,
            },
        )
    )

    progress_row = (
        await db.execute(t.rewrap_progress.select().where(t.rewrap_progress.c.table_name == table_name))
    ).one()
    last_id = progress_row.last_id
    total_rewrapped = int(progress_row.rows_rewrapped)

    await db.commit()

    while True:
        where = sa.and_(
            table.c.transit_key_version != target_version,
            table.c.deleted_at.is_(None),
        )
        if last_id is not None:
            where = sa.and_(where, table.c.id > last_id)

        rows = (
            await db.execute(
                sa.select(table.c.id, table.c.dek_wrapped)
                .where(where)
                .order_by(table.c.id.asc())
                .limit(_CHUNK_SIZE)
            )
        ).all()
        if not rows:
            break

        for row in rows:
            new_wrapped = client.rewrap_dek(row.dek_wrapped)
            new_version = _parse_transit_version(new_wrapped)
            await db.execute(
                table.update()
                .where(table.c.id == row.id)
                .values(
                    dek_wrapped=new_wrapped,
                    transit_key_version=new_version,
                )
            )
            total_rewrapped += 1
            last_id = row.id

        await db.execute(
            t.rewrap_progress.update()
            .where(t.rewrap_progress.c.table_name == table_name)
            .values(last_id=last_id, rows_rewrapped=total_rewrapped)
        )
        await db.commit()
        _log.info("rewrapped %d rows in %s", total_rewrapped, table_name)

    await db.execute(
        t.rewrap_progress.update()
        .where(t.rewrap_progress.c.table_name == table_name)
        .values(completed_at=sa.func.now())
    )
    await db.commit()
    return total_rewrapped


async def _run(settings: Settings) -> RotationSummary:
    client = VaultClient(settings.vault)

    await _rotate_transit(client)
    target_version = await _latest_transit_version(client)
    _log.info("Transit rotated; target version = v%d", target_version)

    engine = create_async_engine(settings.database.dsn)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            api_done = await _rewrap_table(db, client, table=t.api_keys, target_version=target_version)
            search_done = await _rewrap_table(db, client, table=t.search_keys, target_version=target_version)
    finally:
        await engine.dispose()

    return RotationSummary(
        target_version=target_version,
        api_keys_rewrapped=api_done,
        search_keys_rewrapped=search_done,
    )


def run(settings: Settings | None = None) -> RotationSummary:
    """Synchronous entry point used by the Typer command."""
    return asyncio.run(_run(settings or get_settings()))


__all__ = ["RotationSummary", "run"]
