"""Is an exception a platform fault rather than a domain outcome?

One definition, because two layers act on the answer and they must not drift: the
agent tool registry uses it to decide whether a failure is reportable to the model
or has to fail the turn, and the built-in tools use it to decide whether their own
broad ``except`` may swallow what it caught.

The distinction is not cosmetic. A domain failure is a fact the model can act on —
a different tool, different arguments, an apology. An infrastructure fault is not:
the model cannot route around it, and on the turn's shared session it has already
made every later write fail, so continuing buys provider tokens for a reply that
can no longer be persisted.

Lives under ``db`` because every member of the set is a database fault today. It
imports ``sqlalchemy.exc`` and nothing else, so it stays importable from anywhere
including ``alembic/env.py``.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError


def is_infrastructure_error(exc: BaseException) -> bool:
    return isinstance(exc, SQLAlchemyError)


__all__ = ["is_infrastructure_error"]
