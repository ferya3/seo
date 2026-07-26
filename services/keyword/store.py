"""Keyword-specific view over the shared job store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.store import FileJobStore, JobRecord


@dataclass
class ResearchRecord(JobRecord):
    """One research run. `subject` is the seed term."""

    def summary(self) -> dict[str, Any]:
        report = self.result or {}
        return {
            "research_id": self.job_id,
            "seed": self.subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "total": report.get("total"),
            "cluster_count": len(report.get("clusters", [])) if report else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "report": self.result}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ResearchRecord:
        return cls(
            job_id=raw.get("job_id") or raw["research_id"],
            subject=raw.get("subject") or raw["seed"],
            status=raw.get("status", "completed"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            error=raw.get("error"),
            result=raw.get("result") if "result" in raw else raw.get("report"),
        )


class ResearchStore(FileJobStore[ResearchRecord]):
    def __init__(self, directory: Path | str):
        super().__init__(directory, ResearchRecord)
