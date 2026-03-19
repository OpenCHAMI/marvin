"""Data models for OpenCHAMI coding agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_CONTEXT_CLAIM,
    DEFAULT_EXEC_PROGRESS_JSON,
    DEFAULT_PLAN_JSON,
    DEFAULT_PROPOSAL_MD,
    DEFAULT_SUMMARY_JSON,
)


@dataclass
class RepoSpec:
    name: str
    path: Path
    source_path: Path | None = None
    url: str | None = None
    branch: str | None = None
    checkout: bool = False
    language: str = "generic"
    description: str = ""
    checks: list[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    project: str
    problem: str
    mode: str = "plan_and_execute"
    workspace: Path | None = None
    workspace_reused: bool = False
    proposal_markdown: str = DEFAULT_PROPOSAL_MD
    plan_json: str = DEFAULT_PLAN_JSON
    summary_json: str = DEFAULT_SUMMARY_JSON
    context_claim_name: str = DEFAULT_CONTEXT_CLAIM
    proposal_only: bool = False
    execute_after_plan: bool = True
    repos: list[RepoSpec] = field(default_factory=list)
    planner_model: str | None = None
    executor_model: str | None = None
    defaults: dict[str, Any] = field(default_factory=dict)
    planner: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    plan_requirements: list[str] = field(default_factory=list)
    execution_requirements: list[str] = field(default_factory=list)
    max_parallel_checks: int = 4
    max_check_retries: int = 1
    skip_failed_repos: bool = False
    check_command_timeout_sec: int = 900
    check_output_tail_chars: int = 12000
    resume_execution_state: bool = True
    confirm_before_execute: bool = False
    confirm_timeout_sec: int = 30
    resume_from: str | None = None
    executor_progress_json: str = DEFAULT_EXEC_PROGRESS_JSON
    repo_dependencies: dict[str, list[str]] = field(default_factory=dict)
    repo_order: list[str] = field(default_factory=list)
    verbose_io: bool = False


@dataclass
class CheckExecutionResult:
    repo_name: str
    checks_passed: bool
    check_results: list[dict[str, Any]]


@dataclass
class InvocationCapture:
    content: str
    captured_stdout: str = ""
    captured_stderr: str = ""


@dataclass
class ProgressSnapshot:
    stage: str
    detail: str
    workspace: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    completed_repos: int = 0
    total_repos: int = 0
    failed_repos: int = 0
    retries: int = 0
    elapsed_sec: float | None = None
