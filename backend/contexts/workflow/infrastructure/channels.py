"""Workflow-context pub/sub channel builder."""

from __future__ import annotations

import uuid


def workflow_channel(run_id: uuid.UUID) -> str:
    return f"ws:wf:{run_id}"


__all__ = ["workflow_channel"]
