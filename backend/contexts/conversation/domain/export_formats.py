"""What each chat export format produces ([R13.17]).

One table for the worker that writes the archive and the endpoint that names
its download, so a format cannot be added to one and not the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    object_name: str
    content_type: str
    extension: str


EXPORT_ARTIFACTS: Final[dict[str, ExportArtifact]] = {
    "markdown": ExportArtifact("export.md", "text/markdown; charset=utf-8", "md"),
    "json": ExportArtifact("manifest.json", "application/json", "json"),
    "pdf": ExportArtifact("export.pdf", "application/pdf", "pdf"),
}

# With attachments the download is a ZIP holding the transcript (named as
# above), an `attachments/` folder and a README.
ARCHIVE_ARTIFACT: Final = ExportArtifact("export.zip", "application/zip", "zip")


def export_artifact(export_format: str, *, include_attachments: bool) -> ExportArtifact:
    return ARCHIVE_ARTIFACT if include_attachments else EXPORT_ARTIFACTS[export_format]


__all__ = ["ARCHIVE_ARTIFACT", "EXPORT_ARTIFACTS", "ExportArtifact", "export_artifact"]
