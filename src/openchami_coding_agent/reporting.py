"""Progress/reporting abstractions for terminal and TUI output."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .constants import AGENT_NAME
from .models import ProgressSnapshot
from .progress_view import build_progress_display, progress_snapshot_key

console = Console(file=sys.__stderr__)


class ProgressReporter:
    def emit_panel(
        self, message: str, border_style: str = "blue", title: str | None = None
    ) -> None:
        raise NotImplementedError

    def emit_text(self, message: str) -> None:
        raise NotImplementedError

    def emit_table(self, table: Table) -> None:
        raise NotImplementedError

    def emit_progress(self, snapshot: ProgressSnapshot) -> None:
        raise NotImplementedError

    def emit_check_status(self, status: dict[str, str], retries: dict[str, int]) -> None:
        raise NotImplementedError


class RichProgressReporter(ProgressReporter):
    def __init__(self) -> None:
        self._last_progress_key: tuple[Any, ...] | None = None
        self._last_check_key: tuple[Any, ...] | None = None

    def emit_panel(
        self, message: str, border_style: str = "blue", title: str | None = None
    ) -> None:
        console.print(Panel(message, border_style=border_style, title=title, expand=False))

    def emit_text(self, message: str) -> None:
        console.print(message)

    def emit_table(self, table: Table) -> None:
        console.print(table)

    def emit_progress(self, snapshot: ProgressSnapshot) -> None:
        progress_key = progress_snapshot_key(snapshot)
        if progress_key == self._last_progress_key:
            return
        self._last_progress_key = progress_key
        display = build_progress_display(snapshot)

        table = Table(title=f"{AGENT_NAME} run progress", show_header=True)
        table.add_column("Workspace")
        table.add_column("Stage")
        table.add_column("Detail")
        table.add_column("Repo Progress")
        table.add_column("Failures")
        table.add_column("Retries")
        table.add_column("Tokens")
        table.add_column("Elapsed")
        table.add_row(
            display.workspace,
            display.stage_label,
            display.detail,
            display.repo_progress,
            display.failures,
            display.retries,
            display.tokens,
            display.elapsed,
        )
        console.print(table)

    def emit_check_status(self, status: dict[str, str], retries: dict[str, int]) -> None:
        check_key = (
            tuple(sorted(status.items())),
            tuple(sorted(retries.items())),
        )
        if check_key == self._last_check_key:
            return
        self._last_check_key = check_key

        table = Table(title="Repository check status", show_header=True)
        table.add_column("Repo")
        table.add_column("Status")
        table.add_column("Retries")
        for repo_name in sorted(status):
            table.add_row(repo_name, status[repo_name], str(retries.get(repo_name, 0)))
        console.print(table)


_REPORTER: ProgressReporter = RichProgressReporter()
_WORKSPACE_NAME: str | None = None


def set_reporter(reporter: ProgressReporter) -> None:
    global _REPORTER
    _REPORTER = reporter


def get_reporter() -> ProgressReporter:
    return _REPORTER


def set_workspace_name(name: str | None) -> None:
    global _WORKSPACE_NAME
    _WORKSPACE_NAME = name


def emit_panel(message: str, border_style: str = "blue", title: str | None = None) -> None:
    _REPORTER.emit_panel(message, border_style=border_style, title=title)


def emit_text(message: str) -> None:
    _REPORTER.emit_text(message)


def emit_table(table: Table) -> None:
    _REPORTER.emit_table(table)


def render_run_progress(
    *,
    stage: str,
    detail: str,
    token_usage: dict[str, int] | None = None,
    completed_repos: int = 0,
    total_repos: int = 0,
    failed_repos: int = 0,
    retries: int = 0,
    elapsed_sec: float | None = None,
) -> None:
    snapshot = ProgressSnapshot(
        stage=stage,
        detail=detail,
        workspace=_WORKSPACE_NAME,
        token_usage=token_usage or {},
        completed_repos=completed_repos,
        total_repos=total_repos,
        failed_repos=failed_repos,
        retries=retries,
        elapsed_sec=elapsed_sec,
    )
    _REPORTER.emit_progress(snapshot)


def render_check_status(status: dict[str, str], retries: dict[str, int]) -> None:
    _REPORTER.emit_check_status(status, retries)


@contextmanager
def progress_heartbeat(
    *,
    stage: str,
    detail: str,
    detail_provider: Callable[[], str] | None = None,
    token_usage_provider: Callable[[], dict[str, int] | dict[str, Any]] | None = None,
    completed_repos_provider: Callable[[], int] | None = None,
    total_repos_provider: Callable[[], int] | None = None,
    failed_repos_provider: Callable[[], int] | None = None,
    retries_provider: Callable[[], int] | None = None,
    start_time: float | None = None,
    interval_sec: float = 8.0,
):
    started = start_time if start_time is not None else time.time()
    stopped = threading.Event()

    def _value(provider: Callable[[], int] | None, default: int = 0) -> int:
        if provider is None:
            return default
        try:
            return int(provider())
        except Exception:
            return default

    def _tokens() -> dict[str, int]:
        if token_usage_provider is None:
            return {}
        try:
            token_data = token_usage_provider() or {}
        except Exception:
            return {}
        return {
            "input_tokens": int(token_data.get("input_tokens", 0) or 0),
            "output_tokens": int(token_data.get("output_tokens", 0) or 0),
            "total_tokens": int(token_data.get("total_tokens", 0) or 0),
        }

    def _emit() -> None:
        current_detail = detail
        if detail_provider is not None:
            try:
                provided = detail_provider().strip()
                if provided:
                    current_detail = provided
            except Exception:
                current_detail = detail
        render_run_progress(
            stage=stage,
            detail=current_detail,
            token_usage=_tokens(),
            completed_repos=_value(completed_repos_provider),
            total_repos=_value(total_repos_provider),
            failed_repos=_value(failed_repos_provider),
            retries=_value(retries_provider),
            elapsed_sec=time.time() - started,
        )

    def _loop() -> None:
        while not stopped.wait(interval_sec):
            _emit()

    _emit()
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=0.5)
