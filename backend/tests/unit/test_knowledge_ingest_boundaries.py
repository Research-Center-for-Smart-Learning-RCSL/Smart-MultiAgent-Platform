from __future__ import annotations

import ast
from pathlib import Path

_APPLICATION_DIR = Path(__file__).parents[2] / "contexts" / "knowledge" / "application"
_MODULES = (
    "ingest_service.py",
    "knowmap_ingest_service.py",
    "rag_tus_finalizer.py",
    "knowmap_tus_finalizer.py",
)


def test_ingest_application_modules_do_not_import_knowledge_infrastructure() -> None:
    violations: list[str] = []
    for filename in _MODULES:
        tree = ast.parse((_APPLICATION_DIR / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("contexts.knowledge.infrastructure"):
                    violations.append(f"{filename}:{node.lineno}:{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("contexts.knowledge.infrastructure"):
                        violations.append(f"{filename}:{node.lineno}:{alias.name}")

    assert violations == []


def test_ingest_application_modules_do_not_construct_adapters() -> None:
    forbidden_suffixes = ("Repository", "Store", "Client")
    violations: list[str] = []
    for filename in _MODULES:
        tree = ast.parse((_APPLICATION_DIR / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id.endswith(forbidden_suffixes):
                violations.append(f"{filename}:{node.lineno}:{node.func.id}")

    assert violations == []
