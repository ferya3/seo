"""SERP-specific view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.db import JobTable, store_for
from shared.store import FileJobStore, JobRecord


@dataclass
class CheckRecord(JobRecord):
    """One rank check. `subject` is the target domain."""

    @property
    def check_id(self) -> str:
        return self.job_id

    @property
    def target_domain(self) -> str:
        return self.subject

    @property
    def report(self) -> dict[str, Any] | None:
        return self.result

    def summary(self) -> dict[str, Any]:
        report = self.result or {}
        return {
            "check_id": self.job_id,
            "target_domain": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "keywords_checked": report.get("keywords_checked"),
            "keywords_ranked": report.get("keywords_ranked"),
            "average_position": report.get("average_position"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CheckRecord:
        return cls(
            job_id=raw.get("job_id") or raw["check_id"],
            subject=raw.get("subject") or raw["target_domain"],
            status=raw.get("status", "completed"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            error=raw.get("error"),
            result=raw.get("result") if "result" in raw else raw.get("report"),
            tenant_id=raw.get("tenant_id"),
            project_id=raw.get("project_id"),
        )


TABLE = JobTable(
    name="serp_checks",
    subject_column="target_domain",
    derived={
        "keywords_checked": lambda report: report.get("keywords_checked"),
    },
)


class CheckStore(FileJobStore[CheckRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, CheckRecord)


def open_store(directory: Path | str, dsn: str | None = None):
    """Postgres when configured, files otherwise. See `shared.db.store_for`."""
    return store_for(TABLE, CheckRecord, directory, dsn)
