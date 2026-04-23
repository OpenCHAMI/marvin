"""Data models for OpenCHAMI coding agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from .constants import (
    AGENT_NAME,
    AGENT_PERSONA_INSTRUCTION,
    DEFAULT_EXEC_PROGRESS_JSON,
    DEFAULT_EXPLORE_HANDOFF_JSON,
    DEFAULT_OPERATOR_FEEDBACK_MD,
    DEFAULT_PLAN_JSON,
    DEFAULT_PARTIAL_SUCCESS_JSON,
    DEFAULT_PROPOSAL_MD,
    DEFAULT_RECOMMENDED_CONFIG_YAML,
    DEFAULT_RECOMMENDED_OPERATOR_FEEDBACK_MD,
    DEFAULT_SUMMARY_JSON,
    DEFAULT_VERIFICATION_JSON,
    DEFAULT_VERIFICATION_MD,
    DEFAULT_WORKSPACE_ANALYSIS_JSON,
    DEFAULT_WORKSPACE_ANALYSIS_MD,
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
    brief: str = ""
    checks: list[str] = field(default_factory=list)
    read_only: bool = False

    @property
    def execution_enabled(self) -> bool:
        return not self.read_only

    @property
    def role_label(self) -> str:
        return "reference-only" if self.read_only else "execution"


@dataclass(frozen=True)
class PlanStep:
    name: str
    description: str = ""
    expected_outputs: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    requires_code: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "expected_outputs": list(self.expected_outputs),
            "success_criteria": list(self.success_criteria),
            "requires_code": self.requires_code,
        }


@dataclass(frozen=True)
class StructuredPlan:
    steps: list[PlanStep] = field(default_factory=list)
    source: str = "unknown"

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "steps": [step.to_payload() for step in self.steps],
        }


@dataclass(frozen=True)
class RunTraceEvent:
    stage: str
    event_type: str
    status: str = "info"
    title: str = ""
    detail: str = ""
    main_step: int | None = None
    total_main_steps: int | None = None
    sub_step: int | None = None
    total_sub_steps: int | None = None
    repo: str | None = None
    affected_repos: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "event_type": self.event_type,
            "status": self.status,
            "title": self.title,
            "detail": self.detail,
            "affected_repos": list(self.affected_repos),
            "token_usage": {key: int(value) for key, value in self.token_usage.items()},
            "metadata": dict(self.metadata),
        }
        if self.main_step is not None:
            payload["main_step"] = self.main_step
        if self.total_main_steps is not None:
            payload["total_main_steps"] = self.total_main_steps
        if self.sub_step is not None:
            payload["sub_step"] = self.sub_step
        if self.total_sub_steps is not None:
            payload["total_sub_steps"] = self.total_sub_steps
        if self.repo is not None:
            payload["repo"] = self.repo
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> RunTraceEvent:
        source = payload if isinstance(payload, dict) else {}

        def optional_int(key: str) -> int | None:
            value = source.get(key)
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        raw_token_usage = source.get("token_usage")
        token_usage = {
            str(key): int(value or 0)
            for key, value in (raw_token_usage.items() if isinstance(raw_token_usage, dict) else [])
        }

        return cls(
            stage=str(source.get("stage") or "unknown"),
            event_type=str(source.get("event_type") or "unknown"),
            status=str(source.get("status") or "info"),
            title=str(source.get("title") or ""),
            detail=str(source.get("detail") or ""),
            main_step=optional_int("main_step"),
            total_main_steps=optional_int("total_main_steps"),
            sub_step=optional_int("sub_step"),
            total_sub_steps=optional_int("total_sub_steps"),
            repo=(str(source.get("repo")) if source.get("repo") is not None else None),
            affected_repos=[str(value) for value in (source.get("affected_repos") or [])],
            token_usage=token_usage,
            metadata=dict(source.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RunTrace:
    planning_mode: str = "single"
    events: list[RunTraceEvent] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "planning_mode": self.planning_mode,
            "events": [event.to_payload() for event in self.events],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> RunTrace:
        source = payload if isinstance(payload, dict) else {}
        raw_events = source.get("events")
        events = (
            [RunTraceEvent.from_payload(event) for event in raw_events]
            if isinstance(raw_events, list)
            else []
        )
        return cls(
            planning_mode=str(source.get("planning_mode") or "single"),
            events=events,
        )


WorkflowPhase = Literal["explore", "plan", "execute", "verify", "summarize"]
VerificationVerdict = Literal["PASS", "FAIL", "PARTIAL"]


class VerificationBundle(TypedDict):
    workspace_path: str
    repo_states: dict[str, str]
    plan_path: str | None
    execution_summary_path: str | None
    changed_files: list[str]
    diff_path: str | None
    run_trace_path: str | None
    repo_profile_paths: list[str]
    artifact_dir: str


class VerifierResult(TypedDict):
    name: str
    verdict: VerificationVerdict
    required: bool
    tier: int
    scope: str
    evidence: list[str]
    findings: list[str]
    artifacts: list[str]
    rerun_recommended: bool


@dataclass
class AgentConfig:
    project: str
    problem: str
    mode: str = "plan_and_execute"
    planning_mode: str = "single"
    config_path: Path | None = None
    raw_config: dict[str, Any] = field(default_factory=dict)
    workspace: Path | None = None
    workspace_reused: bool = False
    proposal_markdown: str = DEFAULT_PROPOSAL_MD
    plan_json: str = DEFAULT_PLAN_JSON
    summary_json: str = DEFAULT_SUMMARY_JSON
    explore_handoff_json: str = DEFAULT_EXPLORE_HANDOFF_JSON
    partial_success_json: str = DEFAULT_PARTIAL_SUCCESS_JSON
    operator_feedback_markdown: str = DEFAULT_OPERATOR_FEEDBACK_MD
    workspace_analysis_markdown: str = DEFAULT_WORKSPACE_ANALYSIS_MD
    workspace_analysis_json: str = DEFAULT_WORKSPACE_ANALYSIS_JSON
    recommended_config_yaml: str = DEFAULT_RECOMMENDED_CONFIG_YAML
    recommended_operator_feedback_markdown: str = DEFAULT_RECOMMENDED_OPERATOR_FEEDBACK_MD
    proposal_only: bool = False
    execute_after_plan: bool = True
    repos: list[RepoSpec] = field(default_factory=list)
    planner_model: str | None = None
    executor_model: str | None = None
    agent_name: str = AGENT_NAME
    persona_instruction: str = AGENT_PERSONA_INSTRUCTION
    prompt_appendix: str = ""
    planner_prompt_appendix: str = ""
    executor_prompt_appendix: str = ""
    repair_prompt_appendix: str = ""
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
    verification_json: str = DEFAULT_VERIFICATION_JSON
    verification_markdown: str = DEFAULT_VERIFICATION_MD
    repo_dependencies: dict[str, list[str]] = field(default_factory=dict)
    repo_order: list[str] = field(default_factory=list)
    repo_profiles_dir: str = "repo_profiles"
    enabled_verifiers: list[str] = field(default_factory=list)
    disabled_verifiers: list[str] = field(default_factory=list)
    enabled_verifier_tiers: list[int] = field(default_factory=lambda: [1, 2])
    verbose_io: bool = False
    commit_each_step: bool = True
    allow_user_prompts: bool = True

    @property
    def execution_repos(self) -> list[RepoSpec]:
        return [repo for repo in self.repos if repo.execution_enabled]

    @property
    def reference_repos(self) -> list[RepoSpec]:
        return [repo for repo in self.repos if repo.read_only]

    @staticmethod
    def _int_at_least(value: Any, default: int, minimum: int) -> int:
        try:
            return max(minimum, int(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _text_or_default(value: Any, default: str) -> str:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        return default

    @staticmethod
    def _optional_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        return ""

    @staticmethod
    def _int_list(value: Any, default: list[int]) -> list[int]:
        if not isinstance(value, list):
            return list(default)
        parsed: list[int] = []
        for item in value:
            try:
                parsed.append(int(item))
            except (TypeError, ValueError):
                continue
        return parsed or list(default)

    @classmethod
    def from_raw(
        cls,
        raw: dict[str, Any],
        *,
        workspace: Path | None,
        workspace_reused: bool,
        repos: list[RepoSpec],
    ) -> AgentConfig:
        outputs = dict(raw.get("outputs", {}) or {})
        task = dict(raw.get("task", {}) or {})
        models = dict(raw.get("models", {}) or {})
        agent = dict(raw.get("agent", {}) or {})
        execution = dict(raw.get("execution", {}) or {})
        planning = dict(raw.get("planning", {}) or {})

        return cls(
            project=raw["project"],
            problem=raw["problem"],
            mode=raw.get("mode", "plan_and_execute"),
            planning_mode=str(planning.get("mode") or raw.get("planning_mode") or "single"),
            workspace=workspace,
            workspace_reused=workspace_reused,
            proposal_markdown=outputs.get("proposal_markdown", DEFAULT_PROPOSAL_MD),
            plan_json=outputs.get("plan_json", DEFAULT_PLAN_JSON),
            summary_json=outputs.get("summary_json", DEFAULT_SUMMARY_JSON),
            explore_handoff_json=outputs.get(
                "explore_handoff_json", DEFAULT_EXPLORE_HANDOFF_JSON
            ),
            partial_success_json=outputs.get(
                "partial_success_json", DEFAULT_PARTIAL_SUCCESS_JSON
            ),
            operator_feedback_markdown=outputs.get(
                "operator_feedback_markdown", DEFAULT_OPERATOR_FEEDBACK_MD
            ),
            workspace_analysis_markdown=outputs.get(
                "workspace_analysis_markdown", DEFAULT_WORKSPACE_ANALYSIS_MD
            ),
            workspace_analysis_json=outputs.get(
                "workspace_analysis_json", DEFAULT_WORKSPACE_ANALYSIS_JSON
            ),
            recommended_config_yaml=outputs.get(
                "recommended_config_yaml", DEFAULT_RECOMMENDED_CONFIG_YAML
            ),
            recommended_operator_feedback_markdown=outputs.get(
                "recommended_operator_feedback_markdown",
                DEFAULT_RECOMMENDED_OPERATOR_FEEDBACK_MD,
            ),
            proposal_only=bool(task.get("proposal_only", False)),
            execute_after_plan=bool(task.get("execute_after_plan", True)),
            repos=repos,
            planner_model=models.get("planner") or models.get("default"),
            executor_model=models.get("executor") or models.get("default"),
            agent_name=cls._text_or_default(agent.get("name"), AGENT_NAME),
            persona_instruction=cls._text_or_default(
                agent.get("persona_instruction"), AGENT_PERSONA_INSTRUCTION
            ),
            prompt_appendix=cls._optional_text(agent.get("prompt_appendix")),
            planner_prompt_appendix=cls._optional_text(agent.get("planner_prompt_appendix")),
            executor_prompt_appendix=cls._optional_text(
                agent.get("executor_prompt_appendix")
            ),
            repair_prompt_appendix=cls._optional_text(agent.get("repair_prompt_appendix")),
            defaults=raw.get("defaults", {}),
            planner=raw.get("planner", {}),
            execution=execution,
            notes=list(task.get("notes") or []),
            deliverables=list(task.get("deliverables") or []),
            plan_requirements=list(task.get("plan_requirements") or []),
            execution_requirements=list(task.get("execution_requirements") or []),
            max_parallel_checks=cls._int_at_least(
                execution.get("max_parallel_checks", 4), default=4, minimum=1
            ),
            max_check_retries=cls._int_at_least(
                execution.get("max_check_retries", 1), default=1, minimum=0
            ),
            skip_failed_repos=bool(execution.get("skip_failed_repos", False)),
            check_command_timeout_sec=cls._int_at_least(
                execution.get("check_command_timeout_sec", 900), default=900, minimum=1
            ),
            check_output_tail_chars=cls._int_at_least(
                execution.get("check_output_tail_chars", 12000), default=12000, minimum=1000
            ),
            resume_execution_state=bool(execution.get("resume_execution_state", True)),
            confirm_before_execute=bool(task.get("confirm_before_execute", False)),
            confirm_timeout_sec=cls._int_at_least(
                task.get("confirm_timeout_sec", 30), default=30, minimum=1
            ),
            resume_from=execution.get("resume_from"),
            executor_progress_json=outputs.get(
                "executor_progress_json", DEFAULT_EXEC_PROGRESS_JSON
            ),
            verification_json=outputs.get(
                "verification_json", DEFAULT_VERIFICATION_JSON
            ),
            verification_markdown=outputs.get(
                "verification_markdown", DEFAULT_VERIFICATION_MD
            ),
            repo_dependencies={
                str(key): [str(value) for value in (values or [])]
                for key, values in dict(execution.get("repo_dependencies", {})).items()
            },
            repo_order=[str(value) for value in (execution.get("repo_order", []) or [])],
            repo_profiles_dir=str(execution.get("repo_profiles_dir") or "repo_profiles"),
            enabled_verifiers=[
                str(value)
                for value in (execution.get("enabled_verifiers", []) or [])
                if str(value).strip()
            ],
            disabled_verifiers=[
                str(value)
                for value in (execution.get("disabled_verifiers", []) or [])
                if str(value).strip()
            ],
            enabled_verifier_tiers=[
                value
                for value in cls._int_list(execution.get("enabled_verifier_tiers"), [1, 2])
            ],
            verbose_io=bool(execution.get("verbose_io", False)),
            commit_each_step=bool(execution.get("commit_each_step", True)),
        )


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
    raw_response: Any | None = None


@dataclass
class ProgressSnapshot:
    stage: str
    detail: str
    base_detail: str = ""
    agent_feedback: str = ""
    workspace: str | None = None
    planning_mode: str = "single"
    current_main_step: int | None = None
    current_main_total: int = 0
    current_sub_step: int | None = None
    current_sub_total: int = 0
    current_repo: str | None = None
    checkpoint_label: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    completed_repos: int = 0
    total_repos: int = 0
    failed_repos: int = 0
    retries: int = 0
    elapsed_sec: float | None = None
