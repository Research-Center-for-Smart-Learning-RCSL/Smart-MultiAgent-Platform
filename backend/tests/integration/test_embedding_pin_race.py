"""F-3/F-11 — the (project, kind) advisory lock under real concurrency (§8.3, AC-5).

Every unit test of this area fakes ``acquire_lock`` as a no-op, so none of them can
prove the property the design actually rests on: that two sessions racing a create
against a teardown serialize rather than interleave. ``pg_advisory_xact_lock`` needs a
real Postgres, so it is proven here.

The dangerous interleaving F-3 opens up is a teardown dropping a collection a
concurrent create has just started depending on. The lock is what prevents it: whoever
takes it second sees the other's committed effect and backs off.

Requires a Postgres reachable via ``settings.database.dsn`` with migrations applied --
the ``backend-integration`` CI job's environment.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.knowledge.domain.embedding_pin import PinKind, TeardownOutcome
from contexts.knowledge.infrastructure.embedding_pin_repository import EmbeddingPinRepository
from contexts.knowledge.infrastructure.embedding_pin_tables import project_embedding_pins as pins_t

# Real Postgres required (see module docstring) -- routed to the backend-db CI job.
pytestmark = pytest.mark.db

# `sessionmaker` and `project_id` fixtures come from tests/integration/conftest.py.


async def _seed_pin(session: AsyncSession, project_id: uuid.UUID, dim: int) -> None:
    await session.execute(
        pins_t.insert().values(
            project_id=project_id,
            kind=PinKind.FILE_RAG.value,
            provider="openai",
            model="text-embedding-3-small",
            dim=dim,
        )
    )
    await session.commit()


async def _read_pin_dim(session: AsyncSession, project_id: uuid.UUID) -> int | None:
    row = (
        await session.execute(
            pins_t.select().where(
                sa.and_(
                    pins_t.c.project_id == project_id,
                    pins_t.c.kind == PinKind.FILE_RAG.value,
                )
            )
        )
    ).first()
    return int(row.dim) if row is not None else None


def _draft(provider: str, model: str) -> Any:
    from contexts.knowledge.domain.models import RagConfigDraft

    return RagConfigDraft(
        name="pin-race",
        chunk_strategy=SimpleNamespace(value="fixed"),
        chunk_params={},
        embed_key_id=None,
        embed_provider=provider,
        embed_model=model,
        rerank_enabled=False,
        rerank_key_id=None,
        rerank_provider=None,
        rerank_model=None,
        top_k=5,
    )


def _service(session: AsyncSession, live: list[Any]) -> Any:
    """A real RagConfigService on a real session, with only the config repo faked.

    The pin repository is real -- it is the advisory lock under test. ``list_for_project``
    is faked because seeding real config rows would drag in project/org fixtures that say
    nothing about the lock.
    """
    from contexts.knowledge.application.config_service import RagConfigService

    svc = RagConfigService(db=session)
    svc._configs = AsyncMock()
    svc._configs.list_for_project.return_value = live
    return svc


def _patch_store(store: Any) -> Any:
    return (
        patch(
            "contexts.knowledge.infrastructure.qdrant_teardown.AsyncQdrantClient",
            return_value=AsyncMock(),
        ),
        patch("contexts.knowledge.infrastructure.qdrant_store.QdrantStore", return_value=store),
    )


@pytest.mark.asyncio
async def test_teardown_blocks_until_concurrent_create_commits(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    """AC-5: a teardown racing a create must not drop the live collection.

    Session A (the create) takes the lock and holds it. Session B (the teardown) must
    block on ``acquire_lock`` rather than proceed, then observe A's committed config
    and skip -- leaving the collection A depends on intact.
    """
    store = AsyncMock()
    store.delete_collection.return_value = True

    async with sessionmaker() as setup:
        await _seed_pin(setup, project_id, 1536)

    lock_taken = asyncio.Event()
    create_committed = asyncio.Event()
    teardown_entered = asyncio.Event()

    async def creating_session() -> None:
        # Session A: hold the lock, then commit a "live config" the teardown must see.
        async with sessionmaker() as sa_session:
            await EmbeddingPinRepository(sa_session).acquire_lock(project_id, PinKind.FILE_RAG)
            lock_taken.set()
            await asyncio.sleep(0.3)  # B is blocked on the lock for this whole window
            assert not teardown_entered.is_set(), "B entered the teardown while A held the lock"
            await sa_session.commit()
            create_committed.set()

    async def tearing_down_session() -> TeardownOutcome:
        await lock_taken.wait()  # deterministic: A owns the lock before B tries
        async with sessionmaker() as sb_session:
            svc = _service(sb_session, live=[object()])  # A's config, now visible
            p1, p2 = _patch_store(store)
            with p1, p2:
                outcome = await svc.teardown_orphan_collection(project_id=project_id)
            teardown_entered.set()
            await sb_session.commit()
            return outcome

    _, outcome = await asyncio.gather(creating_session(), tearing_down_session())

    assert create_committed.is_set(), "the teardown returned before the create committed"
    assert outcome is TeardownOutcome.SKIPPED_LIVE_CONFIG
    store.delete_collection.assert_not_awaited()  # the live collection survived

    async with sessionmaker() as check:
        assert await _read_pin_dim(check, project_id) == 1536  # pin still backs the collection


@pytest.mark.asyncio
async def test_configless_teardown_releases_pin_and_frees_new_dimension(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    """AC-5: with no live config, the teardown drops and releases through a real lock,
    so the project can then be pinned at a different dimension."""
    store = AsyncMock()
    store.delete_collection.return_value = True

    async with sessionmaker() as setup:
        await _seed_pin(setup, project_id, 1536)

    async with sessionmaker() as session:
        svc = _service(session, live=[])
        p1, p2 = _patch_store(store)
        with p1, p2:
            outcome = await svc.teardown_orphan_collection(project_id=project_id)
        await session.commit()
    assert outcome is TeardownOutcome.DROPPED

    async with sessionmaker() as check:
        assert await _read_pin_dim(check, project_id) is None

    # The dimension is now free: a 3072-dim ensure succeeds where it would have
    # raised against the retained pin.
    async with sessionmaker() as repin:
        await EmbeddingPinRepository(repin).ensure(
            project_id=project_id,
            kind=PinKind.FILE_RAG,
            provider="openai",
            model="text-embedding-3-large",
            dim=3072,
            on_conflict=lambda existing, this: AssertionError(f"unexpected conflict {existing}/{this}"),
        )
        await repin.commit()

    async with sessionmaker() as check:
        assert await _read_pin_dim(check, project_id) == 3072


@pytest.mark.asyncio
async def test_try_acquire_lock_reports_contention_across_sessions(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    """FU-3: the create-path retry skips when it loses the lock race, so the whole
    mitigation rests on ``try_acquire_lock`` actually failing while another *session*
    holds the key. A same-session test would pass on re-entrancy and prove nothing."""
    held = asyncio.Event()
    release = asyncio.Event()
    contended: bool | None = None

    async def holder() -> None:
        async with sessionmaker() as session:
            await EmbeddingPinRepository(session).acquire_lock(project_id, PinKind.FILE_RAG)
            held.set()
            await release.wait()
            await session.commit()  # transaction-scoped: the lock drops here

    async def contender() -> None:
        nonlocal contended
        await held.wait()
        async with sessionmaker() as session:
            contended = await EmbeddingPinRepository(session).try_acquire_lock(project_id, PinKind.FILE_RAG)
            await session.commit()
        release.set()

    await asyncio.gather(holder(), contender())
    assert contended is False, "try_acquire_lock must not hand out a key another session holds"

    # And once the holder is gone it is available again — otherwise the retry would
    # be skipped forever and the pin could never be reclaimed on the create path.
    async with sessionmaker() as session:
        assert await EmbeddingPinRepository(session).try_acquire_lock(project_id, PinKind.FILE_RAG)
        await session.commit()


@pytest.mark.asyncio
async def test_try_lock_is_reentrant_with_the_blocking_lock(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    """FU-3: the retry takes the key with `try`, then `teardown_orphan_collection` and
    `ensure` take the same key with the blocking call. If those did not stack, the
    create path would deadlock against itself."""
    async with sessionmaker() as session:
        repo = EmbeddingPinRepository(session)
        assert await repo.try_acquire_lock(project_id, PinKind.FILE_RAG)
        await repo.acquire_lock(project_id, PinKind.FILE_RAG)  # must not hang
        assert await repo.try_acquire_lock(project_id, PinKind.FILE_RAG)  # still ours
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_creates_make_one_qdrant_attempt_not_one_each(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    """FU-3: the amplification this fix exists to remove, measured.

    A project left in the FAILED-teardown state, Qdrant hanging, and N concurrent
    different-dimension creates. With the retry taking the lock by blocking, each
    waiter inherited the lock and repeated the same doomed Qdrant call: N attempts,
    and the last request waited N x timeout while holding a pooled connection. With
    `try`, the losers skip straight to `ensure` and block only on the one holder.

    Asserting the attempt count rather than wall-clock: the count is the mechanism,
    the seconds are the symptom.
    """
    from contexts.knowledge.domain.errors import EmbedDimensionConflict

    concurrency = 5
    timeout_s = 0.3
    attempts = 0

    async def _hang(_pid: uuid.UUID) -> bool:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(timeout_s * 20)  # never returns before the bound fires
        return True

    store = AsyncMock()
    store.delete_collection.side_effect = _hang
    settings = SimpleNamespace(
        qdrant=SimpleNamespace(url="http://unused", api_key="", teardown_timeout_s=timeout_s)
    )

    async with sessionmaker() as setup:
        await _seed_pin(setup, project_id, 1536)

    async def one_create() -> BaseException | None:
        async with sessionmaker() as session:
            svc = _service(session, live=[])
            try:
                await svc.create(
                    project_id=project_id,
                    draft=_draft("openai", "text-embedding-3-large"),  # 3072-dim
                    actor_user_id=uuid.uuid4(),
                    actor_ip=None,
                )
            except BaseException as exc:
                return exc
            finally:
                await session.rollback()
            return None

    with (
        patch(
            "contexts.knowledge.infrastructure.qdrant_teardown.AsyncQdrantClient",
            return_value=AsyncMock(),
        ),
        patch("app.config.settings.get_settings", return_value=settings),
        patch("contexts.knowledge.infrastructure.qdrant_store.QdrantStore", return_value=store),
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
    ):
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(*(one_create() for _ in range(concurrency)))
        elapsed = asyncio.get_running_loop().time() - started

    # Every create is still correctly rejected: Qdrant never confirmed the drop, so
    # the pin stands and the incompatible dimension stays refused. Fail-closed holds.
    assert all(isinstance(r, EmbedDimensionConflict) for r in results)

    assert attempts == 1, f"{concurrency} concurrent creates made {attempts} Qdrant attempts"
    # One timeout for everyone, not one each. Generous bound so this measures the
    # mechanism rather than the machine.
    assert elapsed < timeout_s * concurrency, f"took {elapsed:.2f}s; serialised retries would"

    async with sessionmaker() as check:
        assert await _read_pin_dim(check, project_id) == 1536


@pytest.mark.asyncio
async def test_list_all_returns_the_fields_the_sweep_reads(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    """AC-6: the sweep drives ``list_all`` and reads ``project_id``/``kind`` off each
    row. That SQL is mocked out of every unit test, so execute it once for real."""
    async with sessionmaker() as setup:
        await _seed_pin(setup, project_id, 1536)

    async with sessionmaker() as session:
        rows = await EmbeddingPinRepository(session).list_all()

    mine = [r for r in rows if r.project_id == project_id]
    assert len(mine) == 1
    assert mine[0].kind == PinKind.FILE_RAG.value  # the sweep round-trips this into PinKind
    assert PinKind(mine[0].kind) is PinKind.FILE_RAG
    assert int(mine[0].dim) == 1536


@pytest.mark.asyncio
async def test_failed_teardown_keeps_pin_rejecting_new_dimension(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> None:
    """AC-3/AC-5: the F-3 regression through a real lock and a real pin row -- a failed
    drop must leave the pin committed, so the incompatible dimension stays rejected."""
    from contexts.knowledge.domain.errors import EmbedDimensionConflict

    store = AsyncMock()
    store.delete_collection.side_effect = RuntimeError("qdrant unreachable")

    async with sessionmaker() as setup:
        await _seed_pin(setup, project_id, 1536)

    async with sessionmaker() as session:
        svc = _service(session, live=[])
        p1, p2 = _patch_store(store)
        with p1, p2:
            outcome = await svc.teardown_orphan_collection(project_id=project_id)
        await session.commit()
    assert outcome is TeardownOutcome.FAILED

    async with sessionmaker() as check:
        assert await _read_pin_dim(check, project_id) == 1536  # survived the commit

    async with sessionmaker() as repin:
        with pytest.raises(EmbedDimensionConflict):
            await EmbeddingPinRepository(repin).ensure(
                project_id=project_id,
                kind=PinKind.FILE_RAG,
                provider="openai",
                model="text-embedding-3-large",
                dim=3072,
                on_conflict=lambda existing, this: EmbedDimensionConflict(f"{existing} != {this}"),
            )
