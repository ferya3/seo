"""Reporting view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.db import JobTable, store_for
from shared.store import FileJobStore, JobRecord


@dataclass
class DocumentRecord(JobRecord):
    """One rendered report. `subject` is the workflow it describes.

    The rendered text lives in `result` alongside the metadata rather than in
    a column of its own: it is only ever read whole, and a document that could
    not say which workflow and which format produced it would be a file with
    no provenance.
    """

    @property
    def report_id(self) -> str:
        return self.job_id

    @property
    def workflow_id(self) -> str:
        return self.subject

    def document(self, fmt: str) -> str | None:
        return ((self.result or {}).get("documents") or {}).get(fmt)

    def summary(self) -> dict[str, Any]:
        result = self.result or {}
        return {
            "report_id": self.job_id,
            "workflow_id": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "title": result.get("title"),
            "formats": sorted((result.get("documents") or {}).keys()),
            "summary_source": result.get("summary_source"),
        }

    def to_dict(self) -> dict[str, Any]:
        # The documents themselves are not in the listing payload: an HTML
        # report is tens of kilobytes and there is an endpoint for it.
        return {**self.summary(), "bytes": {
            fmt: len(text) for fmt, text in ((self.result or {}).get("documents") or {}).items()
        }}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DocumentRecord:
        return cls(
            job_id=raw.get("job_id") or raw["report_id"],
            subject=raw.get("subject") or raw["workflow_id"],
            status=raw.get("status", "completed"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            error=raw.get("error"),
            result=raw.get("result"),
            tenant_id=raw.get("tenant_id"),
            project_id=raw.get("project_id"),
        )


TABLE = JobTable(
    name="reports",
    subject_column="workflow_id",
    derived={
        "title": lambda report: report.get("title"),
        "summary_source": lambda report: report.get("summary_source"),
    },
)


class DocumentStore(FileJobStore[DocumentRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, DocumentRecord)


def open_store(directory: Path | str, dsn: str | None = None):
    """Postgres when configured, files otherwise. See `shared.db.store_for`."""
    return store_for(TABLE, DocumentRecord, directory, dsn)
