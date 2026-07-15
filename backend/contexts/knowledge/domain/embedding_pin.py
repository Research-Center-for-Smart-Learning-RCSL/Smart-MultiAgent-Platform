"""Durable project embedding-pin domain types (F-11).

A per-``(project, kind)`` embedding pin that outlives any single config, so a
project's fixed-size Qdrant collection dimension survives config deletion and
cannot be changed by a concurrent first-create race. The three kinds map to the
three per-project collections: ``rag_{project_id}`` (file RAG),
``knowmap_{project_id}`` (Knowledge Map), and ``graphrag_{project_id}``
(Concept Map).
"""

from __future__ import annotations

import enum


class PinKind(str, enum.Enum):
    FILE_RAG = "file_rag"
    KNOWMAP = "knowmap"
    GRAPHRAG = "graphrag"


__all__ = ["PinKind"]
