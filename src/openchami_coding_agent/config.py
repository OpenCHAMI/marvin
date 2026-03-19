"""Configuration parsing and repository/workspace resolution."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .constants import (
    AGENT_NAME,
    DEFAULT_CONTEXT_CLAIM,
    DEFAULT_EXEC_PROGRESS_JSON,
    DEFAULT_PLAN_JSON,
    DEFAULT_PROPOSAL_MD,
    DEFAULT_SUMMARY_JSON,
    DEFAULT_WORKSPACE_ROOT,
)
from .models import AgentConfig, RepoSpec
from .reporting import emit_panel, emit_table
from .utils import run_command, slugify, to_plain_data


def resolve_workspace(
    raw: dict[str, Any],
    cli_workspace: str | None = None,
    resume: bool = False,
) -> tuple[Path, bool]:
    from ursa.util.plan_execute_utils import generate_workspace_name

    requested = cli_workspace or raw.get("workspace") or raw.get("restart_workspace")
    workspace_root = raw.get("workspace_root") or DEFAULT_WORKSPACE_ROOT
    run_cwd = Path.cwd().resolve()

    def ensure_within_run_cwd(path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(run_cwd)
        except ValueError as exc:
            raise ValueError(
                f"Workspace must be inside launch directory {run_cwd}; got {resolved}"
            ) from exc
        return resolved

    if requested:
        ws = Path(requested)
        if not ws.is_absolute():
            ws = (run_cwd / ws).resolve()
        ws = ensure_within_run_cwd(ws)
        reused = ws.exists()
        if resume and not reused:
            raise FileNotFoundError(f"Requested existing workspace does not exist: {ws}")
        ws.mkdir(parents=True, exist_ok=True)
        return ws, reused

    root = Path(workspace_root)
    if not root.is_absolute():
        root = (run_cwd / root).resolve()
    root = ensure_within_run_cwd(root)
    root.mkdir(parents=True, exist_ok=True)

    while True:
        project_slug = slugify(raw.get("project", "openchami-task"))
        candidate = root / generate_workspace_name(project_slug)
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate, False


def ensure_within_workspace(path: Path, workspace: Path) -> Path:
    resolved = path.resolve()
    workspace_resolved = workspace.resolve()
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Path escapes workspace containment: {resolved} is outside {workspace_resolved}"
        ) from exc
    return resolved


def resolve_repo(workspace: Path, raw_repo: dict[str, Any]) -> RepoSpec:
    if "name" not in raw_repo:
        raise ValueError("Each repo entry must include 'name'.")
    launch_cwd = Path.cwd().resolve()
    source_path: Path | None = None

    if raw_repo.get("path"):
        requested_path = Path(raw_repo["path"])
        resolved_requested = (
            requested_path if requested_path.is_absolute() else (launch_cwd / requested_path)
        )
        resolved_requested = resolved_requested.resolve()

        try:
            path = ensure_within_workspace(resolved_requested, workspace)
        except ValueError:
            source_path = resolved_requested
            path = ensure_within_workspace((workspace / "repos" / raw_repo["name"]), workspace)
    else:
        path = ensure_within_workspace((workspace / "repos" / raw_repo["name"]), workspace)

    return RepoSpec(
        name=raw_repo["name"],
        path=path,
        source_path=source_path,
        url=raw_repo.get("url"),
        branch=raw_repo.get("branch"),
        checkout=bool(raw_repo.get("checkout", False)),
        language=raw_repo.get("language", "generic"),
        description=raw_repo.get("description", ""),
        checks=list(raw_repo.get("checks") or []),
    )


def parse_config(
    config_path: Path, cli_workspace: str | None = None, resume: bool = False
) -> AgentConfig:
    from ursa.util.plan_execute_utils import load_yaml_config, setup_workspace

    raw = to_plain_data(load_yaml_config(str(config_path)))
    workspace, workspace_reused = resolve_workspace(raw, cli_workspace=cli_workspace, resume=resume)
    setup_workspace(str(workspace))

    repos = [resolve_repo(workspace, r) for r in raw.get("repos", [])]
    models = raw.get("models", {})

    return AgentConfig(
        project=raw["project"],
        problem=raw["problem"],
        mode=raw.get("mode", "plan_and_execute"),
        workspace=workspace,
        workspace_reused=workspace_reused,
        proposal_markdown=raw.get("outputs", {}).get("proposal_markdown", DEFAULT_PROPOSAL_MD),
        plan_json=raw.get("outputs", {}).get("plan_json", DEFAULT_PLAN_JSON),
        summary_json=raw.get("outputs", {}).get("summary_json", DEFAULT_SUMMARY_JSON),
        context_claim_name=raw.get("task", {}).get("context_claim_name", DEFAULT_CONTEXT_CLAIM),
        proposal_only=bool(raw.get("task", {}).get("proposal_only", False)),
        execute_after_plan=bool(raw.get("task", {}).get("execute_after_plan", True)),
        repos=repos,
        planner_model=models.get("planner") or models.get("default"),
        executor_model=models.get("executor") or models.get("default"),
        defaults=raw.get("defaults", {}),
        planner=raw.get("planner", {}),
        execution=raw.get("execution", {}),
        notes=list(raw.get("task", {}).get("notes") or []),
        deliverables=list(raw.get("task", {}).get("deliverables") or []),
        plan_requirements=list(raw.get("task", {}).get("plan_requirements") or []),
        execution_requirements=list(raw.get("task", {}).get("execution_requirements") or []),
        max_parallel_checks=max(1, int(raw.get("execution", {}).get("max_parallel_checks", 4))),
        max_check_retries=max(0, int(raw.get("execution", {}).get("max_check_retries", 1))),
        skip_failed_repos=bool(raw.get("execution", {}).get("skip_failed_repos", False)),
        check_command_timeout_sec=max(
            1, int(raw.get("execution", {}).get("check_command_timeout_sec", 900))
        ),
        check_output_tail_chars=max(
            1000, int(raw.get("execution", {}).get("check_output_tail_chars", 12000))
        ),
        resume_execution_state=bool(raw.get("execution", {}).get("resume_execution_state", True)),
        confirm_before_execute=bool(raw.get("task", {}).get("confirm_before_execute", False)),
        confirm_timeout_sec=max(1, int(raw.get("task", {}).get("confirm_timeout_sec", 30))),
        resume_from=raw.get("execution", {}).get("resume_from"),
        executor_progress_json=raw.get("outputs", {}).get(
            "executor_progress_json", DEFAULT_EXEC_PROGRESS_JSON
        ),
        repo_dependencies={
            str(k): [str(v) for v in (vals or [])]
            for k, vals in dict(raw.get("execution", {}).get("repo_dependencies", {})).items()
        },
        repo_order=[str(x) for x in (raw.get("execution", {}).get("repo_order", []) or [])],
        verbose_io=bool(raw.get("execution", {}).get("verbose_io", False)),
    )


def ensure_repo(repo: RepoSpec) -> None:
    repo.path.parent.mkdir(parents=True, exist_ok=True)

    if not repo.path.exists() and repo.source_path is not None:
        if not repo.source_path.exists():
            raise FileNotFoundError(
                f"Source path for repo '{repo.name}' does not exist: {repo.source_path}"
            )
        if (repo.source_path / ".git").exists():
            code, out, err = run_command(["git", "clone", str(repo.source_path), str(repo.path)])
            if code != 0:
                shutil.copytree(repo.source_path, repo.path)
                clone_error = err.strip() or out.strip()
                emit_panel(
                    (
                        f"{repo.name}: git clone from local source failed, "
                        f"copied files instead.\n{clone_error}"
                    ),
                    border_style="yellow",
                )
        else:
            shutil.copytree(repo.source_path, repo.path)

    if not repo.checkout:
        if not repo.path.exists():
            raise RuntimeError(
                f"Repo '{repo.name}' is not available in workspace and "
                "checkout=false with no materialized source. "
                "Provide repos[].path, set checkout=true with repos[].url, "
                "or create the repo in the workspace first."
            )
        return

    if not repo.path.exists():
        if not repo.url:
            raise RuntimeError(
                f"Repo '{repo.name}' does not exist locally and no URL was provided."
            )
        cmd = ["git", "clone"]
        if repo.branch:
            cmd.extend(["--branch", repo.branch])
        cmd.extend([repo.url, str(repo.path)])
        code, out, err = run_command(cmd)
        if code != 0:
            raise RuntimeError(f"git clone failed for {repo.name}\n{out}\n{err}")
        return

    if repo.branch:
        code, current, _ = run_command(
            ["git", "-C", str(repo.path), "rev-parse", "--abbrev-ref", "HEAD"]
        )
        if code == 0 and current.strip() == repo.branch:
            return
        code, _, err = run_command(["git", "-C", str(repo.path), "checkout", repo.branch])
        if code != 0:
            emit_panel(
                (
                    f"{repo.name}: could not checkout {repo.branch}; "
                    f"staying on current branch.\n{err.strip()}"
                ),
                border_style="yellow",
            )


def repo_listing(repos: list[RepoSpec]) -> str:
    lines: list[str] = []
    for repo in repos:
        bits = [f"- {repo.name}: {repo.path}"]
        if repo.branch:
            bits.append(f"branch={repo.branch}")
        if repo.language:
            bits.append(f"language={repo.language}")
        if repo.description:
            bits.append(repo.description)
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def default_working_directory(cfg: AgentConfig) -> Path | None:
    if not cfg.repos:
        return cfg.workspace
    if len(cfg.repos) == 1:
        return cfg.repos[0].path
    return cfg.workspace


def render_status(cfg: AgentConfig) -> None:
    from rich.table import Table
    from ursa.util.plan_execute_utils import sanitize_for_logging

    table = Table(title=f"{AGENT_NAME} coding agent", show_header=True)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Project", cfg.project)
    table.add_row("Mode", cfg.mode)
    table.add_row("Workspace", str(cfg.workspace))
    table.add_row("Workspace reused", "yes" if cfg.workspace_reused else "no")
    table.add_row("Context claim", cfg.context_claim_name)
    table.add_row("Proposal", cfg.proposal_markdown)
    table.add_row("Plan JSON", cfg.plan_json)
    table.add_row("Summary JSON", cfg.summary_json)
    table.add_row("Confirm before execute", "yes" if cfg.confirm_before_execute else "no")
    table.add_row("Check parallelism", str(cfg.max_parallel_checks))
    table.add_row("Check retries", str(cfg.max_check_retries))
    table.add_row("Skip failed repos", "yes" if cfg.skip_failed_repos else "no")
    table.add_row("Resume execution state", "yes" if cfg.resume_execution_state else "no")
    table.add_row("Verbose IO", "yes" if cfg.verbose_io else "no")
    emit_table(table)

    safe_execution = sanitize_for_logging(cfg.execution)
    if safe_execution:
        emit_panel(str(safe_execution), border_style="blue", title="Execution config")
