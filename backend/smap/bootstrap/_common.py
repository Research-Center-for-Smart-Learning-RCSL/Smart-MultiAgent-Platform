"""Shared report types for bootstrap subcommands.

Every subcommand returns a `BootstrapReport` that lists individual `Change`s.
Callers either print them (dev) or JSON-serialise them (CI). The single
`Change.kind` enum makes the "did / already present" contract (B.4) uniform.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from loguru import logger


class ChangeKind(str, enum.Enum):
    did = "did"
    already = "already-present"
    skipped = "skipped"


@dataclass(frozen=True, slots=True)
class Change:
    resource: str
    kind: ChangeKind
    detail: str | None = None


@dataclass(slots=True)
class BootstrapReport:
    subcommand: str
    changes: list[Change] = field(default_factory=list)

    def did(self, resource: str, detail: str | None = None) -> None:
        self.changes.append(Change(resource, ChangeKind.did, detail))

    def already(self, resource: str, detail: str | None = None) -> None:
        self.changes.append(Change(resource, ChangeKind.already, detail))

    def skipped(self, resource: str, detail: str | None = None) -> None:
        self.changes.append(Change(resource, ChangeKind.skipped, detail))

    def print_human(self) -> None:
        logger.info(
            "bootstrap subcommand completed",
            actor="bootstrap-cli",
            subcommand=self.subcommand,
            changes=[{"resource": c.resource, "kind": c.kind.value} for c in self.changes],
        )
        print(f"# {self.subcommand}")
        for c in self.changes:
            tail = f" — {c.detail}" if c.detail else ""
            print(f"  [{c.kind.value:<15}] {c.resource}{tail}")
