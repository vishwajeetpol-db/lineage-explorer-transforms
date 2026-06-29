"""Structured reporting for daily extraction (PRD acceptance: daily report)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DailyExtractionReport:
    execution_ts: datetime
    runs_discovered: int = 0
    runs_attempted: int = 0
    artifacts_extracted: int = 0
    artifacts_skipped: int = 0
    by_source_kind: dict[str, int] = field(default_factory=dict)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_ts": self.execution_ts.isoformat(),
            "runs_discovered": self.runs_discovered,
            "runs_attempted": self.runs_attempted,
            "artifacts_extracted": self.artifacts_extracted,
            "artifacts_skipped": self.artifacts_skipped,
            "by_source_kind": dict(self.by_source_kind),
            "skip_reasons": dict(self.skip_reasons),
            "errors": list(self.errors),
            "timings": dict(self.timings),
        }


def merge_skip_reason(report: DailyExtractionReport, reason: str) -> None:
    report.artifacts_skipped += 1
    report.skip_reasons[reason] = report.skip_reasons.get(reason, 0) + 1
