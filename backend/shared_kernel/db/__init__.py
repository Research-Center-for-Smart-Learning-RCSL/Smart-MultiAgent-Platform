"""Shared SQLAlchemy metadata registry.

Every bounded context's ORM tables attach to **this** MetaData object so
Alembic sees the union. Contexts import `metadata` from here and declare
their own Table/DeclarativeBase bindings against it.

SoC guard: this module MUST NOT import any context; `alembic/env.py` relies
on being able to import it without loading the application graph. The
per-context table modules are registered via side-effect imports from
`shared_kernel.db.registry`, which Phase C introduces.
"""

from __future__ import annotations

from sqlalchemy import MetaData

# Postgres-friendly naming convention — matches operations.md §4.2 O4.04
# expectations that generated DDL is stable across autogenerates.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata: MetaData = MetaData(naming_convention=NAMING_CONVENTION)

__all__ = ["NAMING_CONVENTION", "metadata"]
