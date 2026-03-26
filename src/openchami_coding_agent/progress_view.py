"""Shared progress-view helpers for CLI and TUI renderers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ProgressSnapshot
from .utils import format_elapsed_runtime, format_token_counts

STAGE_LABELS: dict[str, str] = {
    "planning": "Planning",
    "execution": "Executing",
    "validation": "Validating",
    "repair": "Repairing",
    "complete": "Complete",
    "failed": "Failed",
}

REPO_STATUS_LABELS: dict[str, str] = {
    "pending": "waiting",
    "checking": "checking",
    "failed": "failed",
    "passed": "passed",
    "completed": "already complete",
}


@dataclass(frozen=True)
class ProgressDisplay:
    workspace: str
    stage: str
    stage_label: str
    detail: str
    repo_progress: str
    failures: str
    retries: str
    tokens: str
    elapsed: str


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


def repo_status_label(status: str) -> str:
    return REPO_STATUS_LABELS.get(status, status)


def progress_snapshot_key(snapshot: ProgressSnapshot) -> tuple[Any, ...]:
    return (
        snapshot.workspace or "-",
        snapshot.stage,
        snapshot.detail,
        snapshot.completed_repos,
        snapshot.total_repos,
        snapshot.failed_repos,
        snapshot.retries,
        int(snapshot.token_usage.get("input_tokens", 0)),
        int(snapshot.token_usage.get("output_tokens", 0)),
        int(snapshot.token_usage.get("total_tokens", 0)),
        round(snapshot.elapsed_sec or 0.0, 1),
    )


def build_progress_display(snapshot: ProgressSnapshot) -> ProgressDisplay:
    return ProgressDisplay(
        workspace=snapshot.workspace or "-",
        stage=snapshot.stage,
        stage_label=stage_label(snapshot.stage),
        detail=snapshot.detail,
        repo_progress=f"{snapshot.completed_repos}/{snapshot.total_repos}",
        failures=str(snapshot.failed_repos),
        retries=str(snapshot.retries),
        tokens=format_token_counts(snapshot.token_usage),
        elapsed=format_elapsed_runtime(snapshot.elapsed_sec),
    )
