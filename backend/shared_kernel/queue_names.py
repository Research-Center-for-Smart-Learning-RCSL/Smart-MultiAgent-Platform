"""Named Arq queues for isolated resource classes."""

KNOWLEDGE_INGEST_QUEUE = "arq:queue:knowledge_ingest"
KNOWLEDGE_SCAN_QUEUE = "arq:queue:knowledge_scan"

__all__ = ["KNOWLEDGE_INGEST_QUEUE", "KNOWLEDGE_SCAN_QUEUE"]
