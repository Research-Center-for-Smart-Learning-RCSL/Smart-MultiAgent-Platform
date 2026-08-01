from __future__ import annotations

from app.workers.main import (
    KnowledgeIngestWorkerSettings,
    KnowledgeScanWorkerSettings,
    WorkerSettings,
)
from shared_kernel.queue_names import KNOWLEDGE_INGEST_QUEUE, KNOWLEDGE_SCAN_QUEUE


def _function_names(settings: type) -> set[str]:
    return {getattr(fn, "name", None) or fn.__name__ for fn in settings.functions}


def test_heavy_knowledge_jobs_are_not_registered_on_the_general_worker() -> None:
    assert _function_names(WorkerSettings).isdisjoint(
        {
            "rag_ingest_document",
            "rag_scan_document",
            "knowmap_ingest_document",
            "knowmap_scan_document",
        },
    )


def test_scan_worker_is_bounded_and_isolated() -> None:
    assert KnowledgeScanWorkerSettings.queue_name == KNOWLEDGE_SCAN_QUEUE
    assert KnowledgeScanWorkerSettings.max_jobs == 2
    assert KnowledgeScanWorkerSettings.job_timeout == 20 * 60
    assert _function_names(KnowledgeScanWorkerSettings) == {
        "rag_scan_document",
        "knowmap_scan_document",
    }


def test_ingest_worker_is_single_job_and_isolated() -> None:
    assert KnowledgeIngestWorkerSettings.queue_name == KNOWLEDGE_INGEST_QUEUE
    assert KnowledgeIngestWorkerSettings.max_jobs == 1
    assert KnowledgeIngestWorkerSettings.job_timeout == 30 * 60
    assert _function_names(KnowledgeIngestWorkerSettings) == {
        "rag_ingest_document",
        "knowmap_ingest_document",
    }
